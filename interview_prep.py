"""
Interview Prep & Smart Auto-Reply Module.
Generates prep materials and sends auto-replies to interview invitations.
"""

import json
import subprocess
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


def research_company(company_name):
    """Search for company info to build interview prep."""
    result = _pokee_skill("google_search.search_google", {
        "query": f"{company_name} company interview questions glassdoor",
    })
    search_results = []
    if result.get("success"):
        for item in result.get("results", result.get("organic_results", []))[:5]:
            search_results.append({
                "title": item.get("title", ""),
                "snippet": item.get("snippet", item.get("description", "")),
                "link": item.get("link", item.get("url", "")),
            })
    return search_results


def scrape_company_linkedin(company_name):
    """Get company info from LinkedIn."""
    # Build company URL guess
    slug = company_name.lower().replace(" ", "-").replace(",", "").replace(".", "")
    result = _pokee_skill("linkedin_scraper.scrape_linkedin_company_by_url", {
        "urls": [f"https://www.linkedin.com/company/{slug}"],
    })
    if result.get("success") and result.get("companies"):
        return result["companies"][0]
    return None


def generate_prep_content(company, role, company_info=None, search_results=None):
    """Generate interview preparation content."""
    prep = []
    prep.append(f"# Interview Preparation: {role} at {company}")
    prep.append("")

    # Company overview
    prep.append("## Company Overview")
    if company_info:
        desc = company_info.get("description", company_info.get("about", ""))
        industry = company_info.get("industry", "")
        size = company_info.get("company_size", company_info.get("employees", ""))
        hq = company_info.get("headquarters", company_info.get("hq", ""))
        if desc:
            prep.append(desc[:500])
        if industry:
            prep.append(f"- Industry: {industry}")
        if size:
            prep.append(f"- Company Size: {size}")
        if hq:
            prep.append(f"- Headquarters: {hq}")
    else:
        prep.append(f"Research {company} before the interview.")
    prep.append("")

    # Common questions
    prep.append("## Likely Interview Questions")
    prep.append(f"1. Why do you want to work at {company}?")
    prep.append(f"2. What interests you about the {role} role?")
    prep.append("3. Tell me about your most challenging project.")
    prep.append("4. How do you handle tight deadlines?")
    prep.append("5. Where do you see yourself in 5 years?")
    prep.append(f"6. What relevant experience do you bring to the {role} position?")
    prep.append("7. Describe a time you disagreed with a team member and how you resolved it.")
    prep.append("")

    # STAR method
    prep.append("## STAR Method Template")
    prep.append("Use this framework for behavioral questions:")
    prep.append("- **Situation**: Set the scene and context")
    prep.append("- **Task**: Describe your responsibility")
    prep.append("- **Action**: Explain what you did")
    prep.append("- **Result**: Share the outcome and what you learned")
    prep.append("")

    # Research links
    if search_results:
        prep.append("## Research Links")
        for item in search_results[:5]:
            if item.get("title") and item.get("link"):
                prep.append(f"- [{item['title']}]({item['link']})")
                if item.get("snippet"):
                    prep.append(f"  {item['snippet'][:150]}")
        prep.append("")

    # Tips
    prep.append("## Quick Tips")
    prep.append("- Research the interviewer on LinkedIn before the call")
    prep.append("- Prepare 3-5 questions to ask them")
    prep.append("- Test your camera/mic 15 minutes before")
    prep.append(f"- Review the {role} job description one more time")
    prep.append("- Have a copy of your resume open")

    return "\n".join(prep)


def build_reply_email(company, role, interviewer=None, interview_date=None, prep_content=None):
    """Build an auto-reply email for an interview invitation."""
    config = db.get_config()
    user_name = config.get("user_name", "")
    first_name = user_name.split()[0] if user_name else ""

    greeting = f"Dear {interviewer}," if interviewer else "Dear Hiring Team,"

    body_parts = [
        greeting,
        "",
        f"Thank you for considering me for the {role} position at {company}. I'm excited about this opportunity and look forward to learning more about the role and the team.",
        "",
    ]

    if interview_date:
        body_parts.append(f"I confirm my availability for the interview on {interview_date}. I will be prepared and ready.")
        body_parts.append("")

    body_parts.extend([
        "Please let me know if there's anything I should prepare in advance or if you need any additional information from me.",
        "",
        "Best regards,",
        first_name or user_name or "Applicant",
    ])

    return "\n".join(body_parts)


def send_interview_reply(thread_id, company, role, interview_details=None):
    """
    Auto-reply to an interview invitation email.
    """
    config = db.get_config()
    if config.get("auto_reply_enabled", "true") != "true":
        return {"success": False, "skipped": True, "reason": "Auto-reply disabled"}

    interviewer = interview_details.get("interviewer") if interview_details else None
    interview_date = interview_details.get("date_str") if interview_details else None

    # Generate prep content
    search_results = research_company(company)
    company_info = scrape_company_linkedin(company)
    prep_content = generate_prep_content(company, role, company_info, search_results)

    # Build reply
    reply_body = build_reply_email(company, role, interviewer, interview_date, prep_content)

    # Send reply via Gmail
    result = _pokee_skill("gmail.reply_to_gmail_thread", {
        "gmail_thread_id": thread_id,
        "body": reply_body,
    })

    return {
        "success": result.get("success", False),
        "reply_sent": result.get("success", False),
        "prep_content": prep_content,
        "reply_body": reply_body,
        "error": result.get("error"),
    }


def process_interview_replies(interview_data_list):
    """
    Process interview findings from monitor and send auto-replies.
    """
    results = []
    for interview in interview_data_list:
        app_id = interview["application_id"]
        company = interview["company"]
        role = interview["role"]
        thread_id = interview.get("thread_id", "")
        details = interview.get("details", {})
        classification = interview.get("classification", "interview")

        if classification == "self_schedule":
            # Don't auto-reply for self-schedule - just prep materials
            search_results = research_company(company)
            company_info = scrape_company_linkedin(company)
            prep = generate_prep_content(company, role, company_info, search_results)
            results.append({
                "application_id": app_id,
                "action": "prep_generated",
                "prep_content": prep,
            })
        elif thread_id:
            result = send_interview_reply(thread_id, company, role, details)
            results.append({
                "application_id": app_id,
                "action": "reply_sent" if result.get("reply_sent") else "reply_failed",
                **result,
            })
        else:
            results.append({
                "application_id": app_id,
                "action": "no_thread_id",
                "error": "No email thread ID to reply to",
            })

    return results


if __name__ == "__main__":
    # Test prep generation
    prep = generate_prep_content("Google", "Software Engineer")
    print(prep)
