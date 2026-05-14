from datetime import datetime

from flask import jsonify, request


def register(app, deps):
    @app.route("/api/utilities/notes", methods=["GET"])
    @deps["api_json_guard"]
    @deps["require_utilities_auth"]
    def list_notes():
        notes_col = deps["notes_col"]
        if notes_col is None:
            return jsonify({"error": "MONGO_URI is not configured"}), 503
        docs = list(notes_col.find({}, {"text": 1, "profile_pic_url": 1, "updated_at": 1}).sort("updated_at", -1))
        notes = []
        for d in docs:
            updated_at = d.get("updated_at")
            notes.append(
                {
                    "id": str(d["_id"]),
                    "text": d.get("text", ""),
                    "profile_pic_url": d.get("profile_pic_url", ""),
                    "updated_at": updated_at.isoformat() if isinstance(updated_at, datetime) else "",
                }
            )
        return jsonify({"notes": notes})

    @app.route("/api/utilities/notes", methods=["POST"])
    @deps["api_json_guard"]
    @deps["require_utilities_auth"]
    def create_note():
        notes_col = deps["notes_col"]
        if notes_col is None:
            return jsonify({"error": "MONGO_URI is not configured"}), 503
        data = request.get_json(silent=True) or {}
        text = (data.get("text") or "").strip()
        profile_pic_url = (data.get("profile_pic_url") or "").strip()
        if not text:
            return jsonify({"error": "text is required"}), 400
        now = datetime.utcnow()
        inserted = notes_col.insert_one({"text": text, "profile_pic_url": profile_pic_url, "created_at": now, "updated_at": now})
        return jsonify({"ok": True, "id": str(inserted.inserted_id)})
