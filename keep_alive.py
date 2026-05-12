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

sync_mongo_client = MongoClient(MONGO_URI, connect=True, serverSelectionTimeoutMS=5000) if MONGO_URI else None
music_col = sync_mongo_client["LeaderboardBotDB"]["MusicTracks"] if sync_mongo_client else None
keywords_col = sync_mongo_client["LeaderboardBotDB"]["GameKeywords"] if sync_mongo_client else None
tad_col = sync_mongo_client["LeaderboardBotDB"]["TruthOrDare"] if sync_mongo_client else None
quiz_col = sync_mongo_client["LeaderboardBotDB"]["QuizQuestions"] if sync_mongo_client else None
UPLOAD_ID_PATTERN = r"^[a-fA-F0-9-]{8,64}$"
MAX_CHUNKS = 4096
CHUNK_SIZE_BYTES = 10 * 1024 * 1024
_UPLOAD_SESSION_KEYS: dict[str, str] = {}


def _run_async(coro):
    return asyncio.run(coro)


def _coerce_id_query(raw_id: str) -> dict:
    try:
        return {"$or": [{"_id": ObjectId(raw_id)}, {"_id": raw_id}]}
    except Exception:
        return {"_id": raw_id}


def _track_doc_to_payload(doc: dict) -> dict:
    return {
        "id": str(doc.get("_id", "")),
        "title": doc.get("title") or doc.get("name") or "Untitled Track",
        "artwork_url": doc.get("artwork_url") or DEFAULT_ARTWORK,
        "file_url": doc.get("file_url") or doc.get("url") or "",
    }


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


