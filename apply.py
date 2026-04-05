"""
Job Application Engine — Uses Browser Use to fill and submit job applications.
Generates tailored cover letters from resume + job description, saves them locally,
and instructs Browser Use to upload both resume and cover letter on the job site.
"""

import json
import re
import subprocess
import time
import os
import db
from cover_letter import generate_cover_letter, save_cover_letter

BROWSER_USE_API_KEY = "bu_gah3-S_Tn03VHw8Sb_GEgREGdoGA_LOjV65e9ls7iHw"
BROWSER_USE_BASE = "https://api.browser-use.com/api/v3"


def _curl_json(method, url, data=None, headers=None):
    cmd = ["curl", "-s", "-X", method, url]
    if headers:
        for k, v in headers.items():
            cmd.extend(["-H", f"{k}: {v}"])
    if data:
        cmd.extend(["-H", "Content-Type: application/json", "-d", json.dumps(data)])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": result.stdout[:500]}


def _browser_use_headers():
    return {"X-Browser-Use-API-Key": BROWSER_USE_API_KEY}


def _upload_to_browser_use_workspace(file_path, workspace_id=None):
    """
    Upload a file to Browser Use workspace so the browser agent can access it.
    Returns the workspace file reference.
    """
    if not os.path.exists(file_path):
        return None

    # Create workspace if needed
    if not workspace_id:
        resp = _curl_json("POST", f"{BROWSER_USE_BASE}/workspaces", {}, _browser_use_headers())
        workspace_id = resp.get("id")
        if not workspace_id:
            return None

    # Upload file to workspace
    filename = os.path.basename(file_path)
    cmd = [
        "curl", "-s", "-X", "POST",
        f"{BROWSER_USE_BASE}/workspaces/{workspace_id}/files",
        "-H", f"X-Browser-Use-API-Key: {BROWSER_USE_API_KEY}",
        "-F", f"file=@{file_path}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    try:
        data = json.loads(result.stdout)
        return {"workspace_id": workspace_id, "filename": filename, "response": data}
    except json.JSONDecodeError:
        return {"workspace_id": workspace_id, "filename": filename, "error": result.stdout[:300]}


def _run_browser_task(task, timeout_seconds=300, workspace_id=None):
    """Run a Browser Use task and poll for completion."""
    body = {"task": task}
    if workspace_id:
        body["workspace_id"] = workspace_id
    resp = _curl_json("POST", f"{BROWSER_USE_BASE}/sessions", body, _browser_use_headers())
    session_id = resp.get("id")
    if not session_id:
        return {"error": "Failed to create session", "response": resp}

    live_url = resp.get("liveUrl", "")
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        time.sleep(5)
        status_resp = _curl_json("GET", f"{BROWSER_USE_BASE}/sessions/{session_id}", headers=_browser_use_headers())
        status = status_resp.get("status", "")
        if status in ("idle", "stopped", "error", "timed_out"):
            return {
                "session_id": session_id,
                "live_url": live_url,
                "status": status,
                "output": status_resp.get("output", ""),
                "success": status_resp.get("isTaskSuccessful", False),
                "cost": status_resp.get("totalCostUsd", "0"),
                "full_response": status_resp,
            }
    _curl_json("POST", f"{BROWSER_USE_BASE}/sessions/{session_id}/stop",
               {"strategy": "session"}, _browser_use_headers())
    return {"error": "Task timed out", "session_id": session_id, "live_url": live_url}


def _read_resume_text(resume_path):
    """Read resume text for cover letter generation."""
    if not resume_path or not os.path.exists(resume_path):
        return ""
    param_json = json.dumps({"file_path": resume_path})
    cmd = f"pokee-skill file.read_file_from_pokee_storage <<'SKILLEOF'\n{param_json}\nSKILLEOF"
    result = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=120)
    try:
        data = json.loads(result.stdout.strip())
        inner = data.get("data", data)
        if inner.get("success"):
            return inner.get("content", inner.get("text", ""))
    except Exception:
        pass
    try:
        with open(resume_path, "r", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


def build_application_prompt(job, config, cover_letter_path=None, resume_path=None):
    """
    Build the Browser Use task prompt for filling a job application.
    Includes instructions to upload resume and cover letter files.
    """
    name = config.get("user_name", "")
    email = config.get("user_email", "")
    phone = config.get("user_phone", "")
    linkedin = config.get("linkedin_profile_url", "")
    work_history = config.get("work_history_summary", "")
    education = config.get("education_summary", "")

    apply_url = job.get("apply_url") or job.get("job_url", "")
    company = job.get("company", "Unknown Company")
    role = job.get("job_title", "Unknown Role")

    prompt = f"""Navigate to this job application URL: {apply_url}

You are applying for the position of "{role}" at "{company}".

Fill out the entire job application form with the following information:

PERSONAL INFORMATION:
- Full Name: {name}
- Email: {email}
- Phone: {phone}
- LinkedIn Profile: {linkedin}

WORK EXPERIENCE:
{work_history if work_history else "Not provided - skip or enter N/A if required"}

EDUCATION:
{education if education else "Not provided - skip or enter N/A if required"}

FILE UPLOADS — THIS IS CRITICAL:
"""

    if resume_path:
        prompt += f"""
- RESUME: When you see a "Resume" or "CV" upload field, click the upload/attach button
  and upload the file. The resume file is available in your workspace as "{os.path.basename(resume_path)}".
  Look for file input elements (type="file"), "Upload Resume", "Attach Resume", "Choose File", etc.
  Click it and select the resume file.
"""

    if cover_letter_path:
        prompt += f"""
- COVER LETTER: When you see a "Cover Letter" upload field or text area:
  - If it's a FILE UPLOAD field: click the upload button and upload the cover letter file
    available in your workspace as "{os.path.basename(cover_letter_path)}".
  - If it's a TEXT AREA / text box for cover letter: paste the following cover letter text into it.
  - If there is NO cover letter field, skip this step.
"""

    prompt += f"""
STEP-BY-STEP INSTRUCTIONS:
1. If the page has an "Apply", "Easy Apply", or "Apply Now" button, click it first
2. Fill in ALL required fields using the personal information above
3. Upload the resume file when you see the resume upload field
4. Upload the cover letter file OR paste the cover letter text when you see that field
5. For required fields without matching data, use reasonable defaults
6. If the site requires creating an account or logging in (Workday, Greenhouse, Lever, etc.):
   - Try to proceed as a guest if possible
   - If login is required, use the email {email} and try common flows
   - If OAuth (Google/LinkedIn sign-in) is available, prefer that
7. For salary expectations, select "Prefer not to say" or leave blank if optional
8. For "How did you hear about us", select "LinkedIn" or "Job Board"
9. For work authorization questions, select "Yes" if applicable
10. For sponsorship questions, if unclear, skip or select "No"

IMPORTANT: Be thorough. Fill every required field. Upload files where requested.
If you encounter CAPTCHAs or verification that blocks progress, stop and report it.
"""

    return prompt


def apply_to_job(job, dry_run=False):
    """
    Apply to a single job using Browser Use.
    1. Reads resume text
    2. Generates tailored cover letter from resume + job description
    3. Saves cover letter as a file
    4. Uploads resume + cover letter to Browser Use workspace
    5. Instructs Browser Use to fill the form and upload both files
    """
    if not db.can_apply_today():
        return {"success": False, "error": "Daily application limit reached"}

    config = db.get_config()
    apply_url = job.get("apply_url") or job.get("job_url", "")
    if not apply_url:
        return {"success": False, "error": "No apply URL provided"}

    if db.is_duplicate(job.get("job_url", "")):
        return {"success": False, "error": "Already applied to this job"}

    resume_path = config.get("resume_path", "")
    company = job.get("company", "Unknown")
    role = job.get("job_title", "Unknown")
    job_description = job.get("description", "")

    print(f"Applying to {company} - {role}...")

    # ── Step 1: Read resume for cover letter context ──
    resume_text = ""
    if resume_path:
        print(f"  Reading resume from {resume_path}...")
        resume_text = _read_resume_text(resume_path)
        if resume_text:
            print(f"  Resume loaded: {len(resume_text)} chars")
        else:
            print(f"  Warning: Could not read resume, using stored profile data")

    # ── Step 2: Generate tailored cover letter ──
    print(f"  Generating cover letter tailored to {company} - {role}...")
    cover_letter_text = generate_cover_letter(
        company=company,
        role=role,
        job_description=job_description,
        resume_text=resume_text if resume_text else None,
    )
    cover_letter_path = save_cover_letter(company, role, cover_letter_text)
    print(f"  Cover letter saved: {cover_letter_path}")

    # ── Step 3: Upload files to Browser Use workspace ──
    workspace_id = None
    if resume_path and os.path.exists(resume_path):
        print(f"  Uploading resume to Browser Use workspace...")
        upload_result = _upload_to_browser_use_workspace(resume_path)
        if upload_result and upload_result.get("workspace_id"):
            workspace_id = upload_result["workspace_id"]
            print(f"  Resume uploaded to workspace {workspace_id}")

    if cover_letter_path and os.path.exists(cover_letter_path):
        print(f"  Uploading cover letter to Browser Use workspace...")
        _upload_to_browser_use_workspace(cover_letter_path, workspace_id=workspace_id)
        print(f"  Cover letter uploaded")

    # ── Step 4: Build prompt and run Browser Use ──
    prompt = build_application_prompt(
        job, config,
        cover_letter_path=cover_letter_path,
        resume_path=resume_path,
    )

    # Always include cover letter text as fallback for text-area fields
    prompt += f"\n\nCOVER LETTER TEXT (use this if there is a text area for cover letter):\n---\n{cover_letter_text}\n---"

    if dry_run:
        prompt += "\n\nDRY RUN: Do NOT click submit. Stop at the review step and report what fields you filled and what files you uploaded."
    else:
        prompt += "\n\nAfter filling all fields, uploading files, and reviewing, click the Submit/Apply button to submit the application."

    print(f"  Launching Browser Use session...")
    result = _run_browser_task(prompt, timeout_seconds=300, workspace_id=workspace_id)

    # ── Sync live URL to dashboard so user can watch ──
    if result.get("session_id") or result.get("live_url"):
        try:
            from pipeline import sync_applying_job
            sync_applying_job(
                job_url=job.get("job_url", ""),
                browser_session_id=result.get("session_id", ""),
                browser_live_url=result.get("live_url", ""),
            )
            if result.get("live_url"):
                print(f"  Watch live: {result['live_url']}")
        except Exception:
            pass  # Non-critical — don't break the apply flow

    if result.get("error"):
        return {
            "success": False,
            "error": result["error"],
            "session_id": result.get("session_id"),
            "live_url": result.get("live_url"),
            "cover_letter_path": cover_letter_path,
        }

    # ── Step 5: Record the application ──
    app_id = db.add_application(
        company=company,
        role=role,
        job_url=job.get("job_url"),
        apply_url=apply_url,
        browser_session_id=result.get("session_id"),
        browser_live_url=result.get("live_url"),
        notes=f"Cover letter: {cover_letter_path}\nBrowser Use output: {str(result.get('output', ''))[:400]}",
    )

    return {
        "success": result.get("success", False),
        "application_id": app_id,
        "session_id": result.get("session_id"),
        "live_url": result.get("live_url"),
        "output": result.get("output", ""),
        "cost": result.get("cost", "0"),
        "cover_letter_path": cover_letter_path,
        "dry_run": dry_run,
    }


def apply_to_jobs(jobs, dry_run=False):
    """Apply to a list of jobs, respecting the daily limit."""
    results = []
    for job in jobs:
        if not db.can_apply_today():
            results.append({
                "job": job,
                "success": False,
                "error": "Daily limit reached",
            })
            break

        result = apply_to_job(job, dry_run=dry_run)
        results.append({"job": job, **result})

        if result.get("success"):
            time.sleep(3)

    applied = sum(1 for r in results if r.get("success"))
    failed = sum(1 for r in results if not r.get("success"))
    return {"total": len(results), "applied": applied, "failed": failed, "results": results}


def run_pipeline(dry_run=False):
    """Full pipeline: discover jobs → apply to them."""
    from discover_jobs import discover_jobs

    print("=== Job Application Pipeline ===")
    print(f"Daily limit: {db.get_config('max_applications_per_day')}")
    print(f"Applied today: {db.today_application_count()}")
    print()

    print("Step 1: Discovering jobs...")
    discovery = discover_jobs()
    if not discovery["success"]:
        return {"success": False, "phase": "discovery", "error": discovery.get("message", "No jobs found")}

    jobs = discovery["jobs"]
    print(f"Found {len(jobs)} new jobs to apply to")
    print()

    print("Step 2: Applying to jobs...")
    results = apply_to_jobs(jobs, dry_run=dry_run)

    print()
    print(f"=== Results: {results['applied']} applied, {results['failed']} failed ===")
    return {"success": True, "discovery": discovery, "applications": results}


if __name__ == "__main__":
    import sys
    dry_run = "--dry-run" in sys.argv
    result = run_pipeline(dry_run=dry_run)
    print(json.dumps(result, indent=2, default=str))
