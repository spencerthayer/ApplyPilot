"""Structural validation — JSON field checks and cover letter validation."""

from __future__ import annotations

import logging
import re
from typing import Optional

from applypilot.resume.extraction import (
    get_profile_company_names,
    get_profile_school_names,
)
from applypilot.scoring.tailoring_config import (
    check_banned_phrases,
    check_required_patterns,
)
from applypilot.scoring.validator.banned_words import (
    BANNED_WORDS,
    FABRICATION_WATCHLIST,
    LLM_LEAK_PHRASES,
    MECHANISM_VERBS,
)
from applypilot.scoring.validator.sanitizer import build_skills_set, sanitize_text

log = logging.getLogger(__name__)


def _get_role_constraints(role_type: str, config: dict) -> dict:
    if not config or not role_type:
        return {}
    return config.get("role_types", {}).get(role_type, {}).get("constraints", {})


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
    def _normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

    company_norm = _normalize(company)
    if not company_norm:
        return False
    entry_text = " ".join(str(experience_entry.get(key, "")) for key in ("header", "company", "subtitle"))
    return company_norm in _normalize(entry_text)


def validate_json_fields(
        data: dict,
        profile: dict,
        mode: str = "normal",
        role_type: Optional[str] = None,
        config: Optional[dict] = None,
) -> dict:
    """Validate individual JSON fields from an LLM-generated tailored resume."""
    errors: list[str] = []
    warnings: list[str] = []

    allowed_skills = build_skills_set(profile)
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

    # Title alignment check
    job_context = profile.get("job_context", {}) or {}
    target_title = str(job_context.get("title", "")).strip()
    generated_title = str(data.get("title", "")).strip()
    if target_title and generated_title:

        def _norm(text: str) -> str:
            return re.sub(r"[^a-z0-9 ]", "", text.lower())

        target_words = [w for w in _norm(target_title).split() if len(w) > 2]
        generated_words = [w for w in _norm(generated_title).split() if len(w) > 2]
        shared = set(target_words) & set(generated_words)
        odd_modifiers = {"partner", "alliances", "evangelist", "advocate", "champion", "ambassador", "specialist"}
        if not shared or (
                any(w in generated_words for w in odd_modifiers) and not any(w in target_words for w in odd_modifiers)
        ):
            errors.append(f"Generated title '{generated_title}' is not aligned with target '{target_title}'")

    if isinstance(data["skills"], dict):
        skills_text = " ".join(str(v) for v in data["skills"].values()).lower()
        for fake in FABRICATION_WATCHLIST:
            if len(fake) <= 2:
                continue
            if fake in skills_text and fake not in allowed_skills:
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
        found_banned = [w for w in BANNED_WORDS if re.search(r"\b" + re.escape(w) + r"\b", all_text)]
        if found_banned:
            msg = f"Banned words: {', '.join(found_banned[:5])}"
            (errors if mode == "strict" else warnings).append(msg)

    if config and role_type:
        if mode != "lenient":
            try:
                found_role_banned = _check_banned_phrases(all_text, role_type, config)
            except Exception as exc:
                log.warning("Role-specific banned phrase check failed: %s", exc)
                found_role_banned = []
            if found_role_banned:
                msg = f"Role-specific banned phrases: {', '.join(found_role_banned[:5])}"
                (errors if mode == "strict" else warnings).append(msg)

        try:
            _, missing_patterns = _check_required_patterns(all_text, role_type, config)
        except Exception as exc:
            log.warning("Required pattern check failed: %s", exc)
            missing_patterns = []
        if missing_patterns:
            msg = f"Missing required patterns: {', '.join(missing_patterns[:5])}"
            (errors if mode == "strict" else warnings).append(msg)

        if not _check_mechanism_required(all_text, role_type, config):
            msg = "Missing mechanism verb (e.g., built, designed, implemented, architected)"
            (errors if mode == "strict" else warnings).append(msg)

    return {"passed": len(errors) == 0, "errors": errors, "warnings": warnings}


def validate_cover_letter(text: str, mode: str = "normal") -> dict:
    """Programmatic validation of a cover letter."""
    errors: list[str] = []
    warnings: list[str] = []
    text_lower = text.lower()

    if "\u2014" in text or "\u2013" in text:
        errors.append("Contains em dash or en dash.")

    if mode != "lenient":
        found = [w for w in BANNED_WORDS if re.search(r"\b" + re.escape(w) + r"\b", text_lower)]
        if found:
            msg = f"Banned words: {', '.join(found[:5])}"
            (errors if mode == "strict" else warnings).append(msg)

    words = len(text.split())
    if mode == "strict" and words > 250:
        errors.append(f"Too long ({words} words). Max 250.")
    elif mode == "normal" and words > 275:
        warnings.append(f"Long ({words} words). Target 250.")

    found_leaks = [p for p in LLM_LEAK_PHRASES if p in text_lower]
    if found_leaks:
        errors.append(f"LLM self-talk: '{found_leaks[0]}'")

    if not text.strip().lower().startswith("dear"):
        errors.append("Must start with 'Dear Hiring Manager,'")

    return {"passed": len(errors) == 0, "errors": errors, "warnings": warnings}
