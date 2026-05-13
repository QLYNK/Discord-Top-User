from flask import jsonify


def register(app, deps):
    @app.route("/api/utilities/tad/<tad_id>", methods=["DELETE"])
    @deps["api_json_guard"]
    @deps["require_utilities_auth"]
    def delete_tad(tad_id: str):
        tad_col = deps["tad_col"]
        if not tad_col:
            return jsonify({"error": "MONGO_URI is not configured"}), 503
        result = tad_col.delete_one(deps["coerce_id_query"](tad_id))
        if result.deleted_count == 0:
            return jsonify({"error": "Entry not found"}), 404
        return jsonify({"ok": True})
