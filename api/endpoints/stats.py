from flask import jsonify


def register(app, deps):
    @app.route("/api/stats", methods=["GET"])
    @deps["api_json_guard"]
    def stats():
        snapshot = deps["get_discovery_snapshot"]()
        return jsonify(
            {
                "servers": snapshot.get("total_guilds", 0),
                "ping": snapshot.get("ping_ms", 0),
                "users": snapshot.get("total_users", 0),
                "global_message_count": snapshot.get("global_message_count", 0),
                "uptime_seconds": snapshot.get("uptime_seconds", 0),
            }
        )
