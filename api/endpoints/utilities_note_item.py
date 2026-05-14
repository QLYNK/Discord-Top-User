from datetime import datetime

from flask import jsonify, request


def register(app, deps):
    @app.route("/api/utilities/notes/<note_id>", methods=["PUT"])
    @deps["api_json_guard"]
    @deps["require_utilities_auth"]
    def update_note(note_id: str):
        notes_col = deps["notes_col"]
        if notes_col is None:
            return jsonify({"error": "MONGO_URI is not configured"}), 503
        data = request.get_json(silent=True) or {}
        text = (data.get("text") or "").strip()
        profile_pic_url = (data.get("profile_pic_url") or "").strip()
        if not text:
            return jsonify({"error": "text is required"}), 400
        result = notes_col.update_one(
            deps["coerce_id_query"](note_id),
            {"$set": {"text": text, "profile_pic_url": profile_pic_url, "updated_at": datetime.utcnow()}},
        )
        if result.matched_count == 0:
            return jsonify({"error": "Note not found"}), 404
        return jsonify({"ok": True})

    @app.route("/api/utilities/notes/<note_id>", methods=["DELETE"])
    @deps["api_json_guard"]
    @deps["require_utilities_auth"]
    def delete_note(note_id: str):
        notes_col = deps["notes_col"]
        if notes_col is None:
            return jsonify({"error": "MONGO_URI is not configured"}), 503
        result = notes_col.delete_one(deps["coerce_id_query"](note_id))
        if result.deleted_count == 0:
            return jsonify({"error": "Note not found"}), 404
        return jsonify({"ok": True})
