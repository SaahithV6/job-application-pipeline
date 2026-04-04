"""
Email Monitoring & Status Updates Module.
Searches Gmail for application-related emails, classifies them,
and updates the application tracker accordingly.
"""

import json
import os
import re
import subprocess
from datetime import datetime
import db


def _pokee_skill(skill_call, params=None, timeout=660):
    if params:
        param_json = json.dumps(params)
        cmd = f"pokee-skill {skill_call} <<'SKILLEOF'\n{param_json}\nSKILLEOF"
    else:
        cmd = f"pokee-skill {skill_call}"
    result = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=timeout)
    try:
        return json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return {"success": False, "error": result.stdout[:500] + result.stderr[:500]}


# ── Email Classification ──

REJECTION_KEYWORDS = [
    "unfortunately", "not moving forward", "other candidates",
    "decided not to proceed", "not selected", "position has been filled",
    "will not be moving forward", "regret to inform", "unable to offer",
    "after careful consideration", "decided to pursue other",
    "not a match", "not the right fit",
]

INTERVIEW_KEYWORDS = [
    "interview", "schedule a call", "meet the team",
    "phone screen", "technical assessment", "coding challenge",
    "on-site", "virtual interview", "video call",
    "would like to speak", "next steps in the process",
    "availability for", "book a time", "calendly",
    "hiring manager would like",
]

OFFER_KEYWORDS = [
    "offer letter", "pleased to offer", "congratulations",
    "compensation package", "start date", "we'd like to extend",
    "formal offer", "offer of employment", "salary",
    "excited to welcome you",
]

CONFIRMATION_KEYWORDS = [
    "application received", "thank you for applying",
    "we received your application", "application has been submitted",
    "confirming your application", "successfully applied",
]

SCHEDULING_KEYWORDS = [
    "pick a time", "select a time", "choose a slot",
    "scheduling link", "calendly.com", "scheduler",
    "book your interview", "self-schedule",
]


def classify_email(subject, body):
    """Classify an email into a status category."""
    text = f"{subject} {body}".lower()

    # Check for offer first (most important)
    offer_score = sum(1 for kw in OFFER_KEYWORDS if kw in text)
    if offer_score >= 2:
        return "offer"

    # Interview
    interview_score = sum(1 for kw in INTERVIEW_KEYWORDS if kw in text)
    scheduling_score = sum(1 for kw in SCHEDULING_KEYWORDS if kw in text)
    if interview_score >= 2 or (interview_score >= 1 and scheduling_score >= 1):
        if scheduling_score >= 1:
            return "self_schedule"
        return "interview"

    # Rejection
    rejection_score = sum(1 for kw in REJECTION_KEYWORDS if kw in text)
    if rejection_score >= 1:
        return "rejected"

    # Confirmation (doesn't change status, just logged)
    confirm_score = sum(1 for kw in CONFIRMATION_KEYWORDS if kw in text)
    if confirm_score >= 1:
        return "confirmation"

    return "unknown"


def extract_interview_details(subject, body):
    """Extract interview date, time, and meeting link from email."""
    text = f"{subject}\n{body}"
    details = {}

    # Extract meeting links
    zoom_match = re.search(r'https://[^\s]*zoom\.us/[^\s<>"]+', text)
    teams_match = re.search(r'https://teams\.microsoft\.com/[^\s<>"]+', text)
    meet_match = re.search(r'https://meet\.google\.com/[^\s<>"]+', text)
    calendly_match = re.search(r'https://calendly\.com/[^\s<>"]+', text)
    generic_link = re.search(r'https://[^\s<>"]*(?:interview|meeting|call)[^\s<>"]*', text, re.IGNORECASE)

    if zoom_match:
        details["meeting_link"] = zoom_match.group()
    elif teams_match:
        details["meeting_link"] = teams_match.group()
    elif meet_match:
        details["meeting_link"] = meet_match.group()
    elif calendly_match:
        details["scheduling_link"] = calendly_match.group()
    elif generic_link:
        details["meeting_link"] = generic_link.group()

    # Extract date patterns (various formats)
    date_patterns = [
        r'(\w+ \d{1,2},?\s*\d{4})\s+at\s+(\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?)',
        r'(\d{1,2}/\d{1,2}/\d{2,4})\s+at\s+(\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?)',
        r'(\d{4}-\d{2}-\d{2})(?:T|\s+)(\d{2}:\d{2})',
    ]
    for pattern in date_patterns:
        match = re.search(pattern, text)
        if match:
            details["date_str"] = match.group(1)
            details["time_str"] = match.group(2)
            break

    # Extract interviewer name
    interviewer_match = re.search(
        r'(?:with|meet|interviewer[:]?)\s+([A-Z][a-z]+ [A-Z][a-z]+)', text
    )
    if interviewer_match:
        details["interviewer"] = interviewer_match.group(1)

    return details


