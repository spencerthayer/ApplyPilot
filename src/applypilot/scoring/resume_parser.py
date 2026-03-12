# Helper function to parse ALL roles from resume text (not just normalized profile work entries)
def _parse_resume_roles(resume_text: str) -> list[dict]:
    """Parse all work history entries from resume text.

    Extracts roles from format: '- Title | Company | Date'
    Returns list of dicts with company, position, start_date, end_date
    """
    roles = []
    lines = resume_text.split("\n")

    in_experience = False
    for line in lines:
        line = line.strip()

        # Detect EXPERIENCE section
        if line.upper() in ["EXPERIENCE", "WORK EXPERIENCE", "PROFESSIONAL EXPERIENCE"]:
            in_experience = True
            continue

        # Stop at next major section
        if in_experience and line and line.upper() in ["EDUCATION", "SKILLS", "PROJECTS"]:
            in_experience = False
            continue

        # Parse role lines: "- Title | Company | Date" or "- Title | Company | Start - End"
        if in_experience and line.startswith("- "):
            parts = line[2:].split(" | ")  # Remove '- ' and split by ' | '
            if len(parts) >= 2:
                position = parts[0].strip()
                company = parts[1].strip()
                dates = parts[2].strip() if len(parts) > 2 else ""

                # Parse dates: "2019-07-31 - Present" or "2019-07-31 - 2019-07-31"
                start_date = ""
                end_date = ""
                if dates:
                    date_parts = dates.split(" - ")
                    start_date = date_parts[0].strip()
                    end_date = date_parts[1].strip() if len(date_parts) > 1 else ""

                roles.append({"company": company, "position": position, "start_date": start_date, "end_date": end_date})

    return roles
