from flask import jsonify


def register(app, deps):
    @app.route("/api/public/guilds/<int:guild_id>", methods=["GET"])
    @deps["api_json_guard"]
    def public_guild_detail(guild_id: int):
        snapshot = deps["get_discovery_snapshot"]()
        for guild in snapshot.get("guilds", []):
            try:
                resolved_id = int(guild.get("id", 0))
            except (TypeError, ValueError):
                continue
            if resolved_id == guild_id:
                return jsonify({"guild": guild})
        return jsonify({"error": "Guild not found"}), 404
