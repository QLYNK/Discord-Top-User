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
                "servers": snapshot["total_guilds"],
                "ping": ping,
                "users": snapshot["total_users"],
                "global_message_count": snapshot["global_message_count"],
                "uptime_seconds": snapshot["uptime_seconds"],
            }
        )
