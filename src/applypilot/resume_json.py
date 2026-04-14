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
    if any(key in data for key in ("basics", "work", "skills", "projects")):
        return True
    meta = data.get("meta")
    return isinstance(meta, dict) and any(key in meta for key in ("canonical", "version", "theme", "applypilot"))


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
            errors.extend(_collect_schema_errors(extension, _WORK_EXTENSION_SCHEMA, ["work", index, "x-applypilot"]))
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


def _primary_role_from_label(label: str) -> str:
    """Return a single target-role candidate from a potentially multi-role label."""

    value = _coerce_str(label)
    if not value:
        return ""
    parts = [part.strip() for part in re.split(r"[;,|]", value) if part.strip()]
    if parts:
        return parts[0]
    return value


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


def _skill_label_from_boundary_key(key: str) -> str:
    label_map = {
        "programming_languages": "Programming Languages",
        "frameworks": "Frameworks & Libraries",
        "devops": "DevOps & Infra",
        "databases": "Databases",
        "tools": "Tools & Platforms",
    }
    return label_map.get(key, key.replace("_", " ").title())


def _normalize_work_entries(work: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in work:
        if not isinstance(item, dict):
            continue
        extension = item.get("x-applypilot", {}) if isinstance(item.get("x-applypilot"), dict) else {}
        normalized.append(
            {
                "company": _coerce_str(_safe_get(item, "company", "name")),
                "position": _coerce_str(_safe_get(item, "position", "title")),
                "location": _coerce_str(item.get("location")),
                "start_date": _coerce_str(_safe_get(item, "startDate", "start_date", "start_year")),
                "end_date": _coerce_str(_safe_get(item, "endDate", "end_date", "end_year")),
                "summary": _coerce_str(item.get("summary")),
                "highlights": _coerce_list(item.get("highlights", [])),
                "key_metrics": _coerce_list(extension.get("key_metrics", item.get("key_metrics", []))),
                "technologies": _coerce_list(item.get("technologies", [])),
            }
        )
    return normalized


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


def _normalize_skills(skills: list[dict[str, Any]]) -> list[dict[str, list[str]]]:
    normalized: list[dict[str, list[str]]] = []
    for item in skills:
        if not isinstance(item, dict):
            continue
        name = _coerce_str(item.get("name"))
        keywords = _coerce_list(item.get("keywords", []))
        if not name and not keywords:
            continue
        normalized.append({"name": name or "Skills", "keywords": keywords})
    return normalized


def _skills_from_boundary(boundary: dict[str, Any]) -> list[dict[str, list[str]]]:
    normalized: list[dict[str, list[str]]] = []
    for key, value in boundary.items():
        keywords = _coerce_list(value)
        if keywords:
            normalized.append({"name": _skill_label_from_boundary_key(key), "keywords": keywords})
    return normalized


def _normalize_projects(projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in projects:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "name": _coerce_str(item.get("name")),
                "description": _coerce_str(_safe_get(item, "description", "summary")),
                "highlights": _coerce_list(item.get("highlights", [])),
                "url": _coerce_str(item.get("url")),
            }
        )
    return normalized


def _merge_unique(base: list[str], extra: list[str]) -> list[str]:
    merged = list(base)
    for item in extra:
        if item and item not in merged:
            merged.append(item)
    return merged


def _normalize_personal_from_resume_json(
    basics: dict[str, Any],
    location: dict[str, Any],
    personal_meta: dict[str, Any],
) -> dict[str, str]:
    profile_urls = _profile_urls(basics.get("profiles", []) if isinstance(basics.get("profiles"), list) else [])
    website_url = _coerce_str(personal_meta.get("website_url")) or _coerce_str(basics.get("url"))
    linkedin_url = _coerce_str(personal_meta.get("linkedin_url")) or profile_urls["linkedin_url"]
    github_url = _coerce_str(personal_meta.get("github_url")) or profile_urls["github_url"]
    portfolio_url = _coerce_str(personal_meta.get("portfolio_url")) or profile_urls["portfolio_url"]
    return {
        "full_name": _coerce_str(basics.get("name")),
        "preferred_name": _coerce_str(personal_meta.get("preferred_name")),
        "email": _coerce_str(basics.get("email")),
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
    }


