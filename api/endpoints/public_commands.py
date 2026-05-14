from flask import jsonify


def register(app, deps):
    @app.route("/api/public/commands", methods=["GET"])
    @deps["api_json_guard"]
    def public_commands():
        commands_snapshot = deps["get_public_commands_snapshot"]()
        return jsonify({"commands": commands_snapshot, "total": len(commands_snapshot)})
