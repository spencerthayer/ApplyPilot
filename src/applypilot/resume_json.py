"""Canonical JSON Resume loading, validation, normalization, and rendering."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from applypilot.jsonresume_schema import JSON_RESUME_SCHEMA_V1

DEFAULT_RENDER_THEME = "jsonresume-theme-even"
_FORBIDDEN_SECRET_KEY_RE = re.compile(r"(password|secret|token|api[_-]?key)", re.IGNORECASE)
_DATE_YEAR_RE = re.compile(r"^\s*(\d{4})")

_APPLYPILOT_META_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "personal": {
            "type": "object",
            "properties": {
                "preferred_name": {"type": "string"},
                "address": {"type": "string"},
                "province_state": {"type": "string"},
                "country": {"type": "string"},
                "postal_code": {"type": "string"},
                "linkedin_url": {"type": "string"},
                "github_url": {"type": "string"},
                "portfolio_url": {"type": "string"},
                "website_url": {"type": "string"},
            },
            "additionalProperties": True,
        },
        "target_role": {"type": "string"},
        "years_of_experience_total": {"type": ["string", "number", "integer"]},
        "work_authorization": {
            "type": "object",
            "properties": {
                "legally_authorized_to_work": {"type": ["string", "boolean"]},
                "legally_authorized": {"type": ["string", "boolean"]},
                "require_sponsorship": {"type": ["string", "boolean"]},
                "needs_sponsorship": {"type": ["string", "boolean"]},
                "work_permit_type": {"type": "string"},
            },
            "additionalProperties": True,
        },
        "compensation": {
            "type": "object",
            "properties": {
                "salary_expectation": {"type": ["string", "number", "integer"]},
                "salary_currency": {"type": "string"},
                "salary_range_min": {"type": ["string", "number", "integer"]},
                "salary_range_max": {"type": ["string", "number", "integer"]},
                "currency_conversion_note": {"type": "string"},
            },
            "additionalProperties": True,
        },
        "availability": {
            "type": "object",
            "properties": {
                "earliest_start_date": {"type": "string"},
                "available_for_full_time": {"type": ["string", "boolean"]},
                "available_for_contract": {"type": ["string", "boolean"]},
            },
            "additionalProperties": True,
        },
        "eeo_voluntary": {
            "type": "object",
            "properties": {
                "gender": {"type": "string"},
                "race_ethnicity": {"type": "string"},
                "ethnicity": {"type": "string"},
                "veteran_status": {"type": "string"},
                "disability_status": {"type": "string"},
            },
            "additionalProperties": True,
        },
        "resume_facts": {
            "type": "object",
            "properties": {
                "preserved_companies": {"type": "array", "items": {"type": "string"}},
                "preserved_projects": {"type": "array", "items": {"type": "string"}},
                "preserved_school": {"type": "string"},
                "real_metrics": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": True,
        },
        "tailoring_config": {"type": "object"},
        "files": {
            "type": "object",
            "additionalProperties": {"type": "string"},
        },
        "render": {
            "type": "object",
            "properties": {
                "theme": {"type": "string"},
            },
            "additionalProperties": True,
        },
    },
    "additionalProperties": True,
}

_WORK_EXTENSION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "key_metrics": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": True,
}


class ResumeJsonError(ValueError):
    """Raised when resume.json is invalid or unusable."""


def _load_jsonschema():
    try:
        from jsonschema import Draft7Validator
    except ImportError as exc:  # pragma: no cover - exercised via runtime diagnostics
        raise ResumeJsonError(
            "JSON Resume support requires the 'jsonschema' package. "
            "Install ApplyPilot with updated Python dependencies."
        ) from exc
    return Draft7Validator


def _format_path(parts: list[Any]) -> str:
    if not parts:
        return "$"
    rendered = "$"
    for part in parts:
        if isinstance(part, int):
            rendered += f"[{part}]"
        else:
            rendered += f".{part}"
    return rendered


def _collect_schema_errors(payload: Any, schema: dict, prefix: list[Any] | None = None) -> list[str]:
    validator_cls = _load_jsonschema()
    validator = validator_cls(schema)
    errors = []
    path_prefix = prefix or []
    for error in sorted(validator.iter_errors(payload), key=lambda item: list(item.path)):
        path = _format_path(path_prefix + list(error.path))
        errors.append(f"{path}: {error.message}")
    return errors


def _find_forbidden_keys(value: Any, prefix: list[Any] | None = None) -> list[str]:
    prefix = prefix or []
    findings: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            current = prefix + [key]
            if _FORBIDDEN_SECRET_KEY_RE.search(str(key)):
                findings.append(f"{_format_path(current)}: secrets must stay in .env, not resume.json")
            findings.extend(_find_forbidden_keys(child, current))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(_find_forbidden_keys(child, prefix + [index]))
    return findings


def looks_like_resume_json(data: Any) -> bool:
    """Return True when a payload follows JSON Resume top-level conventions."""

    if not isinstance(data, dict):
        return False
    return any(key in data for key in ("basics", "work", "education", "skills", "projects", "meta"))


def validate_applypilot_meta(data: dict) -> None:
    """Validate ApplyPilot extensions inside a JSON Resume document."""

    meta = data.get("meta", {})
    errors: list[str] = []
    if meta is not None and not isinstance(meta, dict):
        raise ResumeJsonError("Invalid resume.json:\n- $.meta: must be an object")

    applypilot_meta = meta.get("applypilot", {}) if isinstance(meta, dict) else {}
    if applypilot_meta is None:
        applypilot_meta = {}
    errors.extend(_collect_schema_errors(applypilot_meta, _APPLYPILOT_META_SCHEMA, ["meta", "applypilot"]))
    errors.extend(_find_forbidden_keys(applypilot_meta, ["meta", "applypilot"]))

    work_entries = data.get("work", [])
    if isinstance(work_entries, list):
        for index, entry in enumerate(work_entries):
            if not isinstance(entry, dict):
                continue
            extension = entry.get("x-applypilot")
            if extension is None:
                continue
            errors.extend(
                _collect_schema_errors(extension, _WORK_EXTENSION_SCHEMA, ["work", index, "x-applypilot"])
            )
            errors.extend(_find_forbidden_keys(extension, ["work", index, "x-applypilot"]))

    if errors:
        raise ResumeJsonError("Invalid resume.json ApplyPilot extensions:\n- " + "\n- ".join(errors))


def validate_resume_json(data: dict) -> None:
    """Validate a JSON Resume payload plus ApplyPilot extensions."""

    if not isinstance(data, dict):
        raise ResumeJsonError("Invalid resume.json:\n- $: root value must be an object")

    errors = _collect_schema_errors(data, JSON_RESUME_SCHEMA_V1)
    if errors:
        raise ResumeJsonError("Invalid resume.json:\n- " + "\n- ".join(errors))
    validate_applypilot_meta(data)


def load_resume_json_from_path(path: Path) -> dict:
    """Read and validate a resume.json file from disk."""

    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise FileNotFoundError(f"Resume JSON not found at {path}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ResumeJsonError(
            f"Invalid resume.json at {path}: line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc

    validate_resume_json(data)
    return data


def _coerce_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _coerce_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value in (None, ""):
        return []
    return [str(value).strip()]


def _safe_get(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return ""


def _parse_year(value: Any) -> int | None:
    if value in (None, ""):
        return None
    match = _DATE_YEAR_RE.match(str(value))
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


def _compute_years_experience(work: list[dict]) -> str:
    years = [_parse_year(item.get("startDate")) for item in work if isinstance(item, dict)]
    years = [year for year in years if year is not None]
    if not years:
        return ""
    current_year = date.today().year
    span = max(0, current_year - min(years))
    return str(span)


def _select_current_work(work: list[dict]) -> dict[str, Any]:
    def sort_key(entry: dict[str, Any]) -> tuple[int, int, int]:
        end_year = _parse_year(entry.get("endDate"))
        start_year = _parse_year(entry.get("startDate"))
        is_current = 1 if not _coerce_str(entry.get("endDate")) else 0
        return (is_current, end_year or 0, start_year or 0)

    valid = [entry for entry in work if isinstance(entry, dict)]
    if not valid:
        return {}
    return sorted(valid, key=sort_key, reverse=True)[0]


def _profile_urls(profiles: list[dict[str, Any]]) -> dict[str, str]:
    urls = {
        "linkedin_url": "",
        "github_url": "",
        "portfolio_url": "",
    }
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        network = _coerce_str(profile.get("network")).lower()
        url = _coerce_str(profile.get("url"))
        if not url:
            continue
        if "linkedin" in network and not urls["linkedin_url"]:
            urls["linkedin_url"] = url
        elif "github" in network and not urls["github_url"]:
            urls["github_url"] = url
        elif not urls["portfolio_url"]:
            urls["portfolio_url"] = url
    return urls


def _normalize_skill_category(name: str) -> str:
    lowered = name.lower()
    if "language" in lowered:
        return "programming_languages"
    if "framework" in lowered or "library" in lowered:
        return "frameworks"
    if "database" in lowered:
        return "databases"
    if "devops" in lowered or "infra" in lowered or "cloud" in lowered:
        return "devops"
    return "tools"


def _normalize_skill_sections(skills: list[dict[str, Any]]) -> dict[str, list[str]]:
    normalized: dict[str, list[str]] = {
        "programming_languages": [],
        "frameworks": [],
        "devops": [],
        "databases": [],
        "tools": [],
    }
    for item in skills:
        if not isinstance(item, dict):
            continue
        category = _normalize_skill_category(_coerce_str(item.get("name")))
        for keyword in _coerce_list(item.get("keywords", [])):
            if keyword not in normalized[category]:
                normalized[category].append(keyword)
    return normalized


def _normalize_work_history(work: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    normalized: list[dict[str, Any]] = []
    metrics: list[str] = []
    for item in work:
        if not isinstance(item, dict):
            continue
        extension = item.get("x-applypilot", {}) if isinstance(item.get("x-applypilot"), dict) else {}
        key_metrics = _coerce_list(extension.get("key_metrics", []))
        metrics.extend(metric for metric in key_metrics if metric not in metrics)
        normalized.append(
            {
                "company": _coerce_str(item.get("name")),
                "position": _coerce_str(item.get("position")),
                "location": _coerce_str(item.get("location")),
                "start_year": _parse_year(item.get("startDate")),
                "end_year": _parse_year(item.get("endDate")),
                "start_date": _coerce_str(item.get("startDate")),
                "end_date": _coerce_str(item.get("endDate")),
                "summary": _coerce_str(item.get("summary")),
                "highlights": _coerce_list(item.get("highlights", [])),
                "key_metrics": key_metrics,
            }
        )
    return normalized, metrics


def _normalize_education(education: list[dict[str, Any]]) -> tuple[list[dict[str, str]], str]:
    normalized: list[dict[str, str]] = []
    latest_level = ""
    for item in education:
        if not isinstance(item, dict):
            continue
        normalized_entry = {
            "institution": _coerce_str(item.get("institution")),
            "studyType": _coerce_str(item.get("studyType")),
            "area": _coerce_str(item.get("area")),
            "endDate": _coerce_str(item.get("endDate")),
        }
        normalized.append(normalized_entry)
        if normalized_entry["studyType"]:
            latest_level = normalized_entry["studyType"]
    return normalized, latest_level


def _merge_unique(base: list[str], extra: list[str]) -> list[str]:
    merged = list(base)
    for item in extra:
        if item and item not in merged:
            merged.append(item)
    return merged


def normalize_profile_from_resume_json(data: dict) -> dict:
    """Map JSON Resume plus ApplyPilot extensions into the legacy profile contract."""

    basics = data.get("basics", {}) if isinstance(data.get("basics"), dict) else {}
    location = basics.get("location", {}) if isinstance(basics.get("location"), dict) else {}
    meta = data.get("meta", {}) if isinstance(data.get("meta"), dict) else {}
    applypilot = meta.get("applypilot", {}) if isinstance(meta.get("applypilot"), dict) else {}
    personal_meta = applypilot.get("personal", {}) if isinstance(applypilot.get("personal"), dict) else {}
    work_entries = data.get("work", []) if isinstance(data.get("work"), list) else []
    education_entries = data.get("education", []) if isinstance(data.get("education"), list) else []
    skills_entries = data.get("skills", []) if isinstance(data.get("skills"), list) else []

    work_history, work_metrics = _normalize_work_history(work_entries)
    education, education_level = _normalize_education(education_entries)
    current_work = _select_current_work(work_entries)
    profile_urls = _profile_urls(basics.get("profiles", []) if isinstance(basics.get("profiles"), list) else [])

    experience_total = _coerce_str(applypilot.get("years_of_experience_total")) or _compute_years_experience(work_entries)
    target_role = _coerce_str(applypilot.get("target_role")) or _coerce_str(basics.get("label"))
    current_title = _coerce_str(current_work.get("position"))
    current_company = _coerce_str(current_work.get("name"))

    resume_facts = applypilot.get("resume_facts", {}) if isinstance(applypilot.get("resume_facts"), dict) else {}
    real_metrics = _merge_unique(_coerce_list(resume_facts.get("real_metrics", [])), work_metrics)

    work_authorization = applypilot.get("work_authorization", {}) if isinstance(
        applypilot.get("work_authorization"), dict
    ) else {}
    compensation = applypilot.get("compensation", {}) if isinstance(applypilot.get("compensation"), dict) else {}
    availability = applypilot.get("availability", {}) if isinstance(applypilot.get("availability"), dict) else {}
    eeo = applypilot.get("eeo_voluntary", {}) if isinstance(applypilot.get("eeo_voluntary"), dict) else {}

    website_url = _coerce_str(personal_meta.get("website_url")) or _coerce_str(basics.get("url"))
    linkedin_url = _coerce_str(personal_meta.get("linkedin_url")) or profile_urls["linkedin_url"]
    github_url = _coerce_str(personal_meta.get("github_url")) or profile_urls["github_url"]
    portfolio_url = _coerce_str(personal_meta.get("portfolio_url")) or profile_urls["portfolio_url"]

    return normalize_legacy_profile(
        {
            "personal": {
                "full_name": _coerce_str(basics.get("name")),
                "preferred_name": _coerce_str(personal_meta.get("preferred_name")),
                "email": _coerce_str(basics.get("email")),
                "password": "",
                "phone": _coerce_str(basics.get("phone")),
                "address": _coerce_str(personal_meta.get("address")) or _coerce_str(location.get("address")),
                "city": _coerce_str(location.get("city")),
                "province_state": _coerce_str(personal_meta.get("province_state")) or _coerce_str(location.get("region")),
                "country": _coerce_str(personal_meta.get("country")) or _coerce_str(location.get("countryCode")),
                "postal_code": _coerce_str(personal_meta.get("postal_code")) or _coerce_str(location.get("postalCode")),
                "linkedin_url": linkedin_url,
                "github_url": github_url,
                "portfolio_url": portfolio_url,
                "website_url": website_url,
            },
            "work_authorization": work_authorization,
            "availability": availability,
            "compensation": compensation,
            "work_history": work_history,
            "experience": {
                "years_of_experience_total": experience_total,
                "education_level": education_level,
                "current_title": current_title,
                "current_job_title": current_title,
                "current_company": current_company,
                "target_role": target_role,
            },
            "education": education,
            "skills_boundary": _normalize_skill_sections(skills_entries),
            "resume_facts": {
                "preserved_companies": _coerce_list(resume_facts.get("preserved_companies", [])),
                "preserved_projects": _coerce_list(resume_facts.get("preserved_projects", [])),
                "preserved_school": _coerce_str(resume_facts.get("preserved_school")),
                "real_metrics": real_metrics,
            },
            "eeo_voluntary": eeo,
            "tailoring_config": applypilot.get("tailoring_config", {}) if isinstance(
                applypilot.get("tailoring_config"), dict
            ) else {},
            "files": applypilot.get("files", {}) if isinstance(applypilot.get("files"), dict) else {},
        }
    )


def normalize_legacy_profile(profile: dict) -> dict:
    """Normalize legacy profile.json payloads and fill contract aliases/defaults."""

    normalized = copy.deepcopy(profile)
    personal = normalized.setdefault("personal", {})
    work_auth = normalized.setdefault("work_authorization", {})
    availability = normalized.setdefault("availability", {})
    compensation = normalized.setdefault("compensation", {})
    experience = normalized.setdefault("experience", {})
    resume_facts = normalized.setdefault("resume_facts", {})
    eeo = normalized.setdefault("eeo_voluntary", {})
    normalized.setdefault("education", [])
    normalized.setdefault("work_history", [])
    normalized.setdefault("tailoring_config", {})
    normalized.setdefault("files", {})

    personal.setdefault("full_name", "")
    personal.setdefault("preferred_name", "")
    personal.setdefault("email", "")
    personal.setdefault("password", "")
    personal.setdefault("phone", "")
    personal.setdefault("address", "")
    personal.setdefault("city", "")
    personal.setdefault("province_state", "")
    personal.setdefault("country", "")
    personal.setdefault("postal_code", "")
    personal.setdefault("linkedin_url", "")
    personal.setdefault("github_url", "")
    personal.setdefault("portfolio_url", "")
    personal.setdefault("website_url", "")

    authorized = _safe_get(work_auth, "legally_authorized_to_work", "legally_authorized")
    sponsorship = _safe_get(work_auth, "require_sponsorship", "needs_sponsorship")
    work_auth["legally_authorized_to_work"] = authorized
    work_auth["legally_authorized"] = authorized
    work_auth["require_sponsorship"] = sponsorship
    work_auth["needs_sponsorship"] = sponsorship
    work_auth.setdefault("work_permit_type", "")

    compensation.setdefault("salary_expectation", "")
    compensation.setdefault("salary_currency", "USD")
    compensation.setdefault("salary_range_min", "")
    compensation.setdefault("salary_range_max", "")
    compensation.setdefault("currency_conversion_note", "")

    experience.setdefault("years_of_experience_total", "")
    current_title = _safe_get(experience, "current_title", "current_job_title")
    experience["current_title"] = current_title
    experience["current_job_title"] = current_title
    experience.setdefault("current_company", "")
    experience.setdefault("education_level", "")
    experience.setdefault("target_role", current_title)

    skills = normalized.setdefault("skills_boundary", {})
    if "languages" in skills and "programming_languages" not in skills:
        skills["programming_languages"] = _coerce_list(skills.get("languages"))
    if "frameworks" not in skills:
        skills["frameworks"] = []
    if "programming_languages" not in skills:
        skills["programming_languages"] = []
    if "tools" not in skills:
        skills["tools"] = []
    if "devops" not in skills:
        skills["devops"] = _coerce_list(skills.get("devops"))
    if "databases" not in skills:
        skills["databases"] = _coerce_list(skills.get("databases"))
    for key, value in list(skills.items()):
        skills[key] = _coerce_list(value)

    resume_facts.setdefault("preserved_companies", [])
    resume_facts.setdefault("preserved_projects", [])
    resume_facts.setdefault("preserved_school", "")
    resume_facts["real_metrics"] = _merge_unique(_coerce_list(resume_facts.get("real_metrics", [])), [])

    availability.setdefault("earliest_start_date", "Immediately")
    availability.setdefault("available_for_full_time", "")
    availability.setdefault("available_for_contract", "")

    ethnicity = _safe_get(eeo, "race_ethnicity", "ethnicity")
    eeo["race_ethnicity"] = ethnicity or "Decline to self-identify"
    eeo["ethnicity"] = ethnicity or "Decline to self-identify"
    eeo.setdefault("gender", "Decline to self-identify")
    eeo.setdefault("veteran_status", "Decline to self-identify")
    eeo.setdefault("disability_status", "Decline to self-identify")

    for role in normalized.get("work_history", []):
        if not isinstance(role, dict):
            continue
        role.setdefault("company", "")
        role.setdefault("position", "")
        role.setdefault("location", "")
        role.setdefault("start_year", _parse_year(role.get("start_date")))
        role.setdefault("end_year", _parse_year(role.get("end_date")))
        role.setdefault("start_date", "")
        role.setdefault("end_date", "")
        role["highlights"] = _coerce_list(role.get("highlights", []))
        role["key_metrics"] = _coerce_list(role.get("key_metrics", []))

    return normalized


def normalize_profile_data(data: dict) -> dict:
    """Normalize either canonical JSON Resume or legacy profile data."""

    if looks_like_resume_json(data):
        return normalize_profile_from_resume_json(data)
    return normalize_legacy_profile(data)


def build_resume_text_from_json(data: dict) -> str:
    """Render a deterministic plain-text resume for LLM-facing consumers."""

    basics = data.get("basics", {}) if isinstance(data.get("basics"), dict) else {}
    location = basics.get("location", {}) if isinstance(basics.get("location"), dict) else {}
    profiles = basics.get("profiles", []) if isinstance(basics.get("profiles"), list) else []
    skills = data.get("skills", []) if isinstance(data.get("skills"), list) else []
    work = data.get("work", []) if isinstance(data.get("work"), list) else []
    education = data.get("education", []) if isinstance(data.get("education"), list) else []
    projects = data.get("projects", []) if isinstance(data.get("projects"), list) else []
    certificates = data.get("certificates", []) if isinstance(data.get("certificates"), list) else []
    publications = data.get("publications", []) if isinstance(data.get("publications"), list) else []

    lines: list[str] = []
    lines.append(_coerce_str(basics.get("name")))
    lines.append(_coerce_str(basics.get("label")))

    location_parts = [
        _coerce_str(location.get("city")),
        _coerce_str(location.get("region")),
        _coerce_str(location.get("countryCode")),
    ]
    location_line = ", ".join(part for part in location_parts if part)
    if location_line:
        lines.append(location_line)

    contact_parts = []
    if basics.get("email"):
        contact_parts.append(_coerce_str(basics.get("email")))
    if basics.get("phone"):
        contact_parts.append(_coerce_str(basics.get("phone")))
    if basics.get("url"):
        contact_parts.append(_coerce_str(basics.get("url")))
    for profile in profiles:
        if isinstance(profile, dict) and _coerce_str(profile.get("url")):
            contact_parts.append(_coerce_str(profile.get("url")))
    if contact_parts:
        lines.append(" | ".join(dict.fromkeys(contact_parts)))
    lines.append("")

    lines.extend(["SUMMARY", _coerce_str(basics.get("summary")) or "N/A", ""])

    lines.append("TECHNICAL SKILLS")
    if skills:
        for entry in skills:
            if not isinstance(entry, dict):
                continue
            label = _coerce_str(entry.get("name")) or "Skills"
            keywords = ", ".join(_coerce_list(entry.get("keywords", []))) or _coerce_str(entry.get("level")) or "N/A"
            lines.append(f"{label}: {keywords}")
    else:
        lines.append("N/A")
    lines.append("")

    lines.append("EXPERIENCE")
    if work:
        for entry in work:
            if not isinstance(entry, dict):
                continue
            date_parts = [_coerce_str(entry.get("startDate")), _coerce_str(entry.get("endDate")) or "Present"]
            date_range = " - ".join(part for part in date_parts if part)
            header_parts = [
                _coerce_str(entry.get("position")),
                _coerce_str(entry.get("name")),
                date_range,
            ]
            header = " | ".join(part for part in header_parts if part)
            lines.append(header or "Untitled role")
            subtitle_parts = [_coerce_str(entry.get("location")), _coerce_str(entry.get("url"))]
            subtitle = " | ".join(part for part in subtitle_parts if part)
            if subtitle:
                lines.append(subtitle)
            bullets = []
            if _coerce_str(entry.get("summary")):
                bullets.append(_coerce_str(entry.get("summary")))
            bullets.extend(_coerce_list(entry.get("highlights", [])))
            if not bullets:
                bullets.append("N/A")
            for bullet in bullets:
                lines.append(f"- {bullet}")
            lines.append("")
    else:
        lines.extend(["N/A", ""])

    lines.append("PROJECTS")
    if projects:
        for entry in projects:
            if not isinstance(entry, dict):
                continue
            lines.append(_coerce_str(entry.get("name")) or "Untitled project")
            subtitle_parts = []
            date_parts = [_coerce_str(entry.get("startDate")), _coerce_str(entry.get("endDate"))]
            if any(date_parts):
                subtitle_parts.append(" - ".join(part for part in date_parts if part))
            if _coerce_str(entry.get("url")):
                subtitle_parts.append(_coerce_str(entry.get("url")))
            if subtitle_parts:
                lines.append(" | ".join(subtitle_parts))
            bullets = []
            if _coerce_str(entry.get("description")):
                bullets.append(_coerce_str(entry.get("description")))
            bullets.extend(_coerce_list(entry.get("highlights", [])))
            if not bullets:
                bullets.append("N/A")
            for bullet in bullets:
                lines.append(f"- {bullet}")
            lines.append("")
    else:
        lines.extend(["N/A", ""])

    lines.append("EDUCATION")
    if education:
        for entry in education:
            if not isinstance(entry, dict):
                continue
            parts = [
                _coerce_str(entry.get("institution")),
                _coerce_str(entry.get("studyType")),
                _coerce_str(entry.get("area")),
                _coerce_str(entry.get("endDate")),
            ]
            lines.append(" | ".join(part for part in parts if part) or "N/A")
    else:
        lines.append("N/A")

    if certificates:
        lines.extend(["", "CERTIFICATES"])
        for entry in certificates:
            if not isinstance(entry, dict):
                continue
            parts = [
                _coerce_str(entry.get("name")),
                _coerce_str(entry.get("issuer")),
                _coerce_str(entry.get("date")),
            ]
            lines.append(" | ".join(part for part in parts if part) or "N/A")

    if publications:
        lines.extend(["", "PUBLICATIONS"])
        for entry in publications:
            if not isinstance(entry, dict):
                continue
            parts = [
                _coerce_str(entry.get("name")),
                _coerce_str(entry.get("publisher")),
                _coerce_str(entry.get("releaseDate")),
            ]
            lines.append(" | ".join(part for part in parts if part) or "N/A")
            summary = _coerce_str(entry.get("summary"))
            if summary:
                lines.append(f"- {summary}")

    return "\n".join(lines).strip() + "\n"


def resolve_render_theme(data: dict, explicit_theme: str | None = None) -> str:
    """Resolve the preferred render theme from CLI and metadata."""

    if explicit_theme and explicit_theme.strip():
        return explicit_theme.strip()
    meta = data.get("meta", {}) if isinstance(data.get("meta"), dict) else {}
    applypilot = meta.get("applypilot", {}) if isinstance(meta.get("applypilot"), dict) else {}
    render_cfg = applypilot.get("render", {}) if isinstance(applypilot.get("render"), dict) else {}
    return _coerce_str(render_cfg.get("theme")) or _coerce_str(meta.get("theme")) or DEFAULT_RENDER_THEME


@dataclass(frozen=True)
class CanonicalResumeSource:
    """Detected canonical/legacy resume storage mode."""

    mode: str
    path: Path | None
    detail: str = ""

