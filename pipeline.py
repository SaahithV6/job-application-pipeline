"""
Main Pipeline Orchestrator.
Ties together all modules for the daily automated job application cycle.
"""

import json
import subprocess
import sys
import os
from datetime import datetime

import db
from discover_jobs import discover_jobs
from apply import apply_to_jobs
from monitor import monitor_emails
from calendar_ops import process_interviews, create_offer_deadline_event
from interview_prep import process_interview_replies

REPLIT_DASHBOARD_URL = os.environ.get("REPLIT_DASHBOARD_URL", "")
SYNC_API_KEY = os.environ.get("SYNC_API_KEY", "pipeline-sync-key-change-me")


def run_discovery_and_apply(dry_run=False):
    """Phase 1: Discover jobs and apply."""
    print("=" * 60)
    print("PHASE 1: JOB DISCOVERY & APPLICATION")
    print("=" * 60)
    print(f"Time: {datetime.utcnow().isoformat()}")
    print(f"Daily limit: {db.get_config('max_applications_per_day')}")
    print(f"Applied today: {db.today_application_count()}")
    print()

    if not db.can_apply_today():
        print("Daily application limit reached. Skipping.")
        return {"phase": "discovery", "skipped": True, "reason": "limit_reached"}

    # Discover
    print("[1/3] Discovering jobs...")
    discovery = discover_jobs()
    print(f"  Found {len(discovery.get('jobs', []))} new jobs (after dedup)")

    if not discovery.get("jobs"):
        print("  No new jobs found.")
        return {"phase": "discovery", "jobs_found": 0}

    # Queue — push discovered jobs to dashboard so user can see them
    print(f"\n[2/3] Queueing {len(discovery['jobs'])} jobs on dashboard...")
    for job in discovery["jobs"]:
        try:
            sync_queued_job(job)
            print(f"  Queued: {job.get('company', '?')} - {job.get('job_title', '?')}")
        except Exception as e:
            print(f"  Queue sync error: {e}")

    # Apply
    print(f"\n[3/3] Applying to {len(discovery['jobs'])} jobs via Browser Use...")
    if dry_run:
        print("  DRY RUN - forms will be filled but NOT submitted")

    results = apply_to_jobs(discovery["jobs"], dry_run=dry_run)
    print(f"  Applied: {results['applied']}, Failed: {results['failed']}")

    return {
        "phase": "discovery_and_apply",
        "jobs_found": len(discovery.get("jobs", [])),
        "applied": results["applied"],
        "failed": results["failed"],
        "results": results["results"],
    }


def run_email_monitor():
    """Phase 2: Monitor emails and update statuses."""
    print("\n" + "=" * 60)
    print("PHASE 2: EMAIL MONITORING & STATUS UPDATES")
    print("=" * 60)

    print("[1/3] Scanning Gmail for application updates...")
    monitor_results = monitor_emails()
    print(f"  Emails checked: {monitor_results['emails_checked']}")
    print(f"  Status updates: {len(monitor_results['status_updates'])}")
    print(f"  Interviews found: {len(monitor_results['interviews_found'])}")
    print(f"  Ghosted: {monitor_results['ghosted_count']}")

    # Process interviews - create calendar events
    calendar_results = []
    if monitor_results["interviews_found"]:
        print(f"\n[2/3] Creating calendar events for {len(monitor_results['interviews_found'])} interviews...")
        calendar_results = process_interviews(monitor_results["interviews_found"])
        for cr in calendar_results:
            action = cr.get("action", "unknown")
            app_id = cr.get("application_id", "?")
            print(f"  App #{app_id}: {action}")

    # Send auto-replies for interviews
    reply_results = []
    if monitor_results["interviews_found"]:
        print(f"\n[3/3] Sending interview auto-replies...")
        reply_results = process_interview_replies(monitor_results["interviews_found"])
        for rr in reply_results:
            action = rr.get("action", "unknown")
            app_id = rr.get("application_id", "?")
            print(f"  App #{app_id}: {action}")

    # Handle offers - create deadline events
    for update in monitor_results["status_updates"]:
        if update["new_status"] == "offer":
            print(f"\n  Creating offer deadline for {update['company']}...")
            create_offer_deadline_event(update["application_id"])

    return {
        "phase": "email_monitor",
        "monitor": monitor_results,
        "calendar": calendar_results,
        "replies": reply_results,
    }


