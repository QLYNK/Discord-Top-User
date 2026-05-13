from flask import jsonify, request


def register(app, deps):
    @app.route("/api/music/tracks/<track_id>", methods=["PUT"])
    @deps["api_json_guard"]
    @deps["require_music_auth"]
    def edit_track(track_id: str):
        music_col = deps["music_col"]
        if not music_col:
            return jsonify({"error": "MONGO_URI is not configured"}), 503

        data = request.get_json(silent=True) or {}
        title = (data.get("title") or "").strip()
        artwork_url = (data.get("artwork_url") or "").strip() or deps["DEFAULT_ARTWORK"]

        if not title:
            return jsonify({"error": "title is required"}), 400

        result = music_col.update_one(
            deps["coerce_id_query"](track_id),
            {"$set": {"title": title, "name": title, "artwork_url": artwork_url}},
        )
        if result.matched_count == 0:
            return jsonify({"error": "Track not found"}), 404
        return jsonify({"ok": True})

    @app.route("/api/music/tracks/<track_id>", methods=["DELETE"])
    @deps["api_json_guard"]
    @deps["require_music_auth"]
    def delete_track(track_id: str):
        music_col = deps["music_col"]
        if not music_col:
            return jsonify({"error": "MONGO_URI is not configured"}), 503

        result = music_col.delete_one(deps["coerce_id_query"](track_id))
        if result.deleted_count == 0:
            return jsonify({"error": "Track not found"}), 404
        return jsonify({"ok": True})
