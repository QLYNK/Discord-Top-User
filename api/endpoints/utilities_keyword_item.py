from flask import jsonify, request


def register(app, deps):
    @app.route("/api/utilities/keywords/<kw_id>", methods=["PUT"])
    @deps["api_json_guard"]
    @deps["require_utilities_auth"]
    def update_keyword(kw_id: str):
        keywords_col = deps["keywords_col"]
        if not keywords_col:
            return jsonify({"error": "MONGO_URI is not configured"}), 503
        data = request.get_json(silent=True) or {}
        trigger = (data.get("trigger") or "").strip().lower()
        reply = (data.get("reply") or "").strip()
        if not trigger or not reply:
            return jsonify({"error": "trigger and reply are required"}), 400
        result = keywords_col.update_one(deps["coerce_id_query"](kw_id), {"$set": {"trigger": trigger, "reply": reply}})
        if result.matched_count == 0:
            return jsonify({"error": "Keyword not found"}), 404
        return jsonify({"ok": True})

    @app.route("/api/utilities/keywords/<kw_id>", methods=["DELETE"])
    @deps["api_json_guard"]
    @deps["require_utilities_auth"]
    def delete_keyword(kw_id: str):
        keywords_col = deps["keywords_col"]
        if not keywords_col:
            return jsonify({"error": "MONGO_URI is not configured"}), 503
        result = keywords_col.delete_one(deps["coerce_id_query"](kw_id))
        if result.deleted_count == 0:
            return jsonify({"error": "Keyword not found"}), 404
        return jsonify({"ok": True})
