import asyncio
import json
import os
import re
import secrets
import time
from datetime import datetime
from functools import wraps
from pathlib import Path
from threading import Thread

import requests
from bson import ObjectId
from flask import Flask, jsonify, redirect, render_template_string, request, session
from flask_cors import CORS
from pymongo import MongoClient

from utils.audio_manager import (
    DEFAULT_ARTWORK,
    cleanup_path,
    cleanup_tree,
    convert_to_96k_mp3,
    extract_from_url,
    upload_to_cdn,
)

app = Flask("")
app.secret_key = os.getenv("PASSWORD") or secrets.token_hex(16)
CORS(app)

MONGO_URI = os.getenv("MONGO_URI")
PASSWORD = os.getenv("PASSWORD")
SPACE_PASSWORD = os.getenv("SPACE_PASSWORD")
RENDER_PUBLIC_URL = "https://deepdey.onrender.com"

mongo_client = MongoClient(MONGO_URI) if MONGO_URI else None
music_col = mongo_client["LeaderboardBotDB"]["MusicTracks"] if mongo_client else None
UPLOAD_ID_PATTERN = r"^[a-fA-F0-9-]{8,64}$"
MAX_CHUNKS = 4096
CHUNK_SIZE_BYTES = 10 * 1024 * 1024
_UPLOAD_SESSION_KEYS: dict[str, str] = {}


def _run_async(coro):
    return asyncio.run(coro)


