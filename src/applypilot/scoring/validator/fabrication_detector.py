"""Fabrication detection — validate tailored resume against user profile."""

from __future__ import annotations

import re

from applypilot.resume.extraction import (
    get_profile_company_names,
    get_profile_project_names,
    get_profile_school_names,
)
from applypilot.scoring.validator.banned_words import (
    BANNED_WORDS,
    FABRICATION_WATCHLIST,
    LLM_LEAK_PHRASES,
)
from applypilot.scoring.validator.deviation_guard import check_resume_deviation
from applypilot.scoring.validator.sanitizer import build_skills_set


def validate_tailored_resume(text: str, profile: dict, original_text: str = "") -> dict:
    """Programmatic validation of a tailored resume against the user's profile."""
    errors: list[str] = []
    warnings: list[str] = []
    text_lower = text.lower()
    personal = profile.get("personal", {})

    # 1. Required sections
    section_variants: dict[str, list[str]] = {
        "SUMMARY": ["summary", "professional summary", "profile"],
        "TECHNICAL SKILLS": ["technical skills", "skills", "tech stack", "core skills", "technologies"],
        "EXPERIENCE": ["experience", "work experience", "professional experience"],
        "PROJECTS": ["projects", "personal projects", "key projects", "selected projects"],
        "EDUCATION": ["education", "academic background"],
    }
    for section, variants in section_variants.items():
        if not any(v in text_lower for v in variants):
            errors.append(f"Missing required section: {section} (or variant)")

    # 2. Name preserved
    full_name = personal.get("full_name", "")
    if full_name and full_name.lower() not in text_lower:
        warnings.append(f"Name '{full_name}' missing -- will be injected")

    # 3. Companies preserved
    for company in get_profile_company_names(profile):
        if company.lower() not in text_lower:
            errors.append(f"Company '{company}' missing -- cannot remove real experience")

    # 4. Projects preserved
    for project in get_profile_project_names(profile):
        if project.lower() not in text_lower:
            warnings.append(f"Project '{project}' not found -- may have been renamed")

    # 5. School preserved
    schools = get_profile_school_names(profile)
    if schools and schools[0].lower() not in text_lower:
        errors.append(f"Education '{schools[0]}' missing")

    # 6. Contact info
    email = personal.get("email", "")
    phone = personal.get("phone", "")
    if email and email.lower() not in text_lower:
        warnings.append("Email missing -- will be injected")
    if phone and phone not in text:
        warnings.append("Phone missing -- will be injected")

    # 7. Fabricated skills
    allowed_skills = build_skills_set(profile)
    skills_start = text_lower.find("technical skills")
    skills_end = text_lower.find("experience", skills_start) if skills_start != -1 else -1
    if skills_start != -1 and skills_end != -1:
        skills_block = text_lower[skills_start:skills_end]
        for fake in FABRICATION_WATCHLIST:
            if len(fake) <= 2:
                continue
            if fake in skills_block and fake not in allowed_skills:
                errors.append(f"FABRICATED SKILL in Technical Skills: '{fake}'")

    # 8. New tools not in original
    if original_text:
        original_lower = original_text.lower()
        for fake in FABRICATION_WATCHLIST:
            if len(fake) <= 2:
                continue
            if fake in text_lower and fake not in original_lower:
                warnings.append(f"New tool/skill appeared: '{fake}' (not in original)")

    # 9. Em dashes
    if "\u2014" in text or "\u2013" in text:
        errors.append("Contains em dash or en dash.")

    # 10. Banned words
    found_banned = [w for w in BANNED_WORDS if re.search(r"\b" + re.escape(w) + r"\b", text_lower)]
    if found_banned:
        errors.append(f"Banned words: {', '.join(found_banned[:5])}")

    # 11. LLM self-talk
    found_leaks = [p for p in LLM_LEAK_PHRASES if p in text_lower]
    if found_leaks:
        errors.append(f"LLM self-talk: '{found_leaks[0]}'")

    # 12. Duplicate sections
    for section_name in ["summary", "experience", "education", "projects"]:
        count = text_lower.count(f"\n{section_name}\n") + text_lower.count(f"\n{section_name} \n")
        if text_lower.startswith(f"{section_name}\n"):
            count += 1
        if count > 1:
            errors.append(f"Section '{section_name}' appears {count} times.")

    # 13. Statistical deviation
    if original_text:
        passed, retention = check_resume_deviation(original_text, text)
        if not passed:
            errors.append(
                f"Resume deviates too far from original (token retention {retention:.0%}, "
                f"below statistical threshold). Reframe existing content, don't replace it."
            )
        elif retention < 0.50:
            warnings.append(f"Low token retention ({retention:.0%}) — borderline deviation.")

    return {"passed": len(errors) == 0, "errors": errors, "warnings": warnings}
