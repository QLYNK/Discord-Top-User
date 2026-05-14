from flask import jsonify


def register(app, deps):
    @app.route("/api/public/guilds/<guild_id>", methods=["GET"])
    @deps["api_json_guard"]
    def public_guild_detail(guild_id: str):
        target = str(guild_id).strip()
        if not target:
            return jsonify({"error": "Guild not found"}), 404
        snapshot = deps["get_discovery_snapshot"]()
        for guild in snapshot.get("guilds", []):
            if str(guild.get("id", "")).strip() == target:
                return jsonify({"guild": guild})
        return jsonify({"error": "Guild not found"}), 404
