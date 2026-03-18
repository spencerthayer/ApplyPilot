"""Resume and cover letter validation: banned words, fabrication detection, structural checks.

All validation is profile-driven -- no hardcoded personal data. The validator receives
a profile dict (from applypilot.config.load_profile()) and validates against the user's
actual skills, companies, projects, and school.

Validation modes
----------------
strict  -- banned words = hard errors that trigger retries (original behavior)
normal  -- banned words = warnings only; requires at least one real company in experience
lenient -- banned words ignored; only fabrication and required structure checked
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from applypilot.resume_json import (
    get_profile_company_names,
    get_profile_project_names,
    get_profile_school_names,
    get_profile_skill_keywords,
)
from applypilot.scoring.tailoring_config import (
    check_banned_phrases,
    check_required_patterns,
)

log = logging.getLogger(__name__)


# ── Universal Constants (not personal data) ───────────────────────────────

BANNED_WORDS: list[str] = [
    "passionate", "dedicated", "committed to",
    "utilizing", "utilize", "harnessing",
    "spearheaded", "spearhead", "orchestrated", "championed", "pioneered",
    "robust", "scalable solutions", "cutting-edge", "state-of-the-art", "best-in-class",
    "proven track record", "track record of success", "demonstrated ability",
    "strong communicator", "team player", "fast learner", "self-starter", "go-getter",
    "synergy", "cross-functional collaboration", "holistic",
    "transformative", "innovative solutions", "paradigm", "ecosystem",
    "proactive", "detail-oriented", "highly motivated",
    "seamless", "full lifecycle",
    "deep understanding", "extensive experience", "comprehensive knowledge",
    "thrives in", "excels at", "adept at", "well-versed in",
    "i am confident", "i believe", "i am excited",
    "plays a critical role", "instrumental in", "integral part of",
    "strong track record", "eager to", "eager",
    # Cover-letter-specific additions
    "this demonstrates", "this reflects", "i have experience with",
    "furthermore", "additionally", "moreover",
]

LLM_LEAK_PHRASES: list[str] = [
    "i am sorry", "i apologize", "i will try", "let me try",
    "i am at a loss", "i am truly sorry", "apologies for",
    "i keep fabricating", "i will have to admit", "one final attempt",
    "one last time", "if it fails again", "persistent errors",
    "i am having difficulty", "i made an error", "my mistake",
    "here is the corrected", "here is the revised", "here is the updated",
    "here is my", "below is the", "as requested",
    "note:", "disclaimer:", "important:",
    "i have rewritten", "i have removed", "i have fixed",
    "i have replaced", "i have updated", "i have corrected",
    "per your feedback", "based on your feedback", "as per the instructions",
    "the following resume", "the resume below",
    "the following cover letter", "the letter below",
]

# Known fabrication markers: completely unrelated tools/languages.
# Reasonable stretches (K8s, Terraform, Redis, Kafka etc.) are ALLOWED.
FABRICATION_WATCHLIST: set[str] = {
    # Languages with zero relation to the candidate's stack
    "c#", "c++", "golang", "rust", "ruby",
    "kotlin", "swift", "scala", "matlab",
    # Frameworks for wrong languages
    # NOTE: django, spring, angular, vue removed — may be in candidate's skills_boundary.
    # The skip logic cross-references against profile, but keeping them out avoids edge cases.
    "rails", "svelte",
    # Hard lies: certifications can't be stretched
    "certif", "certified", "pmp", "scrum master", "aws certified",
}

REQUIRED_SECTIONS: set[str] = {"SUMMARY", "TECHNICAL SKILLS", "EXPERIENCE", "PROJECTS", "EDUCATION"}
MECHANISM_VERBS: set[str] = {
    "built",
    "designed",
    "implemented",
    "architected",
    "developed",
    "created",
    "engineered",
    "constructed",
    "automated",
    "optimized",
    "improved",
    "reduced",
}


# ── Helpers ───────────────────────────────────────────────────────────────

def _build_skills_set(profile: dict) -> set[str]:
    """Build the set of allowed skills from the normalized profile skills."""

    return {skill.lower().strip() for skill in get_profile_skill_keywords(profile)}


def sanitize_text(text: str) -> str:
    """Auto-fix common LLM output issues instead of rejecting."""
    text = text.replace(" \u2014 ", ", ").replace("\u2014", ", ")   # em dash -> comma
    text = text.replace("\u2013", "-")    # en dash -> hyphen
    text = text.replace("\u201c", '"').replace("\u201d", '"')   # smart double quotes
    text = text.replace("\u2018", "'").replace("\u2019", "'")   # smart single quotes
    return text.strip()


def _get_role_constraints(role_type: str, config: dict) -> dict:
    """Get role-specific validation constraints from tailoring_config."""

    if not config or not role_type:
        return {}
    role_config = config.get("role_types", {}).get(role_type, {})
    return role_config.get("constraints", {})


def _check_banned_phrases(text: str, role_type: str, config: dict) -> list[str]:
    if not config or not role_type:
        return []
    return check_banned_phrases(text, role_type, config)


def _check_required_patterns(text: str, role_type: str, config: dict) -> tuple[list[str], list[str]]:
    if not config or not role_type:
        return [], []
    return check_required_patterns(text, role_type, config)


def _check_mechanism_required(text: str, role_type: str, config: dict) -> bool:
    if not config or not role_type:
        return True
    constraints = _get_role_constraints(role_type, config)
    if not constraints.get("mechanism_required", False):
        return True
    pattern = r"\b(" + "|".join(map(re.escape, MECHANISM_VERBS)) + r")\b"
    return bool(re.search(pattern, text.lower()))


def _company_is_present(experience_entry: dict, company: str) -> bool:
    """Return whether a profile company appears in an experience entry."""

    def _normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

    company_norm = _normalize(company)
    if not company_norm:
        return False

    entry_text = " ".join(
        str(experience_entry.get(key, ""))
        for key in ("header", "company", "subtitle")
    )
    entry_norm = _normalize(entry_text)
    return company_norm in entry_norm


# ── JSON Field Validation ─────────────────────────────────────────────────

def validate_json_fields(
    data: dict,
    profile: dict,
    mode: str = "normal",
    role_type: Optional[str] = None,
    config: Optional[dict] = None,
) -> dict:
    """Validate individual JSON fields from an LLM-generated tailored resume.

    Args:
        data:    Parsed JSON from the LLM (title, summary, skills, experience, projects, education).
        profile: User profile dict from load_profile().
        mode:    Validation strictness — "strict", "normal", or "lenient".
                 strict  → banned words are errors (trigger retries)
                 normal  → banned words are warnings (no retry), at least one real company required
                 lenient → banned words ignored entirely, company retention not enforced

    Returns:
        {"passed": bool, "errors": list[str], "warnings": list[str]}
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Required keys — always checked regardless of mode.
    # "projects" may be an empty list; only the field itself is required.
    for key in ("title", "summary", "skills", "experience", "education"):
        if key not in data or not data[key]:
            errors.append(f"Missing required field: {key}")
    if "projects" not in data:
        errors.append("Missing required field: projects")
    if errors:
        return {"passed": False, "errors": errors, "warnings": warnings}

    sanitized_title = sanitize_text(str(data.get("title", "")))
    sanitized_summary = sanitize_text(str(data.get("summary", "")))

    skills_val = data.get("skills", "")
    if isinstance(skills_val, dict):
        skills_joined = " ".join(str(v) for v in skills_val.values())
    elif isinstance(skills_val, list):
        skills_joined = " ".join(str(v) for v in skills_val)
    else:
        skills_joined = str(skills_val)
    sanitized_skills = sanitize_text(skills_joined)

    edu_val = data.get("education", "")
    if isinstance(edu_val, list):
        edu_joined = " ".join(str(e) for e in edu_val)
    elif isinstance(edu_val, dict):
        edu_joined = " ".join(str(v) for v in edu_val.values())
    else:
        edu_joined = str(edu_val)
    sanitized_education = sanitize_text(edu_joined)

    all_text_parts: list[str] = [sanitized_title, sanitized_summary, sanitized_skills, sanitized_education]

    # Generated titles must stay aligned with the target job title when provided.
    job_context = profile.get("job_context", {}) or {}
    target_title = str(job_context.get("title", "")).strip()
    generated_title = str(data.get("title", "")).strip()
    if target_title and generated_title:
        def _norm(text: str) -> str:
            return re.sub(r"[^a-z0-9 ]", "", text.lower())

        target_words = [word for word in _norm(target_title).split() if len(word) > 2]
        generated_words = [word for word in _norm(generated_title).split() if len(word) > 2]
        shared = set(target_words) & set(generated_words)
        odd_modifiers = {"partner", "alliances", "evangelist", "advocate", "champion", "ambassador", "specialist"}
        generated_has_odd = any(word in generated_words for word in odd_modifiers)
        if not shared or (generated_has_odd and not any(word in target_words for word in odd_modifiers)):
            errors.append(f"Generated title '{generated_title}' is not aligned with target '{target_title}'")

    if isinstance(data["skills"], dict):
        skills_text = " ".join(str(v) for v in data["skills"].values()).lower()
        for fake in FABRICATION_WATCHLIST:
            if len(fake) <= 2:
                continue
            if fake in skills_text:
                errors.append(f"Fabricated skill: '{fake}'")

    work_companies = get_profile_company_names(profile)

    if isinstance(data["experience"], list):
        matched_companies: set[str] = set()
        for company in work_companies:
            if any(_company_is_present(entry, company) for entry in data["experience"]):
                matched_companies.add(company)

        if mode == "strict":
            for company in work_companies:
                if company not in matched_companies:
                    errors.append(f"Company '{company}' missing from experience")
        elif mode == "normal":
            if work_companies and not matched_companies:
                errors.append("No profile companies found in experience")
            for company in work_companies:
                if company not in matched_companies:
                    warnings.append(f"Company '{company}' missing from experience")
        # lenient mode intentionally skips company-retention checks

        for entry in data["experience"]:
            for bullet in entry.get("bullets", []):
                all_text_parts.append(sanitize_text(str(bullet)))

    if isinstance(data["projects"], list):
        for entry in data["projects"]:
            for bullet in entry.get("bullets", []):
                all_text_parts.append(sanitize_text(str(bullet)))

    schools = get_profile_school_names(profile)
    if schools and schools[0].lower() not in sanitized_education.lower():
        errors.append(f"Education '{schools[0]}' missing")

    all_text = " ".join(all_text_parts).lower()

    found_leaks = [phrase for phrase in LLM_LEAK_PHRASES if phrase in all_text]
    if found_leaks:
        errors.append(f"LLM self-talk: '{found_leaks[0]}'")

    if mode != "lenient":
        found_banned = [word for word in BANNED_WORDS if re.search(r"\b" + re.escape(word) + r"\b", all_text)]
        if found_banned:
            msg = f"Banned words: {', '.join(found_banned[:5])}"
            if mode == "strict":
                errors.append(msg)
            else:
                warnings.append(msg)

    if config and role_type:
        if mode != "lenient":
            try:
                found_role_banned = _check_banned_phrases(all_text, role_type, config)
            except Exception as exc:
                log.warning("Role-specific banned phrase check failed: %s", exc)
                found_role_banned = []
            if found_role_banned:
                msg = f"Role-specific banned phrases: {', '.join(found_role_banned[:5])}"
                if mode == "strict":
                    errors.append(msg)
                else:
                    warnings.append(msg)

        try:
            _, missing_patterns = _check_required_patterns(all_text, role_type, config)
        except Exception as exc:
            log.warning("Required pattern check failed: %s", exc)
            missing_patterns = []
        if missing_patterns:
            msg = f"Missing required patterns: {', '.join(missing_patterns[:5])}"
            if mode == "strict":
                errors.append(msg)
            else:
                warnings.append(msg)

        if not _check_mechanism_required(all_text, role_type, config):
            msg = "Missing mechanism verb (e.g., built, designed, implemented, architected)"
            if mode == "strict":
                errors.append(msg)
            else:
                warnings.append(msg)

    return {"passed": len(errors) == 0, "errors": errors, "warnings": warnings}


