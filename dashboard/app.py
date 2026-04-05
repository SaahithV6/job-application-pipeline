"""
Flask Web Dashboard for Job Application Tracker.
"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from flask import Flask, render_template, request, jsonify, redirect, url_for
from werkzeug.utils import secure_filename
import db

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "job-pipeline-secret-key-change-me")

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
ALLOWED_EXTENSIONS = {"pdf", "doc", "docx", "txt"}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ── Health Check ──

@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "job-tracker"})


# ── Page Routes ──

@app.route("/")
def index():
    status_filter = request.args.get("status")
    search = request.args.get("search")
    applications = db.list_applications(status=status_filter, search=search, limit=200)
    stats = db.get_stats()
    config = db.get_config()
    return render_template("index.html",
                           applications=applications,
                           stats=stats,
                           config=config,
                           status_filter=status_filter,
                           search=search)


@app.route("/application/<int:app_id>")
def application_detail(app_id):
    application = db.get_application(app_id)
    if not application:
        return "Application not found", 404
    log = db.get_application_log(app_id)
    config = db.get_config()
    return render_template("detail.html", app=application, log=log, config=config)


@app.route("/settings")
def settings():
    config = db.get_config()
    return render_template("settings.html", config=config)


@app.route("/schedule/<int:app_id>")
def schedule_interview(app_id):
    application = db.get_application(app_id)
    if not application:
        return "Application not found", 404
    return render_template("schedule.html", app=application)


@app.route("/status")
def pipeline_status():
    latest = db.get_latest_pipeline_run()
    runs = db.get_pipeline_runs(limit=20)
    stats = db.get_stats()
    return render_template("status.html", latest=latest, runs=runs, stats=stats)


# ── API Routes ──

@app.route("/api/applications")
def api_list_applications():
    status = request.args.get("status")
    search = request.args.get("search")
    limit = int(request.args.get("limit", 100))
    offset = int(request.args.get("offset", 0))
    apps = db.list_applications(status=status, search=search, limit=limit, offset=offset)
    return jsonify({"applications": apps, "total": len(apps)})


@app.route("/api/applications/<int:app_id>")
def api_get_application(app_id):
    application = db.get_application(app_id)
    if not application:
        return jsonify({"error": "Not found"}), 404
    log = db.get_application_log(app_id)
    return jsonify({"application": application, "log": log})


@app.route("/api/applications/<int:app_id>", methods=["PATCH"])
def api_update_application(app_id):
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    success = db.update_application(app_id, **data)
    if success:
        return jsonify({"success": True, "application": db.get_application(app_id)})
    return jsonify({"error": "Update failed"}), 400


@app.route("/api/applications/<int:app_id>", methods=["DELETE"])
def api_delete_application(app_id):
    db.delete_application(app_id)
    return jsonify({"success": True})


@app.route("/api/applications/<int:app_id>/schedule", methods=["POST"])
def api_schedule(app_id):
    data = request.get_json()
    times = data.get("times", [])
    db.update_application(app_id, status="self_schedule",
                          notes=f"User preferred times: {', '.join(times)}")
    return jsonify({"success": True, "times": times})


@app.route("/api/stats")
def api_stats():
    return jsonify(db.get_stats())


@app.route("/api/config")
def api_get_config():
    return jsonify(db.get_config())


@app.route("/api/config", methods=["PUT"])
def api_update_config():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    db.set_config_bulk(data)
    return jsonify({"success": True, "config": db.get_config()})


@app.route("/api/upload-resume", methods=["POST"])
def api_upload_resume():
    if "resume" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    file = request.files["resume"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400
    if not allowed_file(file.filename):
        return jsonify({"error": "File type not allowed. Use PDF, DOC, DOCX, or TXT"}), 400
    filename = secure_filename(file.filename)
    filepath = os.path.join(UPLOAD_DIR, filename)
    file.save(filepath)
    db.set_config("resume_path", filepath)
    return jsonify({"success": True, "path": filepath, "filename": filename})


# ── Sync API (used by the Pokee pipeline to push data to the Replit dashboard) ──

SYNC_KEY = os.environ.get("SYNC_API_KEY", "pipeline-sync-key-change-me")


def _check_sync_key():
    key = request.headers.get("X-Sync-Key") or request.args.get("key")
    return key == SYNC_KEY


@app.route("/api/sync/application", methods=["POST"])
def api_sync_application():
    """Receive an application record from the pipeline and upsert it."""
    if not _check_sync_key():
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400
    # Check if already exists by job_url
    job_url = data.get("job_url", "")
    if job_url and db.is_duplicate(job_url):
        # Update existing
        with db.get_db() as conn:
            row = conn.execute("SELECT id FROM applications WHERE job_url = ?", (job_url,)).fetchone()
            if row:
                db.update_application(row["id"], **{k: v for k, v in data.items()
                                                     if k in ("status", "notes", "interview_date",
                                                              "interview_link", "calendar_event_id",
                                                              "browser_session_id", "browser_live_url")})
                return jsonify({"success": True, "action": "updated", "id": row["id"]})
    # Insert new
    app_id = db.add_application(
        company=data.get("company", "Unknown"),
        role=data.get("role", "Unknown"),
        job_url=job_url,
        apply_url=data.get("apply_url", ""),
        notes=data.get("notes", ""),
        browser_session_id=data.get("browser_session_id"),
        browser_live_url=data.get("browser_live_url"),
    )
    if data.get("status") and data["status"] != "applied":
        db.update_application(app_id, status=data["status"])
    if data.get("date_applied"):
        with db.get_db() as conn:
            conn.execute("UPDATE applications SET date_applied = ? WHERE id = ?",
                         (data["date_applied"], app_id))
    return jsonify({"success": True, "action": "created", "id": app_id})


@app.route("/api/sync/bulk", methods=["POST"])
def api_sync_bulk():
    """Receive multiple application records at once."""
    if not _check_sync_key():
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json()
    applications = data.get("applications", [])
    results = []
    for app_data in applications:
        job_url = app_data.get("job_url", "")
        if job_url and db.is_duplicate(job_url):
            with db.get_db() as conn:
                row = conn.execute("SELECT id FROM applications WHERE job_url = ?", (job_url,)).fetchone()
                if row:
                    db.update_application(row["id"], **{k: v for k, v in app_data.items()
                                                         if k in ("status", "notes")})
                    results.append({"action": "updated", "id": row["id"]})
                    continue
        app_id = db.add_application(
            company=app_data.get("company", "Unknown"),
            role=app_data.get("role", "Unknown"),
            job_url=job_url,
            apply_url=app_data.get("apply_url", ""),
            notes=app_data.get("notes", ""),
        )
        if app_data.get("status") and app_data["status"] != "applied":
            db.update_application(app_id, status=app_data["status"])
        if app_data.get("date_applied"):
            with db.get_db() as conn:
                conn.execute("UPDATE applications SET date_applied = ? WHERE id = ?",
                             (app_data["date_applied"], app_id))
        results.append({"action": "created", "id": app_id})
    return jsonify({"success": True, "synced": len(results), "results": results})


@app.route("/api/pipeline/sessions")
def api_pipeline_sessions():
    """Return recent applications that have Browser Use session URLs."""
    with db.get_db() as conn:
        rows = conn.execute(
            """SELECT id, company, role, date_applied, status, browser_session_id,
                      browser_live_url, updated_at
               FROM applications
               WHERE browser_live_url IS NOT NULL AND browser_live_url != ''
               ORDER BY date_applied DESC LIMIT 20""",
        ).fetchall()
        sessions = [dict(r) for r in rows]
    return jsonify({"sessions": sessions})


@app.route("/api/pipeline/queue")
def api_pipeline_queue():
    """Return jobs currently queued or being applied to via Browser Use."""
    with db.get_db() as conn:
        rows = conn.execute(
            """SELECT id, company, role, job_url, apply_url, status,
                      browser_session_id, browser_live_url, date_applied, notes
               FROM applications
               WHERE status IN ('queued', 'applying')
               ORDER BY date_applied DESC LIMIT 20""",
        ).fetchall()
        queue = [dict(r) for r in rows]
    return jsonify({"queue": queue})


@app.route("/api/pipeline/status")
def api_pipeline_status():
    latest = db.get_latest_pipeline_run()
    runs = db.get_pipeline_runs(limit=20)
    return jsonify({"latest": latest, "runs": runs})


@app.route("/api/sync/pipeline-run", methods=["POST"])
def api_sync_pipeline_run():
    """Receive a pipeline run report from the Pokee sandbox."""
    if not _check_sync_key():
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400
    run_id = db.add_pipeline_run(
        run_type=data.get("run_type", "full"),
        started_at=data.get("started_at", ""),
        finished_at=data.get("finished_at"),
        status=data.get("status", "success"),
        jobs_found=data.get("jobs_found", 0),
        jobs_applied=data.get("jobs_applied", 0),
        jobs_failed=data.get("jobs_failed", 0),
        emails_checked=data.get("emails_checked", 0),
        status_updates=data.get("status_updates", 0),
        interviews_found=data.get("interviews_found", 0),
        ghosted_count=data.get("ghosted_count", 0),
        error_message=data.get("error_message"),
        details=data.get("details"),
    )
    return jsonify({"success": True, "id": run_id})


if __name__ == "__main__":
    db.init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