def _select_current_role(work: list[dict[str, Any]]) -> dict[str, Any]:
    def sort_key(entry: dict[str, Any]) -> tuple[int, int, int]:
        end_year = _parse_year(entry.get("end_date"))
        start_year = _parse_year(entry.get("start_date"))
        is_current = 1 if not _coerce_str(entry.get("end_date")) else 0
        return (is_current, end_year or 0, start_year or 0)

    valid = [entry for entry in work if isinstance(entry, dict)]
    if not valid:
        return {}
    return sorted(valid, key=sort_key, reverse=True)[0]


def normalize_profile_settings(profile: dict) -> dict:
    """Normalize ApplyPilot settings and drop resume-derived content."""

    source = profile if isinstance(profile, dict) else {}
    normalized = {
        "work_authorization": copy.deepcopy(source.get("work_authorization", {})),
        "compensation": copy.deepcopy(source.get("compensation", {})),
        "availability": copy.deepcopy(source.get("availability", {})),
        "eeo_voluntary": copy.deepcopy(source.get("eeo_voluntary", {})),
        "tailoring_config": copy.deepcopy(source.get("tailoring_config", {})),
        "files": copy.deepcopy(source.get("files", {})),
    }

    work_auth = normalized["work_authorization"]
    compensation = normalized["compensation"]
    availability = normalized["availability"]
    eeo = normalized["eeo_voluntary"]
    if not isinstance(normalized["tailoring_config"], dict):
        normalized["tailoring_config"] = {}
    if not isinstance(normalized["files"], dict):
        normalized["files"] = {}

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

    availability.setdefault("earliest_start_date", "Immediately")
    availability.setdefault("available_for_full_time", "")
    availability.setdefault("available_for_contract", "")

    ethnicity = _safe_get(eeo, "race_ethnicity", "ethnicity")
    eeo["race_ethnicity"] = ethnicity or "Decline to self-identify"
    eeo["ethnicity"] = ethnicity or "Decline to self-identify"
    eeo.setdefault("gender", "Decline to self-identify")
    eeo.setdefault("veteran_status", "Decline to self-identify")
    eeo.setdefault("disability_status", "Decline to self-identify")

    return normalized


def settings_from_resume_json(data: dict) -> dict:
    """Extract profile.json settings from canonical resume metadata."""

    meta = data.get("meta", {}) if isinstance(data.get("meta"), dict) else {}
    applypilot = meta.get("applypilot", {}) if isinstance(meta.get("applypilot"), dict) else {}
    return normalize_profile_settings(applypilot)


def normalize_profile_from_resume_json(data: dict, settings: dict | None = None) -> dict:
    """Map JSON Resume plus ApplyPilot extensions into the runtime profile contract."""

    basics = data.get("basics", {}) if isinstance(data.get("basics"), dict) else {}
    location = basics.get("location", {}) if isinstance(basics.get("location"), dict) else {}
    meta = data.get("meta", {}) if isinstance(data.get("meta"), dict) else {}
    applypilot = meta.get("applypilot", {}) if isinstance(meta.get("applypilot"), dict) else {}
    personal_meta = applypilot.get("personal", {}) if isinstance(applypilot.get("personal"), dict) else {}
    work_entries = data.get("work", []) if isinstance(data.get("work"), list) else []
    education_entries = data.get("education", []) if isinstance(data.get("education"), list) else []
    skills_entries = data.get("skills", []) if isinstance(data.get("skills"), list) else []
    projects_entries = data.get("projects", []) if isinstance(data.get("projects"), list) else []

    work = _normalize_work_entries(work_entries)
    education, education_level = _normalize_education(education_entries)
    skills = _normalize_skills(skills_entries)
    projects = _normalize_projects(projects_entries)
    current_work = _select_current_role(work)

    experience_total = _coerce_str(applypilot.get("years_of_experience_total")) or _compute_years_experience(
        work_entries
    )
    target_role = (
        _coerce_str(applypilot.get("target_role"))
        or _primary_role_from_label(_coerce_str(basics.get("label")))
        or _coerce_str(current_work.get("position"))
    )
    current_title = _coerce_str(current_work.get("position"))
    current_company = _coerce_str(current_work.get("company"))

    profile_settings = normalize_profile_settings(settings or applypilot)
    return {
        "personal": _normalize_personal_from_resume_json(basics, location, personal_meta),
        "work_authorization": profile_settings["work_authorization"],
        "availability": profile_settings["availability"],
        "compensation": profile_settings["compensation"],
        "experience": {
            "years_of_experience_total": experience_total,
            "education_level": education_level,
            "current_title": current_title,
            "current_job_title": current_title,
            "current_company": current_company,
            "target_role": target_role,
        },
        "work": work,
        "education": education,
        "skills": skills,
        "projects": projects,
        "eeo_voluntary": profile_settings["eeo_voluntary"],
        "tailoring_config": profile_settings["tailoring_config"],
        "files": profile_settings["files"],
    }