def _require_utilities_auth(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not PASSWORD:
            return jsonify({"error": "PASSWORD env var is not configured"}), 500
        if not session.get("utilities_auth"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect("/utilities/login")
        return func(*args, **kwargs)

    return wrapper


def _api_json_guard(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            app.logger.exception("API request failed: %s", exc)
            return jsonify({"error": "Internal server error"}), 500

    return wrapper


@app.route("/")
def home():
    return render_template_string(_HOME_HTML)


@app.route("/api/stats")
@_api_json_guard
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
@_api_json_guard
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
            {}, {"title": 1, "name": 1, "artwork_url": 1, "file_url": 1, "url": 1}
        ).sort("_id", -1).skip(skip).limit(limit)
    )
    tracks = [_track_doc_to_payload(doc) for doc in docs]
    return jsonify({"tracks": tracks})


@app.route("/api/music/tracks/<track_id>", methods=["PUT"])
@_api_json_guard
@_require_music_auth
def edit_track(track_id: str):
    if not music_col:
        return jsonify({"error": "MONGO_URI is not configured"}), 500

    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    artwork_url = (data.get("artwork_url") or "").strip() or DEFAULT_ARTWORK

    if not title:
        return jsonify({"error": "title is required"}), 400

    result = music_col.update_one(
        _coerce_id_query(track_id),
        {"$set": {"title": title, "name": title, "artwork_url": artwork_url}},
    )
    if result.matched_count == 0:
        return jsonify({"error": "Track not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/music/edit", methods=["POST"])
@_api_json_guard
@_require_music_auth
def edit_track_post():
    if not music_col:
        return jsonify({"error": "MONGO_URI is not configured"}), 500

    data = request.get_json(silent=True) or {}
    track_id = (data.get("id") or data.get("track_id") or "").strip()
    title = (data.get("title") or "").strip()
    artwork_url = (data.get("artwork_url") or "").strip() or DEFAULT_ARTWORK

    if not track_id:
        return jsonify({"error": "track_id is required"}), 400
    if not title:
        return jsonify({"error": "title is required"}), 400

    result = music_col.update_one(
        _coerce_id_query(track_id),
        {"$set": {"title": title, "name": title, "artwork_url": artwork_url}},
    )
    if result.matched_count == 0:
        return jsonify({"error": "Track not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/music/tracks/<track_id>", methods=["DELETE"])
@_api_json_guard
@_require_music_auth
def delete_track(track_id: str):
    if not music_col:
        return jsonify({"error": "MONGO_URI is not configured"}), 500

    result = music_col.delete_one(_coerce_id_query(track_id))
    if result.deleted_count == 0:
        return jsonify({"error": "Track not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/music/process", methods=["POST"])
@_api_json_guard
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


# ── Utilities auth routes ──────────────────────────────────────────────────

@app.route("/utilities/login", methods=["GET", "POST"])
def utilities_login():
    if request.method == "POST":
        submitted = request.form.get("password", "")
        if PASSWORD and secrets.compare_digest(submitted, PASSWORD):
            session["utilities_auth"] = True
            return redirect("/utilities")
        return render_template_string(_UTILITIES_LOGIN_HTML, error="Invalid password")
    return render_template_string(_UTILITIES_LOGIN_HTML, error=None)


@app.route("/utilities/logout")
def utilities_logout():
    session.pop("utilities_auth", None)
    return redirect("/utilities/login")


@app.route("/utilities")
@_require_utilities_auth
def utilities_dashboard():
    return render_template_string(_UTILITIES_HTML)


# ── Utilities CRUD: Keywords ───────────────────────────────────────────────

@app.route("/api/utilities/keywords", methods=["GET"])
@_api_json_guard
@_require_utilities_auth
def list_keywords():
    if not keywords_col:
        return jsonify({"error": "MONGO_URI is not configured"}), 500
    docs = list(keywords_col.find({}, {"trigger": 1, "reply": 1}))
    return jsonify({"keywords": [{"id": str(d["_id"]), "trigger": d.get("trigger", ""), "reply": d.get("reply", "")} for d in docs]})


@app.route("/api/utilities/keywords", methods=["POST"])
@_api_json_guard
@_require_utilities_auth
def create_keyword():
    if not keywords_col:
        return jsonify({"error": "MONGO_URI is not configured"}), 500
    data = request.get_json(silent=True) or {}
    trigger = (data.get("trigger") or "").strip().lower()
    reply = (data.get("reply") or "").strip()
    if not trigger or not reply:
        return jsonify({"error": "trigger and reply are required"}), 400
    inserted = keywords_col.insert_one({"trigger": trigger, "reply": reply})
    return jsonify({"ok": True, "id": str(inserted.inserted_id)})


@app.route("/api/utilities/keywords/<kw_id>", methods=["PUT"])
@_api_json_guard
@_require_utilities_auth
def update_keyword(kw_id: str):
    if not keywords_col:
        return jsonify({"error": "MONGO_URI is not configured"}), 500
    data = request.get_json(silent=True) or {}
    trigger = (data.get("trigger") or "").strip().lower()
    reply = (data.get("reply") or "").strip()
    if not trigger or not reply:
        return jsonify({"error": "trigger and reply are required"}), 400
    keywords_col.update_one({"_id": ObjectId(kw_id)}, {"$set": {"trigger": trigger, "reply": reply}})
    return jsonify({"ok": True})


@app.route("/api/utilities/keywords/<kw_id>", methods=["DELETE"])
@_api_json_guard
@_require_utilities_auth
def delete_keyword(kw_id: str):
    if not keywords_col:
        return jsonify({"error": "MONGO_URI is not configured"}), 500
    keywords_col.delete_one({"_id": ObjectId(kw_id)})
    return jsonify({"ok": True})


# ── Utilities CRUD: Truth or Dare ─────────────────────────────────────────

@app.route("/api/utilities/tad", methods=["GET"])
@_api_json_guard
@_require_utilities_auth
def list_tad():
    if not tad_col:
        return jsonify({"error": "MONGO_URI is not configured"}), 500
    docs = list(tad_col.find({}, {"type": 1, "text": 1}))
    return jsonify({"tad": [{"id": str(d["_id"]), "type": d.get("type", ""), "text": d.get("text", "")} for d in docs]})


@app.route("/api/utilities/tad", methods=["POST"])
@_api_json_guard
@_require_utilities_auth
def create_tad():
    if not tad_col:
        return jsonify({"error": "MONGO_URI is not configured"}), 500
    data = request.get_json(silent=True) or {}
    tad_type = (data.get("type") or "").strip().lower()
    text = (data.get("text") or "").strip()
    if tad_type not in ("truth", "dare") or not text:
        return jsonify({"error": "type (truth|dare) and text are required"}), 400
    inserted = tad_col.insert_one({"type": tad_type, "text": text})
    return jsonify({"ok": True, "id": str(inserted.inserted_id)})


@app.route("/api/utilities/tad/<tad_id>", methods=["DELETE"])
@_api_json_guard
@_require_utilities_auth
def delete_tad(tad_id: str):
    if not tad_col:
        return jsonify({"error": "MONGO_URI is not configured"}), 500
    tad_col.delete_one({"_id": ObjectId(tad_id)})
    return jsonify({"ok": True})


# ── Utilities CRUD: Quiz ──────────────────────────────────────────────────

@app.route("/api/utilities/quiz", methods=["GET"])
@_api_json_guard
@_require_utilities_auth
def list_quiz():
    if not quiz_col:
        return jsonify({"error": "MONGO_URI is not configured"}), 500
    docs = list(quiz_col.find({}, {"question": 1, "options": 1, "correct_answer": 1}))
    return jsonify({"quiz": [{"id": str(d["_id"]), "question": d.get("question", ""), "options": d.get("options", []), "correct_answer": d.get("correct_answer", "")} for d in docs]})


@app.route("/api/utilities/quiz", methods=["POST"])
@_api_json_guard
@_require_utilities_auth
def create_quiz():
    if not quiz_col:
        return jsonify({"error": "MONGO_URI is not configured"}), 500
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    options = [str(o).strip() for o in (data.get("options") or []) if str(o).strip()]
    correct_answer = (data.get("correct_answer") or "").strip()
    if not question or len(options) != 4 or not correct_answer or correct_answer not in options:
        return jsonify({"error": "question, exactly 4 options, and a valid correct_answer are required"}), 400
    inserted = quiz_col.insert_one({"question": question, "options": options, "correct_answer": correct_answer})
    return jsonify({"ok": True, "id": str(inserted.inserted_id)})


@app.route("/api/utilities/quiz/<quiz_id>", methods=["DELETE"])
@_api_json_guard
@_require_utilities_auth
def delete_quiz(quiz_id: str):
    if not quiz_col:
        return jsonify({"error": "MONGO_URI is not configured"}), 500
    quiz_col.delete_one({"_id": ObjectId(quiz_id)})
    return jsonify({"ok": True})


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
  await fetch('/api/music/edit', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({track_id: id, title, artwork_url: artwork})
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


_HOME_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Bot Dashboard</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-[#1e1f22] text-[#dbdee1] min-h-screen grid place-items-center">
  <div class="max-w-xl w-full p-6 space-y-6 text-center">
    <h1 class="text-4xl font-bold text-white">🤖 Bot Dashboard</h1>
    <p class="text-[#949ba4]">Select a section to manage:</p>
    <div class="grid grid-cols-2 gap-6">
      <a href="/music" class="bg-[#2b2d31] hover:bg-[#383a40] border border-[#5865F2] rounded-2xl p-8 flex flex-col items-center gap-3 transition">
        <span class="text-5xl">🎵</span>
        <span class="text-xl font-semibold">Music</span>
        <span class="text-sm text-[#949ba4]">Manage tracks & uploads</span>
      </a>
      <a href="/utilities" class="bg-[#2b2d31] hover:bg-[#383a40] border border-[#5865F2] rounded-2xl p-8 flex flex-col items-center gap-3 transition">
        <span class="text-5xl">🎮</span>
        <span class="text-xl font-semibold">Utilities</span>
        <span class="text-sm text-[#949ba4]">Keywords, TAD & Quiz</span>
      </a>
    </div>
    <p class="text-xs text-[#949ba4]">an app by <a href="https://deepdey.vercel.app/" class="underline hover:text-white">deep</a></p>
  </div>
</body>
</html>
"""


_UTILITIES_LOGIN_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Utilities Login</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-[#1e1f22] text-white min-h-screen grid place-items-center">
  <form method="post" class="w-full max-w-sm bg-[#2b2d31] p-6 rounded-xl border border-[#3f4147]">
    <h1 class="text-2xl font-bold mb-4">Utilities Dashboard Login</h1>
    {% if error %}<p class="text-red-400 text-sm mb-3">{{ error }}</p>{% endif %}
    <input type="password" name="password" placeholder="Enter PASSWORD" class="w-full p-3 rounded bg-[#1e1f22] border border-[#3f4147]" required />
    <button class="mt-4 w-full p-3 rounded bg-indigo-600 hover:bg-indigo-500">Login</button>
  </form>
</body>
</html>
"""


_UTILITIES_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Utilities Dashboard</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-[#1e1f22] text-[#dbdee1] min-h-screen">
  <div class="max-w-5xl mx-auto p-6 space-y-8">
    <div class="flex items-center justify-between">
      <h1 class="text-3xl font-bold">🎮 Utilities Dashboard</h1>
      <div class="flex gap-3">
        <a href="/" class="px-4 py-2 rounded bg-[#3f4147] hover:bg-[#4e5058]">Home</a>
        <a href="/utilities/logout" class="px-4 py-2 rounded bg-red-600 hover:bg-red-500">Logout</a>
      </div>
    </div>

    <!-- Tabs -->
    <div class="flex gap-2 border-b border-[#3f4147]">
      <button onclick="showTab('keywords')" id="tab-keywords" class="tab-btn px-4 py-2 rounded-t bg-indigo-600 text-white">Keywords</button>
      <button onclick="showTab('tad')" id="tab-tad" class="tab-btn px-4 py-2 rounded-t bg-[#2b2d31] hover:bg-[#3f4147]">Truth or Dare</button>
      <button onclick="showTab('quiz')" id="tab-quiz" class="tab-btn px-4 py-2 rounded-t bg-[#2b2d31] hover:bg-[#3f4147]">Quiz</button>
    </div>

    <!-- Keywords Panel -->
    <div id="panel-keywords" class="space-y-4">
      <div class="bg-[#2b2d31] p-4 rounded-xl border border-[#3f4147] space-y-3">
        <h2 class="text-xl font-semibold">Add Auto-Reply Keyword</h2>
        <div class="grid md:grid-cols-2 gap-3">
          <input id="kwTrigger" class="p-3 rounded bg-[#1e1f22] border border-[#3f4147]" placeholder="Trigger word/phrase" />
          <input id="kwReply" class="p-3 rounded bg-[#1e1f22] border border-[#3f4147]" placeholder="Bot reply text" />
        </div>
        <button onclick="createKeyword()" class="px-4 py-2 rounded bg-indigo-600 hover:bg-indigo-500">Add Keyword</button>
      </div>
      <div class="bg-[#2b2d31] p-4 rounded-xl border border-[#3f4147]">
        <h2 class="text-xl font-semibold mb-3">Existing Keywords</h2>
        <div class="overflow-auto">
          <table class="w-full text-sm">
            <thead><tr class="text-left border-b border-[#3f4147]">
              <th class="py-2">Trigger</th><th class="py-2">Reply</th><th class="py-2">Actions</th>
            </tr></thead>
            <tbody id="kwBody"></tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Truth or Dare Panel -->
    <div id="panel-tad" class="space-y-4 hidden">
      <div class="bg-[#2b2d31] p-4 rounded-xl border border-[#3f4147] space-y-3">
        <h2 class="text-xl font-semibold">Add Truth or Dare</h2>
        <div class="grid md:grid-cols-3 gap-3">
          <select id="tadType" class="p-3 rounded bg-[#1e1f22] border border-[#3f4147]">
            <option value="truth">Truth</option>
            <option value="dare">Dare</option>
          </select>
          <input id="tadText" class="p-3 rounded bg-[#1e1f22] border border-[#3f4147] md:col-span-2" placeholder="Question or task text" />
        </div>
        <button onclick="createTAD()" class="px-4 py-2 rounded bg-indigo-600 hover:bg-indigo-500">Add Entry</button>
      </div>
      <div class="bg-[#2b2d31] p-4 rounded-xl border border-[#3f4147]">
        <h2 class="text-xl font-semibold mb-3">Existing Entries</h2>
        <div class="overflow-auto">
          <table class="w-full text-sm">
            <thead><tr class="text-left border-b border-[#3f4147]">
              <th class="py-2">Type</th><th class="py-2">Text</th><th class="py-2">Actions</th>
            </tr></thead>
            <tbody id="tadBody"></tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Quiz Panel -->
    <div id="panel-quiz" class="space-y-4 hidden">
      <div class="bg-[#2b2d31] p-4 rounded-xl border border-[#3f4147] space-y-3">
        <h2 class="text-xl font-semibold">Add Quiz Question</h2>
        <input id="quizQ" class="w-full p-3 rounded bg-[#1e1f22] border border-[#3f4147]" placeholder="Question" />
        <div class="grid md:grid-cols-2 gap-3">
          <input id="quizO1" class="p-3 rounded bg-[#1e1f22] border border-[#3f4147]" placeholder="Option A" />
          <input id="quizO2" class="p-3 rounded bg-[#1e1f22] border border-[#3f4147]" placeholder="Option B" />
          <input id="quizO3" class="p-3 rounded bg-[#1e1f22] border border-[#3f4147]" placeholder="Option C" />
          <input id="quizO4" class="p-3 rounded bg-[#1e1f22] border border-[#3f4147]" placeholder="Option D" />
        </div>
        <input id="quizCorrect" class="w-full p-3 rounded bg-[#1e1f22] border border-[#3f4147]" placeholder="Correct answer (must match one of the options exactly)" />
        <button onclick="createQuiz()" class="px-4 py-2 rounded bg-indigo-600 hover:bg-indigo-500">Add Question</button>
      </div>
      <div class="bg-[#2b2d31] p-4 rounded-xl border border-[#3f4147]">
        <h2 class="text-xl font-semibold mb-3">Existing Questions</h2>
        <div class="overflow-auto">
          <table class="w-full text-sm">
            <thead><tr class="text-left border-b border-[#3f4147]">
              <th class="py-2">Question</th><th class="py-2">Options</th><th class="py-2">Answer</th><th class="py-2">Actions</th>
            </tr></thead>
            <tbody id="quizBody"></tbody>
          </table>
        </div>
      </div>
    </div>
  </div>

<script>
// ── Tab switching ──────────────────────────────────────────────────────────
function showTab(name) {
  ['keywords','tad','quiz'].forEach(t => {
    document.getElementById('panel-' + t).classList.toggle('hidden', t !== name);
    const btn = document.getElementById('tab-' + t);
    btn.className = t === name
      ? 'tab-btn px-4 py-2 rounded-t bg-indigo-600 text-white'
      : 'tab-btn px-4 py-2 rounded-t bg-[#2b2d31] hover:bg-[#3f4147]';
  });
  if (name === 'keywords') refreshKeywords();
  if (name === 'tad') refreshTAD();
  if (name === 'quiz') refreshQuiz();
}

// ── Keywords ──────────────────────────────────────────────────────────────
let keywords = [];
async function refreshKeywords() {
  const res = await fetch('/api/utilities/keywords');
  const data = await res.json();
  keywords = data.keywords || [];
  renderKeywords();
}
function renderKeywords() {
  const body = document.getElementById('kwBody');
  body.innerHTML = '';
  keywords.forEach(k => {
    const tr = document.createElement('tr');
    tr.className = 'border-b border-[#3f4147]';
    tr.innerHTML = `<td class="py-2">${esc(k.trigger)}</td><td class="py-2">${esc(k.reply)}</td>
      <td class="py-2">
        <button class="px-2 py-1 bg-yellow-600 rounded mr-2" onclick="editKeyword('${k.id}')">Edit</button>
        <button class="px-2 py-1 bg-red-600 rounded" onclick="deleteKeyword('${k.id}')">Delete</button>
      </td>`;
    body.appendChild(tr);
  });
}
async function createKeyword() {
  const trigger = document.getElementById('kwTrigger').value.trim();
  const reply = document.getElementById('kwReply').value.trim();
  if (!trigger || !reply) return alert('Both trigger and reply are required.');
  const res = await fetch('/api/utilities/keywords', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({trigger, reply})});
  const data = await res.json();
  if (!res.ok) return alert(data.error || 'Failed');
  document.getElementById('kwTrigger').value = '';
  document.getElementById('kwReply').value = '';
  await refreshKeywords();
}
async function editKeyword(id) {
  const k = keywords.find(x => x.id === id);
  if (!k) return;
  const trigger = prompt('New trigger', k.trigger);
  if (!trigger) return;
  const reply = prompt('New reply', k.reply);
  if (!reply) return;
  await fetch('/api/utilities/keywords/' + id, {method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({trigger, reply})});
  await refreshKeywords();
}
async function deleteKeyword(id) {
  if (!confirm('Delete this keyword?')) return;
  await fetch('/api/utilities/keywords/' + id, {method:'DELETE'});
  await refreshKeywords();
}

// ── Truth or Dare ──────────────────────────────────────────────────────────
let tadItems = [];
async function refreshTAD() {
  const res = await fetch('/api/utilities/tad');
  const data = await res.json();
  tadItems = data.tad || [];
  renderTAD();
}
function renderTAD() {
  const body = document.getElementById('tadBody');
  body.innerHTML = '';
  tadItems.forEach(t => {
    const tr = document.createElement('tr');
    tr.className = 'border-b border-[#3f4147]';
    tr.innerHTML = `<td class="py-2 capitalize">${esc(t.type)}</td><td class="py-2">${esc(t.text)}</td>
      <td class="py-2"><button class="px-2 py-1 bg-red-600 rounded" onclick="deleteTAD('${t.id}')">Delete</button></td>`;
    body.appendChild(tr);
  });
}
async function createTAD() {
  const type = document.getElementById('tadType').value;
  const text = document.getElementById('tadText').value.trim();
  if (!text) return alert('Text is required.');
  const res = await fetch('/api/utilities/tad', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({type, text})});
  const data = await res.json();
  if (!res.ok) return alert(data.error || 'Failed');
  document.getElementById('tadText').value = '';
  await refreshTAD();
}
async function deleteTAD(id) {
  if (!confirm('Delete this entry?')) return;
  await fetch('/api/utilities/tad/' + id, {method:'DELETE'});
  await refreshTAD();
}

// ── Quiz ──────────────────────────────────────────────────────────────────
let quizItems = [];
async function refreshQuiz() {
  const res = await fetch('/api/utilities/quiz');
  const data = await res.json();
  quizItems = data.quiz || [];
  renderQuiz();
}
function renderQuiz() {
  const body = document.getElementById('quizBody');
  body.innerHTML = '';
  quizItems.forEach(q => {
    const tr = document.createElement('tr');
    tr.className = 'border-b border-[#3f4147]';
    tr.innerHTML = `<td class="py-2">${esc(q.question)}</td>
      <td class="py-2 text-xs">${(q.options||[]).map(o => esc(o)).join('<br>')}</td>
      <td class="py-2 text-green-400">${esc(q.correct_answer)}</td>
      <td class="py-2"><button class="px-2 py-1 bg-red-600 rounded" onclick="deleteQuiz('${q.id}')">Delete</button></td>`;
    body.appendChild(tr);
  });
}
async function createQuiz() {
  const question = document.getElementById('quizQ').value.trim();
  const options = [
    document.getElementById('quizO1').value.trim(),
    document.getElementById('quizO2').value.trim(),
    document.getElementById('quizO3').value.trim(),
    document.getElementById('quizO4').value.trim(),
  ];
  const correct_answer = document.getElementById('quizCorrect').value.trim();
  if (!question || options.some(o => !o) || !correct_answer) return alert('All fields are required.');
  if (!options.includes(correct_answer)) return alert('Correct answer must exactly match one of the 4 options.');
  const res = await fetch('/api/utilities/quiz', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({question, options, correct_answer})});
  const data = await res.json();
  if (!res.ok) return alert(data.error || 'Failed');
  ['quizQ','quizO1','quizO2','quizO3','quizO4','quizCorrect'].forEach(id => document.getElementById(id).value = '');
  await refreshQuiz();
}
async function deleteQuiz(id) {
  if (!confirm('Delete this question?')) return;
  await fetch('/api/utilities/quiz/' + id, {method:'DELETE'});
  await refreshQuiz();
}

function esc(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// Init
refreshKeywords();
</script>
</body>
</html>
"""