def match_email_to_application(subject, body, from_email):
    """Try to match an email to an existing application by company name."""
    applications = db.list_applications(limit=500)
    text = f"{subject} {body} {from_email}".lower()

    best_match = None
    best_score = 0

    for app in applications:
        score = 0
        company = app["company"].lower()
        role = app["role"].lower()

        # Company name in email
        if company in text:
            score += 3
        # Partial company match (first word)
        company_first = company.split()[0] if company.split() else ""
        if company_first and len(company_first) > 3 and company_first in text:
            score += 1
        # Role in email
        if role in text:
            score += 2
        # Company in from email domain
        company_domain = company.replace(" ", "").replace(",", "").replace(".", "")
        if company_domain in from_email.lower().replace(".", ""):
            score += 2

        if score > best_score:
            best_score = score
            best_match = app

    return best_match if best_score >= 2 else None


# ── Main Monitor ──

def monitor_emails():
    """
    Search Gmail for application-related emails and update tracker.
    Returns a summary of actions taken.
    """
    results = {
        "emails_checked": 0,
        "status_updates": [],
        "interviews_found": [],
        "unmatched": [],
        "ghosted_count": 0,
    }

    # Search for application-related emails — limited to last 50 threads max
    queries = [
        '(subject:application OR subject:interview OR subject:offer OR subject:"thank you for applying") newer_than:1d',
        '(subject:rejected OR subject:unfortunately OR subject:congratulations OR subject:"next steps") newer_than:1d',
        '(from:workday.com OR from:greenhouse.io OR from:lever.co OR from:smartrecruiters.com) newer_than:1d',
    ]

    all_threads = []
    seen_thread_ids = set()
    MAX_THREADS = 50

    for query in queries:
        if len(all_threads) >= MAX_THREADS:
            break
        remaining = MAX_THREADS - len(all_threads)
        result = _pokee_skill("gmail.get_latest_gmail_threads", {
            "query": query,
            "count": min(remaining, 20),
        })
        if result.get("success"):
            for thread in result.get("list_of_gmail_threads", []):
                tid = thread.get("gmail_thread_id")
                if tid and tid not in seen_thread_ids:
                    seen_thread_ids.add(tid)
                    all_threads.append(thread)

    results["emails_checked"] = len(all_threads)

    for thread in all_threads:
        subject = thread.get("gmail_thread_subject", "")
        messages = thread.get("gmail_thread_messages", [])
        thread_id = thread.get("gmail_thread_id", "")

        # Get the latest message body
        body = ""
        from_email = ""
        if messages:
            latest = messages[-1]
            body = latest.get("gmail_message_body", "")
            from_email = latest.get("gmail_message_from_email", "")

        # Classify
        classification = classify_email(subject, body)
        if classification == "unknown" or classification == "confirmation":
            continue

        # Match to application
        matched_app = match_email_to_application(subject, body, from_email)
        if not matched_app:
            results["unmatched"].append({
                "subject": subject,
                "from": from_email,
                "classification": classification,
            })
            continue

        # Update application status
        update_data = {
            "status": classification,
            "confirmation_email_thread_id": thread_id,
        }

        if classification in ("interview", "self_schedule"):
            details = extract_interview_details(subject, body)
            if details.get("meeting_link"):
                update_data["interview_link"] = details["meeting_link"]
            if details.get("date_str"):
                update_data["interview_date"] = f"{details['date_str']} {details.get('time_str', '')}"

            results["interviews_found"].append({
                "application_id": matched_app["id"],
                "company": matched_app["company"],
                "role": matched_app["role"],
                "details": details,
                "classification": classification,
                "thread_id": thread_id,
            })

        db.update_application(matched_app["id"], **update_data)
        results["status_updates"].append({
            "application_id": matched_app["id"],
            "company": matched_app["company"],
            "role": matched_app["role"],
            "old_status": matched_app["status"],
            "new_status": classification,
        })

    # Mark ghosted applications
    ghosted_count = db.mark_ghosted()
    results["ghosted_count"] = ghosted_count

    return results


if __name__ == "__main__":
    result = monitor_emails()
    print(json.dumps(result, indent=2, default=str))
