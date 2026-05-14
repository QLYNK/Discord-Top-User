import re
from typing import Any

from flask import jsonify, request


def register(app, deps):
    @app.route("/api/public/guilds", methods=["GET"])
    @deps["api_json_guard"]
    def public_guilds():
        snapshot = deps["get_discovery_snapshot"]()
        query = (request.args.get("q") or "").strip().lower()
        try:
            page = max(1, int(request.args.get("page", 1)))
        except ValueError:
            return jsonify({"error": "Invalid page parameter"}), 400
        per_page = 50

        guilds = snapshot.get("guilds", [])
        if query:
            terms = [term for term in re.split(r"\s+", query) if term]

            def _match(entry: dict[str, Any]) -> bool:
                haystack = "\n".join(
                    [
                        str(entry.get("name", "")),
                        str(entry.get("description", "")),
                        str(entry.get("owner_name", "")),
                        str(entry.get("owner_id", "")),
                        str(entry.get("bot_integration_status", "")),
                        str(entry.get("permanent_invite_link", "")),
                        " ".join(str(v) for v in list(entry.get("member_names") or [])),
                    ]
                ).lower()
                return all(term in haystack for term in terms)

            guilds = [g for g in guilds if _match(g)]

        start = (page - 1) * per_page
        end = start + per_page
        page_items = guilds[start:end]

        return jsonify(
            {
                "items": page_items,
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total_items": len(guilds),
                    "has_next": end < len(guilds),
                    "has_prev": page > 1,
                },
                "totals": {
                    "guilds": snapshot.get("total_guilds", len(guilds)),
                    "users": snapshot.get("total_users", 0),
                    "global_message_count": snapshot.get("global_message_count", 0),
                    "uptime_seconds": snapshot.get("uptime_seconds", 0),
                    "ping_ms": snapshot.get("ping_ms", 0),
                },
            }
        )
