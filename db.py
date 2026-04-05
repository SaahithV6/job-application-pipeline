"""
Data layer for the Job Application Pipeline.
SQLite database with tables for applications, config, and audit logs.
"""

import sqlite3
import os
import json
from datetime import datetime, timedelta
from contextlib import contextmanager

DB_PATH = os.environ.get("JOB_PIPELINE_DB", os.path.join(os.path.dirname(__file__), "pipeline.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    role TEXT NOT NULL,
    job_url TEXT,
    apply_url TEXT,
    date_applied TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'applied',
    confirmation_email_thread_id TEXT,
    browser_session_id TEXT,
    browser_live_url TEXT,
    notes TEXT,
    last_status_check TEXT,
    interview_date TEXT,
    interview_link TEXT,
    calendar_event_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS application_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    action TEXT NOT NULL,
    details TEXT,
    FOREIGN KEY (application_id) REFERENCES applications(id)
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_type TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    jobs_found INTEGER DEFAULT 0,
    jobs_applied INTEGER DEFAULT 0,
    jobs_failed INTEGER DEFAULT 0,
    emails_checked INTEGER DEFAULT 0,
    status_updates INTEGER DEFAULT 0,
    interviews_found INTEGER DEFAULT 0,
    ghosted_count INTEGER DEFAULT 0,
    error_message TEXT,
    details TEXT
);

CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
CREATE INDEX IF NOT EXISTS idx_applications_company ON applications(company);
CREATE INDEX IF NOT EXISTS idx_applications_date ON applications(date_applied);
CREATE INDEX IF NOT EXISTS idx_log_app_id ON application_log(application_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started ON pipeline_runs(started_at);
"""

DEFAULT_CONFIG = {
    "max_applications_per_day": "5",
    "resume_path": "",
    "linkedin_profile_url": "",
    "user_email": "",
    "user_name": "",
    "user_phone": "",
    "user_timezone": "America/Los_Angeles",
    "ghosted_threshold_days": "21",
    "auto_reply_enabled": "true",
    "work_history_summary": "",
    "education_summary": "",
}

VALID_STATUSES = {"queued", "applying", "applied", "ghosted", "rejected", "interview", "offer", "self_schedule"}


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript(SCHEMA)
        for key, value in DEFAULT_CONFIG.items():
            conn.execute(
                "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)",
                (key, value),
            )


# ── Config helpers ──

def get_config(key=None):
    with get_db() as conn:
        if key:
            row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else None
        rows = conn.execute("SELECT key, value FROM config").fetchall()
        return {r["key"]: r["value"] for r in rows}


def set_config(key, value):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO config (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
            (key, str(value), str(value)),
        )


def set_config_bulk(data: dict):
    with get_db() as conn:
        for key, value in data.items():
            conn.execute(
                "INSERT INTO config (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
                (key, str(value), str(value)),
            )


# ── Application CRUD ──

def add_application(company, role, job_url=None, apply_url=None, notes=None,
                    browser_session_id=None, browser_live_url=None):
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO applications
               (company, role, job_url, apply_url, date_applied, status, notes,
                browser_session_id, browser_live_url, last_status_check)
               VALUES (?, ?, ?, ?, ?, 'applied', ?, ?, ?, ?)""",
            (company, role, job_url, apply_url, now, notes, browser_session_id, browser_live_url, now),
        )
        app_id = cur.lastrowid
        conn.execute(
            "INSERT INTO application_log (application_id, action, details) VALUES (?, ?, ?)",
            (app_id, "applied", f"Applied to {company} - {role}"),
        )
        return app_id


def get_application(app_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
        return dict(row) if row else None


def list_applications(status=None, search=None, limit=100, offset=0):
    with get_db() as conn:
        query = "SELECT * FROM applications WHERE 1=1"
        params = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if search:
            query += " AND (company LIKE ? OR role LIKE ?)"
            params.extend([f"%{search}%", f"%{search}%"])
        query += " ORDER BY date_applied DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def update_application(app_id, **kwargs):
    allowed = {
        "company", "role", "job_url", "apply_url", "status", "notes",
        "confirmation_email_thread_id", "browser_session_id", "browser_live_url",
        "last_status_check", "interview_date", "interview_link", "calendar_event_id",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not updates:
        return False
    updates["updated_at"] = datetime.utcnow().isoformat()
    if "status" in updates:
        updates["last_status_check"] = updates["updated_at"]
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [app_id]
    with get_db() as conn:
        conn.execute(f"UPDATE applications SET {set_clause} WHERE id = ?", values)
        if "status" in updates:
            conn.execute(
                "INSERT INTO application_log (application_id, action, details) VALUES (?, ?, ?)",
                (app_id, updates["status"], json.dumps(updates)),
            )
    return True


def delete_application(app_id):
    with get_db() as conn:
        conn.execute("DELETE FROM application_log WHERE application_id = ?", (app_id,))
        conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))


def is_duplicate(job_url):
    if not job_url:
        return False
    with get_db() as conn:
        row = conn.execute("SELECT id FROM applications WHERE job_url = ?", (job_url,)).fetchone()
        return row is not None


def today_application_count():
    today = datetime.utcnow().strftime("%Y-%m-%d")
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM applications WHERE date_applied LIKE ?",
            (f"{today}%",),
        ).fetchone()
        return row["cnt"]


