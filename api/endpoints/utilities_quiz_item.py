from flask import jsonify


def register(app, deps):
    @app.route("/api/utilities/quiz/<quiz_id>", methods=["DELETE"])
    @deps["api_json_guard"]
    @deps["require_utilities_auth"]
    def delete_quiz(quiz_id: str):
        quiz_col = deps["quiz_col"]
        if quiz_col is None:
            return jsonify({"error": "MONGO_URI is not configured"}), 503
        result = quiz_col.delete_one(deps["coerce_id_query"](quiz_id))
        if result.deleted_count == 0:
            return jsonify({"error": "Quiz not found"}), 404
        return jsonify({"ok": True})