def _require_music_auth(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not PASSWORD:
            return jsonify({"error": "PASSWORD env var is not configured"}), 500
        if not session.get("music_auth"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect("/music/login")
        return func(*args, **kwargs)

    return wrapper


@app.route("/")
def home():
    return "Bot is awake!"


@app.route("/api/stats")
def stats():
    try:
        with open("stats.json", "r") as f:
            data = json.load(f)
            return jsonify(data)
    except Exception:
        return jsonify({"servers": "Loading...", "ping": "..."})


@app.route("/music/login", methods=["GET", "POST"])
def music_login():
    if request.method == "POST":
        submitted = request.form.get("password", "")
        if PASSWORD and secrets.compare_digest(submitted, PASSWORD):
            session["music_auth"] = True
            return redirect("/music")
        return render_template_string(_LOGIN_HTML, error="Invalid password")
    return render_template_string(_LOGIN_HTML, error=None)


@app.route("/music/logout")
def music_logout():
    session.pop("music_auth", None)
    return redirect("/music/login")


@app.route("/music")
@_require_music_auth
def music_dashboard():
    return render_template_string(_MUSIC_HTML)


@app.route("/api/music/tracks", methods=["GET"])
@_require_music_auth
def list_tracks():
    if not music_col:
        return jsonify({"error": "MONGO_URI is not configured"}), 500
    try:
        limit = max(1, min(200, int(request.args.get("limit", 100))))
        skip = max(0, int(request.args.get("skip", 0)))
    except ValueError:
        return jsonify({"error": "Invalid pagination parameters"}), 400

    docs = list(
        music_col.find(
            {}, {"title": 1, "artwork_url": 1, "file_url": 1}
        ).sort("_id", -1).skip(skip).limit(limit)
    )
    tracks = [
        {
            "id": str(doc["_id"]),
            "title": doc.get("title", "Untitled Track"),
            "artwork_url": doc.get("artwork_url", DEFAULT_ARTWORK),
            "file_url": doc.get("file_url", ""),
        }
        for doc in docs
    ]
    return jsonify({"tracks": tracks})


@app.route("/api/music/tracks/<track_id>", methods=["PUT"])
@_require_music_auth
def edit_track(track_id: str):
    if not music_col:
        return jsonify({"error": "MONGO_URI is not configured"}), 500

    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    artwork_url = (data.get("artwork_url") or "").strip() or DEFAULT_ARTWORK

    if not title:
        return jsonify({"error": "title is required"}), 400

    music_col.update_one(
        {"_id": ObjectId(track_id)},
        {"$set": {"title": title, "artwork_url": artwork_url}},
    )
    return jsonify({"ok": True})


@app.route("/api/music/tracks/<track_id>", methods=["DELETE"])
@_require_music_auth
def delete_track(track_id: str):
    if not music_col:
        return jsonify({"error": "MONGO_URI is not configured"}), 500

    music_col.delete_one({"_id": ObjectId(track_id)})
    return jsonify({"ok": True})


@app.route("/api/music/process", methods=["POST"])
@_require_music_auth
def process_music():
    if not music_col:
        return jsonify({"error": "MONGO_URI is not configured"}), 500
    if not SPACE_PASSWORD:
        return jsonify({"error": "SPACE_PASSWORD is not configured"}), 500

    tmp_dir = Path("./tmp")
    tmp_dir.mkdir(parents=True, exist_ok=True)

    if request.files.get("chunk"):
        return _handle_chunk_flow(tmp_dir)

    payload = request.get_json(silent=True) or {}
    url = (payload.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Either url or chunk upload is required"}), 400

    source_file = None
    final_mp3 = None
    try:
        source_file, extracted_title, extracted_artwork = _run_async(extract_from_url(url))
        title = (payload.get("title") or extracted_title or "Untitled Track").strip()
        artwork_url = (payload.get("artwork_url") or extracted_artwork or DEFAULT_ARTWORK).strip() or DEFAULT_ARTWORK

        final_mp3 = _run_async(convert_to_96k_mp3(source_file, output_name=secrets.token_hex(8)))
        cdn_url = _run_async(upload_to_cdn(final_mp3, title, SPACE_PASSWORD))

        doc = {
            "title": title,
            "file_url": cdn_url,
            "artwork_url": artwork_url,
            "created_at": datetime.utcnow(),
        }
        inserted = music_col.insert_one(doc)
        return jsonify({"ok": True, "track": {"id": str(inserted.inserted_id), **doc}})
    except Exception as exc:
        print(f"[MusicDashboard] URL processing failed: {type(exc).__name__}")
        return jsonify({"error": "Failed to process URL input"}), 500
    finally:
        if source_file:
            cleanup_path(source_file)
        if final_mp3:
            cleanup_path(final_mp3)


def _handle_chunk_flow(tmp_dir: Path):
    upload_id = (request.form.get("upload_id") or "").strip()
    chunk_index = request.form.get("chunk_index")
    total_chunks = request.form.get("total_chunks")
    chunk = request.files.get("chunk")

    if not upload_id or chunk_index is None or total_chunks is None or not chunk:
        return jsonify({"error": "Invalid chunk payload"}), 400
    if not re.match(UPLOAD_ID_PATTERN, upload_id):
        return jsonify({"error": "Invalid upload_id"}), 400

    try:
        idx = int(chunk_index)
        total = int(total_chunks)
    except ValueError:
        return jsonify({"error": "Invalid chunk indexes"}), 400
    if idx < 0 or total <= 0 or idx >= total or total > MAX_CHUNKS:
        return jsonify({"error": "Invalid chunk ranges"}), 400

    safe_key = _UPLOAD_SESSION_KEYS.setdefault(upload_id, secrets.token_hex(16))
    upload_dir = tmp_dir / "chunks" / safe_key
    upload_dir.mkdir(parents=True, exist_ok=True)
    part_path = upload_dir / f"{idx}.part"
    chunk.save(part_path)

    present = sorted(upload_dir.glob("*.part"))
    if len(present) < total:
        return jsonify({"ok": True, "status": "chunk_received", "received": len(present), "total": total})

    source_file = tmp_dir / f"assembled_{safe_key}.bin"
    final_mp3 = None
    try:
        with open(source_file, "wb") as target:
            for i in range(total):
                with open(upload_dir / f"{i}.part", "rb") as piece:
                    target.write(piece.read())

        title = (request.form.get("title") or f"upload-{upload_id}").strip()
        artwork_url = (request.form.get("artwork_url") or DEFAULT_ARTWORK).strip() or DEFAULT_ARTWORK

        final_mp3 = _run_async(convert_to_96k_mp3(source_file, output_name=secrets.token_hex(8)))
        cdn_url = _run_async(upload_to_cdn(final_mp3, title, SPACE_PASSWORD))

        doc = {
            "title": title,
            "file_url": cdn_url,
            "artwork_url": artwork_url,
            "created_at": datetime.utcnow(),
        }
        inserted = music_col.insert_one(doc)
        return jsonify({"ok": True, "status": "completed", "track": {"id": str(inserted.inserted_id), **doc}})
    except Exception as exc:
        print(f"[MusicDashboard] Chunk processing failed: {type(exc).__name__}")
        return jsonify({"error": "Failed to process uploaded file"}), 500
    finally:
        cleanup_tree(upload_dir)
        cleanup_path(source_file)
        _UPLOAD_SESSION_KEYS.pop(upload_id, None)
        if final_mp3:
            cleanup_path(final_mp3)


def run():
    app.run(host="0.0.0.0", port=8080)


def crypto_self_ping():
    while True:
        sleep_seconds = 300 + secrets.randbelow(301)
        time.sleep(sleep_seconds)
        try:
            requests.get(RENDER_PUBLIC_URL, timeout=20)
            print(f"🔄 Crypto-ping successful (Waited {sleep_seconds}s)")
        except Exception as e:
            print(f"⚠️ Crypto-ping failed: {e}")


def keep_alive():
    t = Thread(target=run)
    t.start()
    ping_thread = Thread(target=crypto_self_ping)
    ping_thread.start()


_LOGIN_HTML = """
<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>Music Login</title>
  <script src=\"https://cdn.tailwindcss.com\"></script>
</head>
<body class=\"bg-[#1e1f22] text-white min-h-screen grid place-items-center\">
  <form method=\"post\" class=\"w-full max-w-sm bg-[#2b2d31] p-6 rounded-xl border border-[#3f4147]\">
    <h1 class=\"text-2xl font-bold mb-4\">Music Dashboard Login</h1>
    {% if error %}<p class=\"text-red-400 text-sm mb-3\">{{ error }}</p>{% endif %}
    <input type=\"password\" name=\"password\" placeholder=\"Enter PASSWORD\" class=\"w-full p-3 rounded bg-[#1e1f22] border border-[#3f4147]\" required />
    <button class=\"mt-4 w-full p-3 rounded bg-indigo-600 hover:bg-indigo-500\">Login</button>
  </form>
</body>
</html>
"""


_MUSIC_HTML = """
<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>Music Dashboard</title>
  <script src=\"https://cdn.tailwindcss.com\"></script>
</head>
<body class=\"bg-[#1e1f22] text-[#dbdee1] min-h-screen\">
  <div class=\"max-w-7xl mx-auto p-6 space-y-6\">
    <div class=\"flex items-center justify-between\">
      <h1 class=\"text-3xl font-bold\">Music Dashboard</h1>
      <a href=\"/music/logout\" class=\"px-4 py-2 rounded bg-red-600 hover:bg-red-500\">Logout</a>
    </div>

    <section class=\"bg-[#2b2d31] p-4 rounded-xl border border-[#3f4147] space-y-4\">
      <h2 class=\"text-xl font-semibold\">Add Track (URL)</h2>
      <div class=\"grid md:grid-cols-3 gap-3\">
        <input id=\"urlInput\" class=\"p-3 rounded bg-[#1e1f22] border border-[#3f4147]\" placeholder=\"YouTube/SoundCloud URL\" />
        <input id=\"urlTitle\" class=\"p-3 rounded bg-[#1e1f22] border border-[#3f4147]\" placeholder=\"Custom Title (optional)\" />
        <input id=\"urlArtwork\" class=\"p-3 rounded bg-[#1e1f22] border border-[#3f4147]\" placeholder=\"Artwork URL (optional)\" />
      </div>
      <button id=\"addUrlBtn\" class=\"px-4 py-2 rounded bg-indigo-600 hover:bg-indigo-500\">Process URL</button>
    </section>

    <section class=\"bg-[#2b2d31] p-4 rounded-xl border border-[#3f4147] space-y-4\">
      <h2 class=\"text-xl font-semibold\">Add Track (Drag & Drop Upload)</h2>
      <div id=\"dropZone\" class=\"border-2 border-dashed border-[#5865F2] rounded-xl p-10 text-center\">Drop audio/video file here</div>
      <div class=\"grid md:grid-cols-2 gap-3\">
        <input id=\"fileTitle\" class=\"p-3 rounded bg-[#1e1f22] border border-[#3f4147]\" placeholder=\"Track Title\" />
        <input id=\"fileArtwork\" class=\"p-3 rounded bg-[#1e1f22] border border-[#3f4147]\" placeholder=\"Artwork URL (optional)\" />
      </div>
      <p id=\"uploadStatus\" class=\"text-sm text-[#949ba4]\"></p>
    </section>

    <section class=\"bg-[#2b2d31] p-4 rounded-xl border border-[#3f4147] space-y-4\">
      <div class=\"flex items-center justify-between gap-4\">
        <h2 class=\"text-xl font-semibold\">Tracks</h2>
        <input id=\"searchInput\" class=\"p-2 rounded bg-[#1e1f22] border border-[#3f4147] w-80\" placeholder=\"Search title...\" />
      </div>
      <div class=\"overflow-auto\">
        <table class=\"w-full text-sm\">
          <thead>
            <tr class=\"text-left border-b border-[#3f4147]\">
              <th class=\"py-2\">Artwork</th>
              <th class=\"py-2\">Title</th>
              <th class=\"py-2\">CDN URL</th>
              <th class=\"py-2\">Actions</th>
            </tr>
          </thead>
          <tbody id=\"tracksBody\"></tbody>
        </table>
      </div>
    </section>
  </div>

<script>
const CHUNK_SIZE = 10 * 1024 * 1024;
let tracks = [];

async function refreshTracks() {
  const res = await fetch('/api/music/tracks');
  const data = await res.json();
  tracks = data.tracks || [];
  renderTracks();
}

function renderTracks() {
  const q = (document.getElementById('searchInput').value || '').toLowerCase();
  const body = document.getElementById('tracksBody');
  body.innerHTML = '';

  tracks.filter(t => (t.title || '').toLowerCase().includes(q)).forEach(track => {
    const tr = document.createElement('tr');
    tr.className = 'border-b border-[#3f4147]';
    tr.innerHTML = `
      <td class=\"py-2\"><img src=\"${track.artwork_url || ''}\" class=\"w-12 h-12 rounded object-cover\"></td>
      <td class=\"py-2\">${track.title || ''}</td>
      <td class=\"py-2 break-all\">${track.file_url || ''}</td>
      <td class=\"py-2\">
        <button class=\"px-2 py-1 bg-yellow-600 rounded mr-2\" onclick=\"editTrack('${track.id}')\">Edit</button>
        <button class=\"px-2 py-1 bg-red-600 rounded\" onclick=\"deleteTrack('${track.id}')\">Delete</button>
      </td>
    `;
    body.appendChild(tr);
  });
}

async function editTrack(id) {
  const t = tracks.find(x => x.id === id);
  if (!t) return;
  const title = prompt('New title', t.title || '');
  if (!title) return;
  const artwork = prompt('New artwork URL', t.artwork_url || '') || '';
  await fetch(`/api/music/tracks/${id}`, {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({title, artwork_url: artwork})
  });
  await refreshTracks();
}

async function deleteTrack(id) {
  if (!confirm('Delete this track?')) return;
  await fetch(`/api/music/tracks/${id}`, { method: 'DELETE' });
  await refreshTracks();
}

document.getElementById('searchInput').addEventListener('input', renderTracks);

document.getElementById('addUrlBtn').addEventListener('click', async () => {
  const url = document.getElementById('urlInput').value.trim();
  const title = document.getElementById('urlTitle').value.trim();
  const artwork_url = document.getElementById('urlArtwork').value.trim();
  if (!url) return alert('URL required');
  const res = await fetch('/api/music/process', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({url, title, artwork_url})
  });
  const data = await res.json();
  if (!res.ok) return alert(data.error || 'Failed');
  document.getElementById('urlInput').value = '';
  document.getElementById('urlTitle').value = '';
  document.getElementById('urlArtwork').value = '';
  await refreshTracks();
});

const dropZone = document.getElementById('dropZone');
['dragenter','dragover'].forEach(evt => dropZone.addEventListener(evt, e => {
  e.preventDefault(); e.stopPropagation(); dropZone.classList.add('bg-[#1f2230]');
}));
['dragleave','drop'].forEach(evt => dropZone.addEventListener(evt, e => {
  e.preventDefault(); e.stopPropagation(); dropZone.classList.remove('bg-[#1f2230]');
}));

dropZone.addEventListener('drop', async e => {
  const file = e.dataTransfer.files?.[0];
  if (!file) return;
  const upload_id = crypto.randomUUID();
  const total = Math.ceil(file.size / CHUNK_SIZE);
  const title = document.getElementById('fileTitle').value.trim() || file.name;
  const artwork_url = document.getElementById('fileArtwork').value.trim();
  const status = document.getElementById('uploadStatus');

  for (let i = 0; i < total; i++) {
    const start = i * CHUNK_SIZE;
    const end = Math.min(file.size, start + CHUNK_SIZE);
    const blob = file.slice(start, end);
    const form = new FormData();
    form.append('upload_id', upload_id);
    form.append('chunk_index', String(i));
    form.append('total_chunks', String(total));
    form.append('title', title);
    form.append('artwork_url', artwork_url);
    form.append('chunk', blob, `${file.name}.part${i}`);

    status.textContent = `Uploading chunk ${i + 1}/${total}...`;
    const res = await fetch('/api/music/process', { method: 'POST', body: form });
    const data = await res.json();
    if (!res.ok) {
      status.textContent = data.error || 'Upload failed';
      return;
    }
    if (data.status === 'completed') {
      status.textContent = 'Upload complete and processed.';
    }
  }

  document.getElementById('fileTitle').value = '';
  document.getElementById('fileArtwork').value = '';
  await refreshTracks();
});

refreshTracks();
</script>
</body>
</html>
"""