# ── Full Resume Text Validation ───────────────────────────────────────────

def validate_tailored_resume(text: str, profile: dict, original_text: str = "") -> dict:
    """Programmatic validation of a tailored resume against the user's profile.

    Args:
        text: The tailored resume text to validate.
        profile: User profile dict from load_profile().
        original_text: The original base resume text (for fabrication comparison).

    Returns:
        {"passed": bool, "errors": list[str], "warnings": list[str]}
    """
    errors: list[str] = []
    warnings: list[str] = []
    text_lower = text.lower()

    personal = profile.get("personal", {})
    # 1. Check required sections exist (flexible matching)
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

    # 2. Check name preserved (warn, don't error -- we can inject it)
    full_name = personal.get("full_name", "")
    if full_name and full_name.lower() not in text_lower:
        warnings.append(f"Name '{full_name}' missing -- will be injected")

    # 3. Check companies preserved
    for company in get_profile_company_names(profile):
        if company.lower() not in text_lower:
            errors.append(f"Company '{company}' missing -- cannot remove real experience")

    # 4. Check projects preserved
    for project in get_profile_project_names(profile):
        if project.lower() not in text_lower:
            warnings.append(f"Project '{project}' not found -- may have been renamed")

    # 5. Check school preserved
    schools = get_profile_school_names(profile)
    if schools and schools[0].lower() not in text_lower:
        errors.append(f"Education '{schools[0]}' missing")

    # 6. Check contact info preserved (warn, don't error -- we can inject)
    email = personal.get("email", "")
    phone = personal.get("phone", "")
    if email and email.lower() not in text_lower:
        warnings.append("Email missing -- will be injected")
    if phone and phone not in text:
        warnings.append("Phone missing -- will be injected")

    # 7. Scan TECHNICAL SKILLS section for fabricated tools
    skills_start = text_lower.find("technical skills")
    skills_end = text_lower.find("experience", skills_start) if skills_start != -1 else -1
    if skills_start != -1 and skills_end != -1:
        skills_block = text_lower[skills_start:skills_end]
        for fake in FABRICATION_WATCHLIST:
            if len(fake) <= 2:
                continue
            if fake in skills_block:
                errors.append(f"FABRICATED SKILL in Technical Skills: '{fake}'")

    # 8. Scan full document for fabrication watchlist items not in original
    if original_text:
        original_lower = original_text.lower()
        for fake in FABRICATION_WATCHLIST:
            if len(fake) <= 2:
                continue
            if fake in text_lower and fake not in original_lower:
                warnings.append(f"New tool/skill appeared: '{fake}' (not in original)")

    # 9. Em dashes (should be auto-fixed by sanitize_text, but safety net)
    if "\u2014" in text or "\u2013" in text:
        errors.append("Contains em dash or en dash.")

    # 10. Banned words (word-boundary matching)
    found_banned = [w for w in BANNED_WORDS if re.search(r"\b" + re.escape(w) + r"\b", text_lower)]
    if found_banned:
        errors.append(f"Banned words: {', '.join(found_banned[:5])}")

    # 11. LLM self-talk leak detection
    found_leaks = [p for p in LLM_LEAK_PHRASES if p in text_lower]
    if found_leaks:
        errors.append(f"LLM self-talk: '{found_leaks[0]}'")

    # 12. Duplicate section detection
    for section_name in ["summary", "experience", "education", "projects"]:
        count = text_lower.count(f"\n{section_name}\n") + text_lower.count(f"\n{section_name} \n")
        if text_lower.startswith(f"{section_name}\n"):
            count += 1
        if count > 1:
            errors.append(f"Section '{section_name}' appears {count} times.")

    return {
        "passed": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }


# ── Cover Letter Validation ──────────────────────────────────────────────

def validate_cover_letter(text: str, mode: str = "normal") -> dict:
    """Programmatic validation of a cover letter.

    Args:
        text: The cover letter text to validate.
        mode: Validation strictness — "strict", "normal", or "lenient".
              strict  → banned words are errors (trigger retries); word limit enforced
              normal  → banned words are warnings; word limit is soft (+25 words)
              lenient → banned words ignored; word count not checked

    Returns:
        {"passed": bool, "errors": list[str], "warnings": list[str]}
    """
    errors: list[str] = []
    warnings: list[str] = []
    text_lower = text.lower()

    # 1. Em dashes — always an error (sanitize_text should have caught these)
    if "\u2014" in text or "\u2013" in text:
        errors.append("Contains em dash or en dash.")

    # 2. Banned words — severity depends on mode
    if mode != "lenient":
        found = [w for w in BANNED_WORDS if re.search(r"\b" + re.escape(w) + r"\b", text_lower)]
        if found:
            msg = f"Banned words: {', '.join(found[:5])}"
            if mode == "strict":
                errors.append(msg)
            else:  # normal
                warnings.append(msg)

    # 3. Word count
    words = len(text.split())
    if mode == "strict" and words > 250:
        errors.append(f"Too long ({words} words). Max 250.")
    elif mode == "normal" and words > 275:
        warnings.append(f"Long ({words} words). Target 250.")
    # lenient: no word count check

    # 4. LLM self-talk — always an error regardless of mode
    found_leaks = [p for p in LLM_LEAK_PHRASES if p in text_lower]
    if found_leaks:
        errors.append(f"LLM self-talk: '{found_leaks[0]}'")

    # 5. Must start with "Dear" — always checked (preamble should have been stripped)
    stripped = text.strip()
    if not stripped.lower().startswith("dear"):
        errors.append("Must start with 'Dear Hiring Manager,'")

    return {"passed": len(errors) == 0, "errors": errors, "warnings": warnings}
