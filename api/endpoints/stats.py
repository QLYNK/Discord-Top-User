import json

from flask import jsonify


def register(app, deps):
    @app.route("/api/stats", methods=["GET"])
    @deps["api_json_guard"]
    def stats():
        snapshot = deps["get_discovery_snapshot"]()
        ping = "..."
        try:
            with open("stats.json", "r", encoding="utf-8") as f:
                ping = json.load(f).get("ping", "...")
        except Exception:
            pass
        return jsonify(
            {
                "servers": snapshot.get("total_guilds", 0),
                "ping": ping,
                "users": snapshot.get("total_users", 0),
                "global_message_count": snapshot.get("global_message_count", 0),
                "uptime_seconds": snapshot.get("uptime_seconds", 0),
            }
        )