def run_full_pipeline(dry_run=False):
    """Run the complete daily pipeline."""
    started_at = datetime.utcnow().isoformat()
    print("=" * 60)
    print("JOB APPLICATION PIPELINE - FULL RUN")
    print(f"Date: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    results = {}
    run_status = "success"
    error_msg = None

    # Phase 1: Discover and apply
    try:
        results["discovery"] = run_discovery_and_apply(dry_run=dry_run)
    except Exception as e:
        print(f"\nERROR in Phase 1: {e}")
        results["discovery"] = {"error": str(e)}
        run_status = "error"
        error_msg = f"Phase 1: {e}"

    # Phase 2: Monitor emails
    try:
        results["monitoring"] = run_email_monitor()
    except Exception as e:
        print(f"\nERROR in Phase 2: {e}")
        results["monitoring"] = {"error": str(e)}
        run_status = "error"
        error_msg = (error_msg + "; " if error_msg else "") + f"Phase 2: {e}"

    # Summary
    print("\n" + "=" * 60)
    print("PIPELINE COMPLETE")
    print("=" * 60)
    stats = db.get_stats()
    print(f"Total applications: {stats['total']}")
    print(f"By status: {json.dumps(stats['by_status'])}")
    print(f"Applied today: {stats['today']}/{stats['max_per_day']}")

    results["stats"] = stats

    # Sync results to Replit dashboard
    print("\nSyncing to Replit dashboard...")
    try:
        sync_to_replit()
    except Exception as e:
        print(f"  Sync error: {e}")

    # Sync pipeline run status
    disc = results.get("discovery", {})
    mon = results.get("monitoring", {}).get("monitor", {})
    sync_pipeline_run({
        "run_type": "full",
        "started_at": started_at,
        "finished_at": datetime.utcnow().isoformat(),
        "status": run_status,
        "jobs_found": disc.get("jobs_found", 0),
        "jobs_applied": disc.get("applied", 0),
        "jobs_failed": disc.get("failed", 0),
        "emails_checked": mon.get("emails_checked", 0),
        "status_updates": len(mon.get("status_updates", [])),
        "interviews_found": len(mon.get("interviews_found", [])),
        "ghosted_count": mon.get("ghosted_count", 0),
        "error_message": error_msg,
    })

    return results


def _sync_post(endpoint, data):
    """POST JSON to a Replit dashboard endpoint."""
    if not REPLIT_DASHBOARD_URL:
        return {}
    url = f"{REPLIT_DASHBOARD_URL.rstrip('/')}{endpoint}"
    payload = json.dumps(data, default=str)
    cmd = [
        "curl", "-s", "-X", "POST", url,
        "-H", "Content-Type: application/json",
        "-H", f"X-Sync-Key: {SYNC_API_KEY}",
        "-d", payload,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return json.loads(result.stdout) if result.stdout else {}
    except Exception:
        return {}


def sync_queued_job(job):
    """Push a discovered job to the dashboard as 'queued' so the user can see it."""
    return _sync_post("/api/sync/application", {
        "company": job.get("company", "Unknown"),
        "role": job.get("job_title", "Unknown"),
        "job_url": job.get("job_url", ""),
        "apply_url": job.get("apply_url", ""),
        "status": "queued",
        "notes": f"Source: {job.get('source', 'discovery')}\nDescription: {job.get('description', '')[:300]}",
    })


def sync_applying_job(job_url, browser_session_id, browser_live_url):
    """Update a queued job on the dashboard to 'applying' with the Browser Use live URL."""
    return _sync_post("/api/sync/application", {
        "job_url": job_url,
        "status": "applying",
        "browser_session_id": browser_session_id,
        "browser_live_url": browser_live_url,
    })


def sync_to_replit():
    """Push all local application data to the Replit-hosted dashboard."""
    if not REPLIT_DASHBOARD_URL:
        print("  No REPLIT_DASHBOARD_URL set, skipping sync")
        return
    apps = db.list_applications(limit=500)
    if not apps:
        return
    url = f"{REPLIT_DASHBOARD_URL.rstrip('/')}/api/sync/bulk"
    payload = json.dumps({"applications": apps})
    cmd = [
        "curl", "-s", "-X", "POST", url,
        "-H", "Content-Type: application/json",
        "-H", f"X-Sync-Key: {SYNC_API_KEY}",
        "-d", payload,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        resp = json.loads(result.stdout) if result.stdout else {}
        print(f"  Synced {resp.get('synced', 0)} applications to Replit dashboard")
    except Exception as e:
        print(f"  Sync failed: {e}")


def sync_pipeline_run(run_data):
    """Push a pipeline run report to the Replit dashboard."""
    if not REPLIT_DASHBOARD_URL:
        return
    url = f"{REPLIT_DASHBOARD_URL.rstrip('/')}/api/sync/pipeline-run"
    payload = json.dumps(run_data, default=str)
    cmd = [
        "curl", "-s", "-X", "POST", url,
        "-H", "Content-Type: application/json",
        "-H", f"X-Sync-Key: {SYNC_API_KEY}",
        "-d", payload,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        resp = json.loads(result.stdout) if result.stdout else {}
        if resp.get("success"):
            print(f"  Pipeline run status synced (id={resp.get('id')})")
        else:
            print(f"  Pipeline run sync response: {resp}")
    except Exception as e:
        print(f"  Pipeline run sync error: {e}")


def run_monitor_only():
    """Run only the email monitoring phase (for separate scheduling)."""
    return run_email_monitor()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"
    dry_run = "--dry-run" in sys.argv

    if mode == "full":
        result = run_full_pipeline(dry_run=dry_run)
    elif mode == "apply":
        result = run_discovery_and_apply(dry_run=dry_run)
    elif mode == "monitor":
        result = run_monitor_only()
    else:
        print(f"Unknown mode: {mode}")
        print("Usage: python pipeline.py [full|apply|monitor] [--dry-run]")
        sys.exit(1)

    # Save results log
    log_path = os.path.join(os.path.dirname(__file__), "last_run.json")
    with open(log_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"\nResults saved to {log_path}")