def normalize_legacy_profile(profile: dict) -> dict:
    """Normalize legacy profile.json payloads into the runtime profile contract."""

    raw = copy.deepcopy(profile if isinstance(profile, dict) else {})
    personal_raw = raw.get("personal", {}) if isinstance(raw.get("personal"), dict) else {}
    experience_raw = raw.get("experience", {}) if isinstance(raw.get("experience"), dict) else {}
    education_raw = raw.get("education", []) if isinstance(raw.get("education"), list) else []
    work_history_raw = raw.get("work_history", []) if isinstance(raw.get("work_history"), list) else []
    skills_boundary = raw.get("skills_boundary", {}) if isinstance(raw.get("skills_boundary"), dict) else {}
    project_raw = raw.get("projects", []) if isinstance(raw.get("projects"), list) else []
    project_highlights = raw.get("project_highlights", []) if isinstance(raw.get("project_highlights"), list) else []

    work = _normalize_work_entries(work_history_raw)
    education, education_level = _normalize_education(education_raw)
    skills = _normalize_skills(raw.get("skills", [])) if isinstance(raw.get("skills"), list) else []
    if not skills:
        if "languages" in skills_boundary and "programming_languages" not in skills_boundary:
            skills_boundary["programming_languages"] = _coerce_list(skills_boundary.get("languages"))
        skills = _skills_from_boundary(skills_boundary)

    projects = _normalize_projects(project_raw)
    if not projects and project_highlights:
        projects = _normalize_projects(project_highlights)

    current_work = _select_current_role(work)
    current_title = _coerce_str(_safe_get(experience_raw, "current_title", "current_job_title")) or _coerce_str(
        current_work.get("position")
    )
    current_company = _coerce_str(experience_raw.get("current_company")) or _coerce_str(current_work.get("company"))
    years_experience = _coerce_str(experience_raw.get("years_of_experience_total")) or _compute_years_experience(
        [{"startDate": role.get("start_date", "")} for role in work]
    )

    settings = normalize_profile_settings(raw)

    return {
        "personal": {
            "full_name": _coerce_str(personal_raw.get("full_name")),
            "preferred_name": _coerce_str(personal_raw.get("preferred_name")),
            "email": _coerce_str(personal_raw.get("email")),
            "phone": _coerce_str(personal_raw.get("phone")),
            "address": _coerce_str(personal_raw.get("address")),
            "city": _coerce_str(personal_raw.get("city")),
            "province_state": _coerce_str(personal_raw.get("province_state")),
            "country": _coerce_str(personal_raw.get("country")),
            "postal_code": _coerce_str(personal_raw.get("postal_code")),
            "linkedin_url": _coerce_str(personal_raw.get("linkedin_url")),
            "github_url": _coerce_str(personal_raw.get("github_url")),
            "portfolio_url": _coerce_str(personal_raw.get("portfolio_url")),
            "website_url": _coerce_str(personal_raw.get("website_url")),
        },
        "work_authorization": settings["work_authorization"],
        "availability": settings["availability"],
        "compensation": settings["compensation"],
        "experience": {
            "years_of_experience_total": years_experience,
            "education_level": _coerce_str(experience_raw.get("education_level")) or education_level,
            "current_title": current_title,
            "current_job_title": current_title,
            "current_company": current_company,
            "target_role": _coerce_str(experience_raw.get("target_role")) or current_title,
        },
        "work": work,
        "education": education,
        "skills": skills,
        "projects": projects,
        "eeo_voluntary": settings["eeo_voluntary"],
        "tailoring_config": settings["tailoring_config"],
        "files": settings["files"],
    }


