"""
Payday Runway statement-analysis API.

POST /api/analyze-statements  (multipart form, field "statements", 1+ PDFs)
  -> forecast profile JSON: paycheck pattern, recurring bills, balance,
     average daily spend, warnings.

Privacy: uploads are written to a temp dir, parsed, and deleted immediately
in a finally block. Nothing is stored, logged, or retained.

Run locally:  gunicorn app:app   (or python app.py for dev)
"""
import os
import tempfile
import uuid
from flask import Flask, jsonify, request
from flask_cors import CORS

from parser import extract_text, parse_statement
from forecast import build_profile

app = Flask(__name__)
CORS(app)  # prototype web app calls from another origin
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB total


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "payday-runway-analyzer", "banks": ["wells_fargo"]})


@app.post("/api/analyze-statements")
def analyze_statements():
    files = request.files.getlist("statements")
    if not files:
        return jsonify({"error": "No files uploaded under field 'statements'."}), 400

    tmpdir = tempfile.mkdtemp(prefix="prw_")
    saved = []
    try:
        for f in files:
            name = (f.filename or "").lower()
            if not name.endswith(".pdf"):
                return jsonify({"error": f"Unsupported file type: {f.filename}. PDF only."}), 400
            path = os.path.join(tmpdir, f"{uuid.uuid4().hex}.pdf")
            f.save(path)
            saved.append(path)

        all_txns, balance, warnings = [], None, []
        for path in saved:
            try:
                parsed = parse_statement(extract_text(path))
            except Exception:
                warnings.append("One file could not be read and was skipped.")
                continue
            all_txns.extend(parsed["transactions"])
            if parsed["ending_balance"] is not None:
                balance = parsed["ending_balance"]  # latest file wins

        profile = build_profile(all_txns, balance)
        profile["warnings"] = warnings + profile["warnings"]
        profile["transactions_read"] = len(all_txns)
        return jsonify(profile)
    finally:
        # Delete immediately — the core privacy promise.
        for path in saved:
            try:
                os.remove(path)
            except OSError:
                pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
