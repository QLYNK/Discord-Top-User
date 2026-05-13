from flask import jsonify, request


def register(app, deps):
    @app.route("/api/music/tracks", methods=["GET"])
    @deps["api_json_guard"]
    @deps["require_music_auth"]
    def list_tracks():
        music_col = deps["music_col"]
        if not music_col:
            return jsonify({"error": "MONGO_URI is not configured"}), 503
        try:
            limit = max(1, min(200, int(request.args.get("limit", 100))))
            skip = max(0, int(request.args.get("skip", 0)))
        except ValueError:
            return jsonify({"error": "Invalid pagination parameters"}), 400

        docs = list(
            music_col.find({}, {"title": 1, "name": 1, "artwork_url": 1, "file_url": 1, "url": 1})
            .sort("_id", -1)
            .skip(skip)
            .limit(limit)
        )
        tracks = [deps["track_doc_to_payload"](doc) for doc in docs]
        return jsonify({"tracks": tracks})
