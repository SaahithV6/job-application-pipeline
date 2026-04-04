"""
Configuration/Setup module for user onboarding.
Handles resume parsing, LinkedIn profile extraction, and user config.
"""

import json
import os
import subprocess
import re
import db


def run_pokee_skill(skill_call, params=None, timeout=660):
    """Execute a pokee-skill command and return parsed JSON result."""
    if params:
        param_json = json.dumps(params)
        cmd = f"pokee-skill {skill_call} <<'SKILLEOF'\n{param_json}\nSKILLEOF"
    else:
        cmd = f"pokee-skill {skill_call}"
    result = subprocess.run(
        ["bash", "-c", cmd],
        capture_output=True, text=True,
        timeout=timeout,
    )
    output = result.stdout.strip()
    if not output:
        return {"success": False, "error": result.stderr.strip() or "No output"}
    try:
        parsed = json.loads(output)
        if "data" in parsed and isinstance(parsed["data"], dict):
            inner = parsed["data"]
            if inner.get("success") is None and parsed.get("status") == "success":
                inner["success"] = True
            return inner
        return parsed
    except json.JSONDecodeError:
        return {"success": False, "error": output[:500]}


def get_linkedin_profile():
    """Fetch the connected user's LinkedIn profile details."""
    result = run_pokee_skill("linkedin.get_linkedin_user_detail")
    if result.get("success"):
        return {
            "user_id": result.get("linkedin_user_id", ""),
            "name": result.get("name", ""),
            "email": result.get("email", ""),
        }
    return None


def scrape_linkedin_profile(profile_url):
    """Scrape a full LinkedIn profile for work history, education, skills."""
    api_url = os.environ.get("POKEE_MAIN_API_URL", "")
    if not api_url:
        return None
    cmd = f"""curl -s -X POST "{api_url}/linkedin-scrape" \
      -H "Content-Type: application/json" \
      -d '{{"url": "{profile_url}"}}' """
    result = subprocess.run(
        ["bash", "-c", cmd],
        capture_output=True, text=True,
        timeout=300,
    )
    try:
        data = json.loads(result.stdout)
        if data.get("success"):
            return data.get("profile", {})
    except json.JSONDecodeError:
        pass
    return None


def extract_profile_data(profile):
    """Extract work history and education summaries from scraped profile."""
    work_history = []
    for exp in profile.get("experience", []):
        title = exp.get("title", "")
        company = exp.get("company", "")
        duration = exp.get("duration", "")
        desc = exp.get("description", "")
        entry = f"{title} at {company}"
        if duration:
            entry += f" ({duration})"
        if desc:
            entry += f" - {desc[:200]}"
        work_history.append(entry)

    education = []
    for edu in profile.get("education", []):
        school = edu.get("school", "")
        degree = edu.get("degree", "")
        field = edu.get("field_of_study", "")
        entry = f"{degree} in {field} from {school}" if field else f"{degree} from {school}"
        education.append(entry)

    skills = [s.get("name", s) if isinstance(s, dict) else str(s)
              for s in profile.get("skills", [])]

    return {
        "work_history": "\n".join(work_history),
        "education": "\n".join(education),
        "skills": ", ".join(skills[:20]),
        "headline": profile.get("header", {}).get("headline", ""),
        "summary": profile.get("about", ""),
    }


def read_resume_text(resume_path):
    """Extract text from a resume PDF using pokee-file skill."""
    result = run_pokee_skill("file.read_file_from_pokee_storage", {
        "file_path": resume_path,
    })
    if result.get("success"):
        return result.get("content", result.get("text", ""))
    return ""


def parse_resume_fields(resume_text):
    """Extract key fields from resume text for form filling."""
    # Simple heuristic extraction - the text will be used as context for Browser Use
    lines = resume_text.strip().split("\n")
    # Try to find email
    email = ""
    phone = ""
    for line in lines:
        if not email:
            match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', line)
            if match:
                email = match.group()
        if not phone:
            match = re.search(r'[\(]?\d{3}[\)]?[\s.-]?\d{3}[\s.-]?\d{4}', line)
            if match:
                phone = match.group()
    return {
        "email_from_resume": email,
        "phone_from_resume": phone,
        "full_text": resume_text,
    }


def setup_user(resume_path=None, linkedin_profile_url=None, user_email=None,
               user_name=None, user_phone=None, user_timezone=None):
    """
    Full user onboarding:
    1. Set basic config
    2. Pull LinkedIn profile if available
    3. Parse resume if provided
    4. Merge data and save to config
    """
    results = {"steps": []}

    # Basic config
    if user_email:
        db.set_config("user_email", user_email)
    if user_name:
        db.set_config("user_name", user_name)
    if user_phone:
        db.set_config("user_phone", user_phone)
    if user_timezone:
        db.set_config("user_timezone", user_timezone)
    if resume_path:
        db.set_config("resume_path", resume_path)
    if linkedin_profile_url:
        db.set_config("linkedin_profile_url", linkedin_profile_url)

    # LinkedIn profile fetch
    if linkedin_profile_url:
        results["steps"].append("Fetching LinkedIn profile...")
        profile = scrape_linkedin_profile(linkedin_profile_url)
        if profile:
            extracted = extract_profile_data(profile)
            db.set_config("work_history_summary", extracted["work_history"])
            db.set_config("education_summary", extracted["education"])
            if not user_name and profile.get("header", {}).get("name"):
                db.set_config("user_name", profile["header"]["name"])
            results["linkedin_profile"] = extracted
            results["steps"].append("LinkedIn profile scraped successfully")
        else:
            results["steps"].append("LinkedIn scrape failed, trying API...")
            api_profile = get_linkedin_profile()
            if api_profile:
                if not user_name and api_profile.get("name"):
                    db.set_config("user_name", api_profile["name"])
                if not user_email and api_profile.get("email"):
                    db.set_config("user_email", api_profile["email"])
                results["steps"].append("LinkedIn API profile fetched")

    # Resume parsing
    if resume_path and os.path.exists(resume_path):
        results["steps"].append("Parsing resume...")
        resume_text = read_resume_text(resume_path)
        if resume_text:
            fields = parse_resume_fields(resume_text)
            if fields["email_from_resume"] and not user_email:
                db.set_config("user_email", fields["email_from_resume"])
            if fields["phone_from_resume"] and not user_phone:
                db.set_config("user_phone", fields["phone_from_resume"])
            results["resume_parsed"] = True
            results["steps"].append("Resume parsed successfully")
        else:
            results["steps"].append("Could not extract text from resume")

    results["config"] = db.get_config()
    return results


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        print("Testing setup module...")
        print("LinkedIn profile fetch:", get_linkedin_profile())
