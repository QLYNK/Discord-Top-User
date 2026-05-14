from datetime import datetime
from pathlib import Path

from flask import jsonify, request


def register(app, deps):
    @app.route("/api/music/process", methods=["POST"])
    @deps["api_json_guard"]
    @deps["require_music_auth"]
    def process_music():
        music_col = deps["music_col"]
        if music_col is None:
            return jsonify({"error": "MONGO_URI is not configured"}), 503
        if not deps["SPACE_PASSWORD"]:
            return jsonify({"error": "SPACE_PASSWORD is not configured"}), 503

        tmp_dir = Path("./tmp")
        tmp_dir.mkdir(parents=True, exist_ok=True)

        if request.files.get("chunk"):
            return deps["handle_chunk_flow"](tmp_dir)

        payload = request.get_json(silent=True) or {}
        url = (payload.get("url") or "").strip()
        if not url:
            return jsonify({"error": "Either url or chunk upload is required"}), 400

        source_file = None
        final_mp3 = None
        try:
            source_file, extracted_title, extracted_artwork = deps["run_async"](deps["extract_from_url"](url))
            title = (payload.get("title") or extracted_title or "Untitled Track").strip()
            artwork_url = (payload.get("artwork_url") or extracted_artwork or deps["DEFAULT_ARTWORK"]).strip() or deps[
                "DEFAULT_ARTWORK"
            ]

            final_mp3 = deps["run_async"](deps["convert_to_96k_mp3"](source_file, output_name=deps["secrets"].token_hex(8)))
            cdn_url = deps["run_async"](deps["upload_to_cdn"](final_mp3, title, deps["SPACE_PASSWORD"]))

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
                deps["cleanup_path"](source_file)
            if final_mp3:
                deps["cleanup_path"](final_mp3)
