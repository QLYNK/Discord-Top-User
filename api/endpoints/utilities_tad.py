from flask import jsonify, request


def register(app, deps):
    @app.route("/api/utilities/tad", methods=["GET"])
    @deps["api_json_guard"]
    @deps["require_utilities_auth"]
    def list_tad():
        tad_col = deps["tad_col"]
        if tad_col is None:
            return jsonify({"error": "MONGO_URI is not configured"}), 503
        docs = list(tad_col.find({}, {"type": 1, "text": 1}))
        return jsonify({"tad": [{"id": str(d["_id"]), "type": d.get("type", ""), "text": d.get("text", "")} for d in docs]})

    @app.route("/api/utilities/tad", methods=["POST"])
    @deps["api_json_guard"]
    @deps["require_utilities_auth"]
    def create_tad():
        tad_col = deps["tad_col"]
        if tad_col is None:
            return jsonify({"error": "MONGO_URI is not configured"}), 503
        data = request.get_json(silent=True) or {}
        tad_type = (data.get("type") or "").strip().lower()
        text = (data.get("text") or "").strip()
        if tad_type not in ("truth", "dare") or not text:
            return jsonify({"error": "type (truth|dare) and text are required"}), 400
        inserted = tad_col.insert_one({"type": tad_type, "text": text})
        return jsonify({"ok": True, "id": str(inserted.inserted_id)})
