from flask import jsonify, request


def register(app, deps):
    @app.route("/api/utilities/quiz", methods=["GET"])
    @deps["api_json_guard"]
    @deps["require_utilities_auth"]
    def list_quiz():
        quiz_col = deps["quiz_col"]
        if quiz_col is None:
            return jsonify({"error": "MONGO_URI is not configured"}), 503
        docs = list(quiz_col.find({}, {"question": 1, "options": 1, "correct_answer": 1}))
        return jsonify(
            {
                "quiz": [
                    {
                        "id": str(d["_id"]),
                        "question": d.get("question", ""),
                        "options": d.get("options", []),
                        "correct_answer": d.get("correct_answer", ""),
                    }
                    for d in docs
                ]
            }
        )

    @app.route("/api/utilities/quiz", methods=["POST"])
    @deps["api_json_guard"]
    @deps["require_utilities_auth"]
    def create_quiz():
        quiz_col = deps["quiz_col"]
        if quiz_col is None:
            return jsonify({"error": "MONGO_URI is not configured"}), 503
        data = request.get_json(silent=True) or {}
        question = (data.get("question") or "").strip()
        options = [str(o).strip() for o in (data.get("options") or []) if str(o).strip()]
        correct_answer = (data.get("correct_answer") or "").strip()
        if not question or len(options) != 4 or not correct_answer or correct_answer not in options:
            return jsonify({"error": "question, exactly 4 options, and a valid correct_answer are required"}), 400
        inserted = quiz_col.insert_one({"question": question, "options": options, "correct_answer": correct_answer})
        return jsonify({"ok": True, "id": str(inserted.inserted_id)})