def _set_if_missing(mapping: dict[str, Any], key: str, value: Any) -> bool:
    if value in (None, "", [], {}):
        return False
    if mapping.get(key) not in (None, "", [], {}):
        return False
    mapping[key] = copy.deepcopy(value)
    return True


def _ensure_profile_url(profiles: list[dict[str, Any]], network: str, url: str) -> bool:
    if not url:
        return False
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        if _coerce_str(profile.get("url")) == url:
            return False
        if _coerce_str(profile.get("network")).lower() == network.lower():
            return False
    profiles.append({"network": network, "url": url})
    return True


def merge_resume_json_with_legacy_profile(data: dict, profile: dict) -> tuple[dict, bool]:
    """Backfill missing canonical resume fields from a legacy profile payload."""

    merged = copy.deepcopy(data if isinstance(data, dict) else {})
    legacy = normalize_legacy_profile(profile)
    changed = False

    basics = merged.setdefault("basics", {})
    location = basics.setdefault("location", {})
    profiles = basics.setdefault("profiles", [])
    if not isinstance(profiles, list):
        profiles = []
        basics["profiles"] = profiles
    meta = merged.setdefault("meta", {})
    applypilot = meta.setdefault("applypilot", {})
    personal_meta = applypilot.setdefault("personal", {})
    personal = legacy.get("personal", {})
    experience = legacy.get("experience", {})

    changed |= _set_if_missing(basics, "name", personal.get("full_name"))
    changed |= _set_if_missing(basics, "email", personal.get("email"))
    changed |= _set_if_missing(basics, "phone", personal.get("phone"))
    changed |= _set_if_missing(basics, "url", personal.get("website_url"))
    changed |= _set_if_missing(location, "address", personal.get("address"))
    changed |= _set_if_missing(location, "city", personal.get("city"))
    changed |= _set_if_missing(location, "region", personal.get("province_state"))
    changed |= _set_if_missing(location, "countryCode", personal.get("country"))
    changed |= _set_if_missing(location, "postalCode", personal.get("postal_code"))
    changed |= _set_if_missing(personal_meta, "preferred_name", personal.get("preferred_name"))
    changed |= _set_if_missing(personal_meta, "address", personal.get("address"))
    changed |= _set_if_missing(personal_meta, "province_state", personal.get("province_state"))
    changed |= _set_if_missing(personal_meta, "country", personal.get("country"))
    changed |= _set_if_missing(personal_meta, "postal_code", personal.get("postal_code"))
    changed |= _set_if_missing(personal_meta, "linkedin_url", personal.get("linkedin_url"))
    changed |= _set_if_missing(personal_meta, "github_url", personal.get("github_url"))
    changed |= _set_if_missing(personal_meta, "portfolio_url", personal.get("portfolio_url"))
    changed |= _set_if_missing(personal_meta, "website_url", personal.get("website_url"))
    changed |= _ensure_profile_url(profiles, "LinkedIn", _coerce_str(personal.get("linkedin_url")))
    changed |= _ensure_profile_url(profiles, "GitHub", _coerce_str(personal.get("github_url")))
    changed |= _ensure_profile_url(profiles, "Portfolio", _coerce_str(personal.get("portfolio_url")))

    changed |= _set_if_missing(applypilot, "target_role", experience.get("target_role"))
    changed |= _set_if_missing(applypilot, "years_of_experience_total", experience.get("years_of_experience_total"))
    for key in ("work_authorization", "compensation", "availability", "eeo_voluntary", "tailoring_config", "files"):
        changed |= _set_if_missing(applypilot, key, legacy.get(key))

    if not merged.get("work") and legacy.get("work"):
        merged["work"] = [
            {
                "name": role.get("company", ""),
                "position": role.get("position", ""),
                "location": role.get("location", ""),
                "startDate": role.get("start_date", ""),
                "endDate": role.get("end_date", ""),
                "summary": role.get("summary", ""),
                "highlights": role.get("highlights", []),
                "x-applypilot": {"key_metrics": role.get("key_metrics", [])},
            }
            for role in legacy["work"]
        ]
        changed = True

    if not merged.get("education") and legacy.get("education"):
        merged["education"] = copy.deepcopy(legacy["education"])
        changed = True

    if not merged.get("skills") and legacy.get("skills"):
        merged["skills"] = copy.deepcopy(legacy["skills"])
        changed = True

    if not merged.get("projects") and legacy.get("projects"):
        merged["projects"] = copy.deepcopy(legacy["projects"])
        changed = True

    return merged, changed