def can_apply_today():
    max_per_day = int(get_config("max_applications_per_day") or 5)
    return today_application_count() < max_per_day


def get_ghosted_candidates(threshold_days=None):
    if threshold_days is None:
        threshold_days = int(get_config("ghosted_threshold_days") or 21)
    cutoff = (datetime.utcnow() - timedelta(days=threshold_days)).isoformat()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM applications WHERE status = 'applied' AND date_applied < ?",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]


def mark_ghosted():
    candidates = get_ghosted_candidates()
    for app in candidates:
        update_application(app["id"], status="ghosted")
    return len(candidates)


def get_application_log(app_id):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM application_log WHERE application_id = ? ORDER BY timestamp DESC",
            (app_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_stats():
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) as cnt FROM applications").fetchone()["cnt"]
        by_status = {}
        for row in conn.execute("SELECT status, COUNT(*) as cnt FROM applications GROUP BY status").fetchall():
            by_status[row["status"]] = row["cnt"]
        today = datetime.utcnow().strftime("%Y-%m-%d")
        today_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM applications WHERE date_applied LIKE ?",
            (f"{today}%",),
        ).fetchone()["cnt"]
        week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
        week_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM applications WHERE date_applied > ?",
            (week_ago,),
        ).fetchone()["cnt"]
    return {
        "total": total,
        "by_status": by_status,
        "today": today_count,
        "this_week": week_count,
        "max_per_day": int(get_config("max_applications_per_day") or 5),
    }


# ── Pipeline Run Tracking ──

def add_pipeline_run(run_type, started_at, finished_at=None, status="running",
                     jobs_found=0, jobs_applied=0, jobs_failed=0,
                     emails_checked=0, status_updates=0, interviews_found=0,
                     ghosted_count=0, error_message=None, details=None):
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO pipeline_runs
               (run_type, started_at, finished_at, status, jobs_found, jobs_applied,
                jobs_failed, emails_checked, status_updates, interviews_found,
                ghosted_count, error_message, details)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_type, started_at, finished_at, status, jobs_found, jobs_applied,
             jobs_failed, emails_checked, status_updates, interviews_found,
             ghosted_count, error_message,
             json.dumps(details) if details and not isinstance(details, str) else details),
        )
        return cur.lastrowid


def update_pipeline_run(run_id, **kwargs):
    allowed = {
        "finished_at", "status", "jobs_found", "jobs_applied", "jobs_failed",
        "emails_checked", "status_updates", "interviews_found", "ghosted_count",
        "error_message", "details",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if "details" in updates and not isinstance(updates["details"], str):
        updates["details"] = json.dumps(updates["details"])
    if not updates:
        return False
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [run_id]
    with get_db() as conn:
        conn.execute(f"UPDATE pipeline_runs SET {set_clause} WHERE id = ?", values)
    return True


def get_pipeline_runs(limit=20):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_latest_pipeline_run():
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


# Initialize on import
init_db()
