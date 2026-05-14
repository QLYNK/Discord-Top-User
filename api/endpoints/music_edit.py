from flask import jsonify, request


def register(app, deps):
    @app.route("/api/music/edit", methods=["POST"])
    @deps["api_json_guard"]
    @deps["require_music_auth"]
    def edit_track_post():
        music_col = deps["music_col"]
        if music_col is None:
            return jsonify({"error": "MONGO_URI is not configured"}), 503

        data = request.get_json(silent=True) or {}
        track_id = (data.get("id") or data.get("track_id") or "").strip()
        title = (data.get("title") or "").strip()
        artwork_url = (data.get("artwork_url") or "").strip() or deps["DEFAULT_ARTWORK"]

        if not track_id:
            return jsonify({"error": "track_id is required"}), 400
        if not title:
            return jsonify({"error": "title is required"}), 400

        result = music_col.update_one(
            deps["coerce_id_query"](track_id),
            {"$set": {"title": title, "name": title, "artwork_url": artwork_url}},
        )
        if result.matched_count == 0:
            return jsonify({"error": "Track not found"}), 404
        return jsonify({"ok": True})
