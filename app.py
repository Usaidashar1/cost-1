"""
Azure Cost Estimation Tool - Flask Web App
Deployable to Azure App Service (Python 3.10+)
"""
import os
import uuid
import threading
from pathlib import Path
from datetime import datetime
from flask import (Flask, render_template, request, jsonify,
                   send_file, redirect, url_for)

# ── Import converter (same folder) ──────────────────────────────────────────
import sys
sys.path.insert(0, os.path.dirname(__file__))
from convert import convert

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB upload limit

# ── Folders (use /tmp on Azure App Service — writable) ──────────────────────
BASE_DIR    = Path(__file__).parent
UPLOAD_DIR  = Path(os.environ.get("UPLOAD_DIR",  "/tmp/azure_tool/uploads"))
OUTPUT_DIR  = Path(os.environ.get("OUTPUT_DIR",  "/tmp/azure_tool/outputs"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── In-memory job tracker ────────────────────────────────────────────────────
jobs = {}   # job_id → {"status": "running"|"done"|"error", "file": path, "log": [...]}


def run_job(job_id, input_path, output_path, currency):
    """Run the conversion in a background thread."""
    log_lines = jobs[job_id]["log"]
    try:
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            convert(str(input_path), str(output_path), currency)
        log_lines.extend(buf.getvalue().splitlines())
        jobs[job_id]["status"] = "done"
        jobs[job_id]["file"]   = str(output_path)
    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"]  = str(e)
        log_lines.append(f"ERROR: {e}")


# ── Routes ───────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    f = request.files["file"]
    if not f.filename.lower().endswith(".xlsx"):
        return jsonify({"error": "Only .xlsx files are supported"}), 400

    currency = request.form.get("currency", "INR").upper()

    # Save uploaded file
    job_id   = str(uuid.uuid4())[:8]
    safe_name = f"input_{job_id}.xlsx"
    input_path  = UPLOAD_DIR / safe_name
    timestamp   = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = OUTPUT_DIR / f"Cost_Estimation_{job_id}_{timestamp}.xlsx"
    f.save(str(input_path))

    # Start background job
    jobs[job_id] = {"status": "running", "file": None, "log": [], "error": None}
    t = threading.Thread(target=run_job,
                         args=(job_id, input_path, output_path, currency),
                         daemon=True)
    t.start()

    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>")
def status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "status": job["status"],
        "log":    job["log"][-20:],   # last 20 lines
        "error":  job.get("error"),
    })


@app.route("/download/<job_id>")
def download(job_id):
    job = jobs.get(job_id)
    if not job or job["status"] != "done":
        return "File not ready", 404
    path = job["file"]
    if not path or not Path(path).exists():
        return "File not found", 404
    return send_file(
        path,
        as_attachment=True,
        download_name=f"Cost_Estimation_{job_id}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.utcnow().isoformat()})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
