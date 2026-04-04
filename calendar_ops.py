"""
Google Calendar Integration Module.
Creates calendar events for interviews and offer deadlines.
"""

import json
import subprocess
import re
from datetime import datetime, timedelta
import db


def _pokee_skill(skill_call, params=None, timeout=660):
    if params:
        param_json = json.dumps(params)
        cmd = f"pokee-skill {skill_call} <<'SKILLEOF'\n{param_json}\nSKILLEOF"
    else:
        cmd = f"pokee-skill {skill_call}"
    result = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=timeout)
    try:
        parsed = json.loads(result.stdout.strip())
        if "data" in parsed and isinstance(parsed["data"], dict):
            inner = parsed["data"]
            if inner.get("success") is None and parsed.get("status") == "success":
                inner["success"] = True
            return inner
        return parsed
    except json.JSONDecodeError:
        return {"success": False, "error": result.stdout[:500] + result.stderr[:500]}


# Timezone mapping for common abbreviations
TZ_MAP = {
    "PST": "America/Los_Angeles",
    "PDT": "America/Los_Angeles",
    "MST": "America/Denver",
    "MDT": "America/Denver",
    "CST": "America/Chicago",
    "CDT": "America/Chicago",
    "EST": "America/New_York",
    "EDT": "America/New_York",
    "GMT": "Europe/London",
    "UTC": "UTC",
}

TZ_OFFSETS = {
    "America/Los_Angeles": "-07:00",
    "America/Denver": "-06:00",
    "America/Chicago": "-05:00",
    "America/New_York": "-04:00",
    "Europe/London": "+00:00",
    "Europe/Paris": "+01:00",
    "Europe/Berlin": "+01:00",
    "Asia/Tokyo": "+09:00",
    "Asia/Shanghai": "+08:00",
    "Asia/Kolkata": "+05:30",
    "Australia/Sydney": "+10:00",
    "UTC": "+00:00",
}


def get_user_timezone():
    return db.get_config("user_timezone") or "America/Los_Angeles"


def get_tz_offset(tz_name):
    return TZ_OFFSETS.get(tz_name, "-07:00")


def create_interview_event(app_id, interview_details):
    """
    Create a Google Calendar event for an interview.

    Args:
        app_id: application ID
        interview_details: dict with date_str, time_str, meeting_link, interviewer, etc.
    """
    application = db.get_application(app_id)
    if not application:
        return {"success": False, "error": "Application not found"}

    company = application["company"]
    role = application["role"]
    tz = get_user_timezone()
    offset = get_tz_offset(tz)

    # Build event times
    date_str = interview_details.get("date_str", "")
    time_str = interview_details.get("time_str", "")

    if date_str and time_str:
        # Try to parse the date/time into RFC3339
        start_time = f"{date_str}T{time_str}:00{offset}"
        # Default 1 hour interview
        end_time_parts = time_str.split(":")
        if len(end_time_parts) >= 2:
            end_hour = int(end_time_parts[0]) + 1
            end_time = f"{date_str}T{end_hour:02d}:{end_time_parts[1]}:00{offset}"
        else:
            end_time = start_time  # fallback
    else:
        # No specific time - create an all-day event for tomorrow as placeholder
        tomorrow = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")
        start_time = f"{tomorrow}T09:00:00{offset}"
        end_time = f"{tomorrow}T10:00:00{offset}"

    # Build description
    meeting_link = interview_details.get("meeting_link", application.get("interview_link", ""))
    interviewer = interview_details.get("interviewer", "")

    description_parts = [f"Interview for {role} at {company}"]
    if meeting_link:
        description_parts.append(f"\nMeeting Link: {meeting_link}")
    if interviewer:
        description_parts.append(f"Interviewer: {interviewer}")
    if application.get("job_url"):
        description_parts.append(f"\nJob Listing: {application['job_url']}")
    description_parts.append("\n---\nPrepared by Job Application Pipeline")

    description = "\n".join(description_parts)

    # Create the calendar event
    event_params = {
        "summary": f"Interview: {company} - {role}",
        "start_time": start_time,
        "end_time": end_time,
        "time_zone": tz,
        "description": description,
    }

    # Add Google Meet if no other meeting link
    if not meeting_link:
        event_params["add_video_meeting"] = True

    # Add location if meeting link exists
    if meeting_link:
        event_params["location"] = meeting_link

    result = _pokee_skill("google_calendar.create_google_calendar_event", event_params)

    if result.get("success"):
        event_id = result.get("google_calendar_event_id", "")
        video_url = result.get("video_meeting_url", meeting_link)

        # Update the application with calendar info
        db.update_application(
            app_id,
            calendar_event_id=event_id,
            interview_link=video_url or meeting_link,
            interview_date=start_time,
        )

        return {
            "success": True,
            "event_id": event_id,
            "event_link": result.get("html_link", ""),
            "video_url": video_url,
            "start_time": start_time,
            "end_time": end_time,
        }

    return {"success": False, "error": result.get("error", "Calendar event creation failed")}


def create_offer_deadline_event(app_id, deadline_date=None):
    """Create a calendar event for an offer deadline."""
    application = db.get_application(app_id)
    if not application:
        return {"success": False, "error": "Application not found"}

    company = application["company"]
    role = application["role"]
    tz = get_user_timezone()
    offset = get_tz_offset(tz)

    if not deadline_date:
        # Default: 1 week from now
        deadline_date = (datetime.utcnow() + timedelta(days=7)).strftime("%Y-%m-%d")

    result = _pokee_skill("google_calendar.create_google_calendar_event", {
        "summary": f"Offer Deadline: {company} - {role}",
        "start_time": f"{deadline_date}T09:00:00{offset}",
        "end_time": f"{deadline_date}T09:30:00{offset}",
        "time_zone": tz,
        "description": f"Deadline to respond to offer from {company} for {role}.\n\nJob: {application.get('job_url', 'N/A')}",
        "list_of_reminders": [
            {"method": "popup", "minutes": 1440},  # 1 day before
            {"method": "email", "minutes": 2880},   # 2 days before
        ],
    })

    if result.get("success"):
        db.update_application(app_id, calendar_event_id=result.get("google_calendar_event_id", ""))
        return {"success": True, "event_link": result.get("html_link", "")}

    return {"success": False, "error": result.get("error", "Failed")}


def process_interviews(interview_data_list):
    """
    Process a list of interview findings from the email monitor
    and create calendar events for each.
    """
    results = []
    for interview in interview_data_list:
        app_id = interview["application_id"]
        details = interview.get("details", {})
        classification = interview.get("classification", "interview")

        if classification == "self_schedule":
            # Don't create calendar event for self-schedule - user picks time
            db.update_application(app_id, status="self_schedule")
            results.append({
                "application_id": app_id,
                "action": "self_schedule",
                "scheduling_link": details.get("scheduling_link", ""),
            })
        else:
            result = create_interview_event(app_id, details)
            results.append({
                "application_id": app_id,
                "action": "calendar_event_created",
                **result,
            })

    return results


if __name__ == "__main__":
    print("Calendar ops module loaded. Use process_interviews() or create_interview_event().")
