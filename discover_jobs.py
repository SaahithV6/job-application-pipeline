"""
Job Discovery Module — 3 strategies with fallback chain.
Strategy A: Browser Use → LinkedIn recommended jobs
Strategy B: LinkedIn Scraper → search-based discovery
Strategy C: Gmail → LinkedIn recommendation emails
"""

import json
import os
import re
import subprocess
import time
import db

BROWSER_USE_API_KEY = "bu_gah3-S_Tn03VHw8Sb_GEgREGdoGA_LOjV65e9ls7iHw"
BROWSER_USE_BASE = "https://api.browser-use.com/api/v3"


def _curl_json(method, url, data=None, headers=None):
    """Make an HTTP request and return parsed JSON."""
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


def _run_browser_task(task, output_schema=None, timeout_seconds=180):
    """Run a Browser Use task and poll for completion."""
    body = {"task": task}
    if output_schema:
        body["output_schema"] = output_schema
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
            }
    # Timeout - stop session
    _curl_json("POST", f"{BROWSER_USE_BASE}/sessions/{session_id}/stop",
               {"strategy": "session"}, _browser_use_headers())
    return {"error": "Task timed out", "session_id": session_id, "live_url": live_url}


def _pokee_skill(skill_call, params=None, timeout=660):
    """Execute a pokee-skill command. Unwraps the outer {data, status} envelope."""
    if params:
        param_json = json.dumps(params)
        cmd = f"pokee-skill {skill_call} <<'SKILLEOF'\n{param_json}\nSKILLEOF"
    else:
        cmd = f"pokee-skill {skill_call}"
    result = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=timeout)
    try:
        parsed = json.loads(result.stdout.strip())
        # Unwrap pokee-skill envelope: {data: {...}, status: "success"} -> inner data
        if "data" in parsed and isinstance(parsed["data"], dict):
            inner = parsed["data"]
            if inner.get("success") is None and parsed.get("status") == "success":
                inner["success"] = True
            return inner
        return parsed
    except json.JSONDecodeError:
        return {"success": False, "error": result.stdout[:500] + result.stderr[:500]}


# ── Strategy A: Browser Use → LinkedIn Recommended Jobs ──

def discover_via_browser_use(max_jobs=10):
    """Use Browser Use to navigate LinkedIn and extract recommended jobs."""
    task = f"""Go to https://www.linkedin.com/jobs/ and find the "Recommended for you" or
"Jobs you might be interested in" section. Extract up to {max_jobs} job listings.

For each job, click into the job listing to get the full description, then extract:
- job_title: the title of the position
- company: the company name
- location: job location
- job_url: the full LinkedIn URL for this job listing
- apply_url: if there's an external apply link, get that URL; otherwise use the LinkedIn job URL
- description: the FULL job description text including responsibilities, requirements, qualifications

Return the results as a JSON array of objects with those fields.
Do NOT apply to any jobs - just extract the information."""

    output_schema = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "job_title": {"type": "string"},
                "company": {"type": "string"},
                "location": {"type": "string"},
                "job_url": {"type": "string"},
                "apply_url": {"type": "string"},
                "description": {"type": "string"},
            },
        },
    }

    result = _run_browser_task(task, output_schema=output_schema, timeout_seconds=240)
    if result.get("error"):
        return {"success": False, "error": result["error"], "jobs": []}

    output = result.get("output", "")
    jobs = []
    if isinstance(output, list):
        jobs = output
    elif isinstance(output, str):
        try:
            parsed = json.loads(output)
            jobs = parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            # Try to extract JSON array from text
            match = re.search(r'\[.*\]', output, re.DOTALL)
            if match:
                try:
                    jobs = json.loads(match.group())
                except json.JSONDecodeError:
                    pass

    return {"success": len(jobs) > 0, "jobs": jobs, "source": "browser_use"}


# ── Strategy B: LinkedIn Scraper → Search-Based ──

def discover_via_scraper(keywords=None, location=None, max_jobs=10):
    """Use LinkedIn scraper to find jobs by search URL."""
    if not keywords:
        config = db.get_config()
        # Use work history or headline as keywords
        work = config.get("work_history_summary", "")
        if work:
            # Extract job titles from work history
            titles = []
            for line in work.split("\n")[:3]:
                match = re.match(r'^(.+?) at ', line)
                if match:
                    titles.append(match.group(1).strip())
            keywords = " ".join(titles[:2]) if titles else "software engineer"
        else:
            keywords = "software engineer"

    if not location:
        location = "United States"

    # Build LinkedIn search URL
    kw_encoded = keywords.replace(" ", "%20")
    loc_encoded = location.replace(" ", "%20")
    search_url = f"https://www.linkedin.com/jobs/search?keywords={kw_encoded}&location={loc_encoded}"

    result = _pokee_skill("linkedin_scraper.scrape_linkedin_jobs_by_search_url", {
        "urls": [search_url],
    })

    if not result.get("success"):
        return {"success": False, "error": result.get("error", "Scraper failed"), "jobs": []}

    raw_jobs = result.get("jobs", [])
    jobs = []
    for j in raw_jobs[:max_jobs]:
        jobs.append({
            "job_title": j.get("title", j.get("job_title", "")),
            "company": j.get("company", j.get("company_name", "")),
            "location": j.get("location", ""),
            "job_url": j.get("url", j.get("job_url", "")),
            "apply_url": j.get("apply_url", j.get("url", "")),
            "description": j.get("description", j.get("job_description", "")),
        })

    return {"success": len(jobs) > 0, "jobs": jobs, "source": "linkedin_scraper"}


