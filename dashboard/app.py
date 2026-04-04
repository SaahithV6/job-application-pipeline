"""
Flask Web Dashboard for Job Application Tracker.
"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from flask import Flask, render_template, request, jsonify, redirect, url_for
import db

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "job-pipeline-secret-key-change-me")


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


if __name__ == "__main__":
    db.init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
