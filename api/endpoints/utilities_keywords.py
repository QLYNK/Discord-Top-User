from flask import jsonify, request


def register(app, deps):
    @app.route("/api/utilities/keywords", methods=["GET"])
    @deps["api_json_guard"]
    @deps["require_utilities_auth"]
    def list_keywords():
        keywords_col = deps["keywords_col"]
        if not keywords_col:
            return jsonify({"error": "MONGO_URI is not configured"}), 503
        docs = list(keywords_col.find({}, {"trigger": 1, "reply": 1}))
        return jsonify({"keywords": [{"id": str(d["_id"]), "trigger": d.get("trigger", ""), "reply": d.get("reply", "")} for d in docs]})

    @app.route("/api/utilities/keywords", methods=["POST"])
    @deps["api_json_guard"]
    @deps["require_utilities_auth"]
    def create_keyword():
        keywords_col = deps["keywords_col"]
        if not keywords_col:
            return jsonify({"error": "MONGO_URI is not configured"}), 503
        data = request.get_json(silent=True) or {}
        trigger = (data.get("trigger") or "").strip().lower()
        reply = (data.get("reply") or "").strip()
        if not trigger or not reply:
            return jsonify({"error": "trigger and reply are required"}), 400
        inserted = keywords_col.insert_one({"trigger": trigger, "reply": reply})
        return jsonify({"ok": True, "id": str(inserted.inserted_id)})