def normalize_profile_data(data: dict) -> dict:
    """Normalize either canonical JSON Resume or legacy profile data."""

    if looks_like_resume_json(data):
        return normalize_profile_from_resume_json(data)
    return normalize_legacy_profile(data)


def get_profile_skill_sections(profile: dict) -> list[tuple[str, list[str]]]:
    """Return normalized skill sections as (label, keywords) pairs."""

    sections: list[tuple[str, list[str]]] = []
    for item in profile.get("skills", []):
        if not isinstance(item, dict):
            continue
        label = _coerce_str(item.get("name")) or "Skills"
        keywords = _coerce_list(item.get("keywords", []))
        if keywords:
            sections.append((label, keywords))
    return sections


def get_profile_skill_keywords(profile: dict) -> list[str]:
    """Return a deduplicated flat list of allowed skills."""

    keywords: list[str] = []
    for _, section_keywords in get_profile_skill_sections(profile):
        keywords = _merge_unique(keywords, section_keywords)
    return keywords


def get_profile_company_names(profile: dict) -> list[str]:
    """Return company names from the normalized work history."""

    companies: list[str] = []
    for job in profile.get("work", []):
        if not isinstance(job, dict):
            continue
        company = _coerce_str(job.get("company"))
        if company and company not in companies:
            companies.append(company)
    return companies


def get_profile_project_names(profile: dict) -> list[str]:
    """Return project names from the normalized runtime profile."""

    projects: list[str] = []
    for project in profile.get("projects", []):
        if not isinstance(project, dict):
            continue
        name = _coerce_str(project.get("name"))
        if name and name not in projects:
            projects.append(name)
    return projects


def get_profile_school_names(profile: dict) -> list[str]:
    """Return institution names from the normalized education section."""

    schools: list[str] = []
    for edu in profile.get("education", []):
        if not isinstance(edu, dict):
            continue
        institution = _coerce_str(edu.get("institution"))
        if institution and institution not in schools:
            schools.append(institution)
    return schools


def get_profile_verified_metrics(profile: dict) -> list[str]:
    """Return deduplicated verified metrics from normalized work entries."""

    metrics: list[str] = []
    for job in profile.get("work", []):
        if not isinstance(job, dict):
            continue
        metrics = _merge_unique(metrics, _coerce_list(job.get("key_metrics", [])))
    return metrics


def build_resume_text_from_json(data: dict) -> str:
    """Render a deterministic plain-text resume for LLM-facing consumers.

    Delegates to ResumeBuilder (builder pattern) for consistent rendering.
    """
    from applypilot.resume_builder import from_json_resume

    return from_json_resume(data).render_text() + "\n"


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