# ── Strategy C: Gmail → LinkedIn Recommendation Emails ──

def discover_via_gmail(max_jobs=10):
    """Search Gmail for LinkedIn job recommendation emails and extract job links."""
    result = _pokee_skill("gmail.get_latest_gmail_threads", {
        "query": 'from:jobs-noreply@linkedin.com OR (from:linkedin.com subject:"jobs for you") newer_than:7d',
        "count": 10,
    })

    if not result.get("success"):
        return {"success": False, "error": result.get("error", "Gmail search failed"), "jobs": []}

    threads = result.get("list_of_gmail_threads", [])
    if not threads:
        return {"success": False, "error": "No LinkedIn job emails found", "jobs": []}

    # Extract job URLs from email bodies
    job_urls = set()
    for thread in threads:
        messages = thread.get("gmail_thread_messages", [])
        for msg in messages:
            body = msg.get("gmail_message_body", "")
            # Find LinkedIn job URLs
            urls = re.findall(r'https?://(?:www\.)?linkedin\.com/(?:jobs|comm/jobs)/[^\s"<>]+', body)
            job_urls.update(urls)

    if not job_urls:
        return {"success": False, "error": "No job URLs found in emails", "jobs": []}

    # Scrape the individual job URLs for details
    urls_to_scrape = list(job_urls)[:max_jobs]
    scrape_result = _pokee_skill("linkedin_scraper.scrape_linkedin_jobs_by_url", {
        "urls": urls_to_scrape,
    })

    jobs = []
    if scrape_result.get("success"):
        for j in scrape_result.get("jobs", []):
            jobs.append({
                "job_title": j.get("title", j.get("job_title", "")),
                "company": j.get("company", j.get("company_name", "")),
                "location": j.get("location", ""),
                "job_url": j.get("url", j.get("job_url", "")),
                "apply_url": j.get("apply_url", j.get("url", "")),
                "description": j.get("description", j.get("job_description", "")),
            })
    else:
        # Fallback: just return the URLs without scraping
        for url in urls_to_scrape:
            jobs.append({
                "job_title": "Unknown (from email)",
                "company": "Unknown",
                "location": "",
                "job_url": url,
                "apply_url": url,
                "description": "",
            })

    return {"success": len(jobs) > 0, "jobs": jobs, "source": "gmail"}


# ── Main discovery with fallback chain ──

def discover_jobs(max_jobs=None):
    """
    Run the discovery pipeline with fallback:
    1. Browser Use → LinkedIn recommended
    2. LinkedIn Scraper → search-based
    3. Gmail → LinkedIn emails

    Deduplicates against existing applications and respects daily limit.
    """
    if max_jobs is None:
        max_jobs = int(db.get_config("max_applications_per_day") or 5)

    remaining = max_jobs - db.today_application_count()
    if remaining <= 0:
        return {
            "success": False,
            "message": "Daily application limit reached",
            "jobs": [],
            "applied_today": db.today_application_count(),
        }

    all_results = []

    # Strategy A: Browser Use
    print("Strategy A: Trying Browser Use for LinkedIn recommended jobs...")
    result_a = discover_via_browser_use(max_jobs=remaining + 5)  # Get extras for dedup
    if result_a["success"]:
        all_results.extend(result_a["jobs"])
        print(f"  Found {len(result_a['jobs'])} jobs via Browser Use")
    else:
        print(f"  Browser Use failed: {result_a.get('error', 'unknown')}")

    # Strategy B: LinkedIn Scraper (if we need more)
    if len(all_results) < remaining:
        print("Strategy B: Trying LinkedIn Scraper...")
        result_b = discover_via_scraper(max_jobs=remaining + 5)
        if result_b["success"]:
            all_results.extend(result_b["jobs"])
            print(f"  Found {len(result_b['jobs'])} jobs via scraper")
        else:
            print(f"  Scraper failed: {result_b.get('error', 'unknown')}")

    # Strategy C: Gmail (if we still need more)
    if len(all_results) < remaining:
        print("Strategy C: Trying Gmail for LinkedIn emails...")
        result_c = discover_via_gmail(max_jobs=remaining + 5)
        if result_c["success"]:
            all_results.extend(result_c["jobs"])
            print(f"  Found {len(result_c['jobs'])} jobs via Gmail")
        else:
            print(f"  Gmail failed: {result_c.get('error', 'unknown')}")

    # Deduplicate against existing applications
    unique_jobs = []
    seen_urls = set()
    for job in all_results:
        url = job.get("job_url", "")
        if url and url in seen_urls:
            continue
        if db.is_duplicate(url):
            continue
        seen_urls.add(url)
        unique_jobs.append(job)
        if len(unique_jobs) >= remaining:
            break

    return {
        "success": len(unique_jobs) > 0,
        "jobs": unique_jobs,
        "total_found": len(all_results),
        "after_dedup": len(unique_jobs),
        "remaining_today": remaining,
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        strategy = sys.argv[1]
        if strategy == "browser":
            print(json.dumps(discover_via_browser_use(), indent=2))
        elif strategy == "scraper":
            print(json.dumps(discover_via_scraper(), indent=2))
        elif strategy == "gmail":
            print(json.dumps(discover_via_gmail(), indent=2))
        else:
            print(json.dumps(discover_jobs(), indent=2))
    else:
        print(json.dumps(discover_jobs(), indent=2))
