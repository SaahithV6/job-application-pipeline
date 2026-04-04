"""
Cover Letter Generation Module.
Generates tailored cover letters using BOTH the user's resume content
AND the specific job description. Saves as a PDF file for upload.
"""

import os
import re
import subprocess
from datetime import datetime
import db

COVER_LETTERS_DIR = os.path.join(os.path.dirname(__file__), "cover_letters")


def _read_resume_text(resume_path):
    """Extract text from a resume file using pokee-file skill."""
    if not resume_path or not os.path.exists(resume_path):
        return ""
    import json
    param_json = json.dumps({"file_path": resume_path})
    cmd = f"pokee-skill file.read_file_from_pokee_storage <<'SKILLEOF'\n{param_json}\nSKILLEOF"
    result = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=120)
    try:
        data = json.loads(result.stdout.strip())
        if data.get("success") or data.get("data", {}).get("success"):
            inner = data.get("data", data)
            return inner.get("content", inner.get("text", ""))
    except Exception:
        pass
    # Fallback: try reading as plain text
    try:
        with open(resume_path, "r", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


def _extract_resume_sections(resume_text):
    """Parse resume text into structured sections."""
    sections = {
        "skills": [],
        "experience": [],
        "education": [],
        "projects": [],
        "summary": "",
        "full_text": resume_text,
    }
    if not resume_text:
        return sections

    lines = resume_text.strip().split("\n")
    current_section = "summary"
    buffer = []

    section_map = {
        "experience": ["experience", "work history", "employment", "professional experience"],
        "education": ["education", "academic", "degree"],
        "skills": ["skills", "technical skills", "technologies", "proficiencies", "competencies"],
        "projects": ["projects", "personal projects", "portfolio"],
        "summary": ["summary", "objective", "about", "profile"],
    }

    for line in lines:
        line_lower = line.strip().lower()
        matched = False
        for sec_key, keywords in section_map.items():
            if any(kw in line_lower for kw in keywords) and len(line.strip()) < 50:
                # Flush buffer to previous section
                if buffer:
                    if current_section == "skills":
                        sections["skills"] = [b.strip() for b in " ".join(buffer).split(",") if b.strip()]
                    else:
                        sections[current_section] = buffer[:]
                    buffer = []
                current_section = sec_key
                matched = True
                break
        if not matched and line.strip():
            buffer.append(line.strip())

    # Flush remaining
    if buffer:
        if current_section == "skills":
            sections["skills"] = [b.strip() for b in " ".join(buffer).split(",") if b.strip()]
        else:
            sections[current_section] = buffer[:]

    return sections


def _extract_job_requirements(job_description):
    """Pull key requirements and keywords from a job description."""
    if not job_description:
        return {"requirements": [], "keywords": [], "responsibilities": []}

    reqs = []
    resps = []
    keywords = set()

    lines = job_description.split("\n")
    in_req = False
    in_resp = False

    for line in lines:
        line_lower = line.strip().lower()
        if any(kw in line_lower for kw in ["requirement", "qualification", "must have", "you have", "what we need"]):
            in_req = True
            in_resp = False
            continue
        if any(kw in line_lower for kw in ["responsibilit", "you will", "what you'll do", "role involves"]):
            in_resp = True
            in_req = False
            continue
        if line.strip().startswith(("-", "*", "•")) or re.match(r'^\d+\.', line.strip()):
            clean = re.sub(r'^[-*•\d.]+\s*', '', line.strip())
            if in_req:
                reqs.append(clean)
            elif in_resp:
                resps.append(clean)

    # Extract tech keywords
    tech_patterns = [
        r'\b(Python|Java|JavaScript|TypeScript|C\+\+|Go|Rust|Ruby|Swift|Kotlin)\b',
        r'\b(React|Angular|Vue|Node\.?js|Django|Flask|Spring|Rails)\b',
        r'\b(AWS|GCP|Azure|Docker|Kubernetes|Terraform|CI/CD)\b',
        r'\b(SQL|PostgreSQL|MongoDB|Redis|Elasticsearch)\b',
        r'\b(Machine Learning|AI|Deep Learning|NLP|Data Science)\b',
        r'\b(Agile|Scrum|REST|GraphQL|Microservices)\b',
    ]
    for pattern in tech_patterns:
        matches = re.findall(pattern, job_description, re.IGNORECASE)
        keywords.update(m if isinstance(m, str) else m[0] for m in matches)

    return {
        "requirements": reqs[:10],
        "keywords": list(keywords)[:15],
        "responsibilities": resps[:10],
    }


def generate_cover_letter(company, role, job_description="", resume_text=None):
    """
    Generate a professional, tailored cover letter using resume content
    and job description to create a targeted match.

    Args:
        company: Company name
        role: Job title
        job_description: Full text of the job listing
        resume_text: Full text of the user's resume (auto-loaded if None)
    """
    config = db.get_config()
    name = config.get("user_name", "Applicant")
    email = config.get("user_email", "")
    phone = config.get("user_phone", "")
    linkedin = config.get("linkedin_profile_url", "")

    # Load resume if not provided
    if resume_text is None:
        resume_path = config.get("resume_path", "")
        resume_text = _read_resume_text(resume_path) if resume_path else ""

    # Supplement with stored profile data
    stored_work = config.get("work_history_summary", "")
    stored_edu = config.get("education_summary", "")

    # Parse resume into sections
    resume = _extract_resume_sections(resume_text)
    job_reqs = _extract_job_requirements(job_description)

    # Merge resume experience with stored work history
    experience_lines = resume.get("experience", [])
    if not experience_lines and stored_work:
        experience_lines = stored_work.split("\n")

    education_lines = resume.get("education", [])
    if not education_lines and stored_edu:
        education_lines = stored_edu.split("\n")

    skills = resume.get("skills", [])
    projects = resume.get("projects", [])

    # Find overlap between resume skills and job keywords
    matching_skills = []
    if job_reqs["keywords"] and skills:
        resume_skills_lower = {s.lower() for s in skills}
        for kw in job_reqs["keywords"]:
            if kw.lower() in resume_skills_lower or any(kw.lower() in s.lower() for s in skills):
                matching_skills.append(kw)

    # ── Build the cover letter ──
    lines = []

    # Header
    lines.append(name)
    contact_parts = [p for p in [email, phone, linkedin] if p]
    if contact_parts:
        lines.append(" | ".join(contact_parts))
    lines.append("")
    lines.append(datetime.now().strftime("%B %d, %Y"))
    lines.append("")
    lines.append(f"Hiring Manager")
    lines.append(f"{company}")
    lines.append("")
    lines.append(f"Dear Hiring Manager,")
    lines.append("")

    # ── Opening: Enthusiasm + most relevant qualification ──
    opening = f"I am writing to express my strong interest in the {role} position at {company}."
    if experience_lines:
        # Pull the most recent/relevant role
        first_exp = experience_lines[0]
        if " at " in first_exp:
            parts = first_exp.split(" at ", 1)
            opening += f" As a {parts[0].strip()}, I bring hands-on experience that aligns directly with this opportunity."
        else:
            opening += f" My background in {first_exp[:80]} positions me well for this role."
    elif education_lines:
        opening += f" With my academic foundation from {education_lines[0][:80]}, I am eager to apply my knowledge to this role."
    lines.append(opening)
    lines.append("")

    # ── Body 1: Skills alignment with job description ──
    if job_reqs["requirements"] or job_reqs["keywords"]:
        body1 = "I was particularly excited to see this role's focus on "
        if matching_skills:
            body1 += ", ".join(matching_skills[:5])
            body1 += f" — areas where I have developed strong proficiency"
        elif job_reqs["keywords"]:
            body1 += ", ".join(job_reqs["keywords"][:4])
            body1 += f" — technologies and practices I am experienced with"
        body1 += "."

        if job_reqs["requirements"][:2]:
            body1 += f" Your requirement for \"{job_reqs['requirements'][0][:100]}\" resonates with my experience, "
            if experience_lines and len(experience_lines) > 1:
                body1 += f"particularly from my work where {experience_lines[1][:120]}."
            else:
                body1 += "and I am confident I can deliver results in this area."
        lines.append(body1)
        lines.append("")
    elif job_description:
        # Generic alignment paragraph when we couldn't parse structured requirements
        lines.append(
            f"After reviewing the {role} position in detail, I am confident that my skills and experience "
            f"make me an excellent fit. The responsibilities described align closely with the work I have "
            f"done throughout my career, and I am excited about the opportunity to contribute meaningfully."
        )
        lines.append("")

    # ── Body 2: Experience highlights from resume ──
    if experience_lines:
        body2 = "In my professional experience, I have "
        highlights = []
        for exp in experience_lines[:3]:
            # Extract the actionable/descriptive part
            if " - " in exp:
                highlights.append(exp.split(" - ", 1)[1][:120])
            elif len(exp) > 20:
                highlights.append(exp[:120])
        if highlights:
            body2 += highlights[0].lower()
            if len(highlights) > 1:
                body2 += f". Additionally, I have {highlights[1].lower()}"
            body2 += "."
        else:
            body2 += "built a strong track record of delivering impactful results."

        if projects:
            body2 += f" I have also worked on projects including {projects[0][:100]}"
            body2 += ", demonstrating my ability to apply skills in practical settings."
        lines.append(body2)
        lines.append("")

    # ── Body 3: Education ──
    if education_lines:
        edu_text = education_lines[0]
        lines.append(
            f"I hold {edu_text}, which has equipped me with a strong analytical foundation "
            f"and the ability to approach complex challenges systematically."
        )
        lines.append("")

    # ── Closing: Why this company + call to action ──
    if job_reqs["responsibilities"]:
        closing = (
            f"I am drawn to {company} and the chance to {job_reqs['responsibilities'][0][:100].lower()}. "
            f"I am confident that my combination of skills and drive would allow me to make an immediate impact."
        )
    else:
        closing = (
            f"I am drawn to {company} and the opportunity to contribute as a {role}. "
            f"I believe my combination of technical skills, hands-on experience, and enthusiasm "
            f"would allow me to make meaningful contributions from day one."
        )
    lines.append(closing)
    lines.append("")

    lines.append(
        f"I would welcome the opportunity to discuss how my background aligns with the needs "
        f"of your team. Thank you for considering my application."
    )
    lines.append("")
    lines.append("Sincerely,")
    lines.append(name)

    return "\n".join(lines)


def save_cover_letter(company, role, content, output_dir=None):
    """Save a cover letter as both .txt and .pdf for uploading."""
    if not output_dir:
        output_dir = COVER_LETTERS_DIR
    os.makedirs(output_dir, exist_ok=True)

    safe_company = "".join(c for c in company if c.isalnum() or c in " -_").strip().replace(" ", "_")
    safe_role = "".join(c for c in role if c.isalnum() or c in " -_").strip().replace(" ", "_")
    base_name = f"Cover_Letter_{safe_company}_{safe_role}"

    # Save as .txt
    txt_path = os.path.join(output_dir, f"{base_name}.txt")
    with open(txt_path, "w") as f:
        f.write(content)

    # Try to generate PDF using Python (no pandoc needed)
    pdf_path = os.path.join(output_dir, f"{base_name}.pdf")
    try:
        _generate_pdf(content, pdf_path, company, role)
    except Exception:
        # If PDF generation fails, the .txt is still available
        pdf_path = txt_path

    return pdf_path


def _generate_pdf(content, output_path, company, role):
    """Generate a simple PDF from text content using reportlab or fallback."""
    try:
        # Try reportlab first
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_LEFT

        doc = SimpleDocTemplate(output_path, pagesize=letter,
                                leftMargin=1*inch, rightMargin=1*inch,
                                topMargin=1*inch, bottomMargin=1*inch)
        styles = getSampleStyleSheet()
        body_style = ParagraphStyle('Body', parent=styles['Normal'],
                                     fontSize=11, leading=15, spaceAfter=6)
        elements = []
        for para in content.split("\n\n"):
            para = para.strip()
            if para:
                # Escape XML characters
                para = para.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                para = para.replace("\n", "<br/>")
                elements.append(Paragraph(para, body_style))
                elements.append(Spacer(1, 6))
        doc.build(elements)
        return output_path
    except ImportError:
        pass

    # Fallback: generate PDF using basic Python (fpdf2 or text file)
    try:
        from fpdf import FPDF
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", size=11)
        pdf.set_auto_page_break(auto=True, margin=25)
        for line in content.split("\n"):
            pdf.cell(0, 7, line, new_x="LMARGIN", new_y="NEXT")
        pdf.output(output_path)
        return output_path
    except ImportError:
        pass

    # Last resort: just use the txt file
    raise ImportError("No PDF library available")


if __name__ == "__main__":
    # Test with sample job description
    sample_jd = """
    Software Engineer at Google

    Responsibilities:
    - Design and build scalable distributed systems
    - Write clean, maintainable code in Python and Go
    - Collaborate with cross-functional teams

    Requirements:
    - BS in Computer Science or equivalent
    - 2+ years experience with Python, Java, or Go
    - Experience with cloud platforms (GCP, AWS)
    - Strong understanding of data structures and algorithms
    """
    letter = generate_cover_letter("Google", "Software Engineer", job_description=sample_jd)
    path = save_cover_letter("Google", "Software Engineer", letter)
    print(f"Saved to: {path}")
    print()
    print(letter)
