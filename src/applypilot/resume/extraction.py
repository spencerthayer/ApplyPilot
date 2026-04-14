"""Resume profile extraction — extract structured data from JSON Resume."""

from __future__ import annotations

from typing import Any


def _coerce_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _coerce_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_coerce_str(item) for item in value if _coerce_str(item)]
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return []


def _merge_unique(base: list[str], extra: list[str]) -> list[str]:
    seen = set(s.lower() for s in base)
    result = list(base)
    for item in extra:
        if item.lower() not in seen:
            result.append(item)
            seen.add(item.lower())
    return result


def get_profile_skill_sections(profile: dict) -> list[tuple[str, list[str]]]:
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
    keywords: list[str] = []
    for _, section_keywords in get_profile_skill_sections(profile):
        keywords = _merge_unique(keywords, section_keywords)
    return keywords


def get_profile_company_names(profile: dict) -> list[str]:
    companies: list[str] = []
    for job in profile.get("work", []):
        if not isinstance(job, dict):
            continue
        company = _coerce_str(job.get("company"))
        if company and company not in companies:
            companies.append(company)
    return companies


def get_profile_project_names(profile: dict) -> list[str]:
    projects: list[str] = []
    for project in profile.get("projects", []):
        if not isinstance(project, dict):
            continue
        name = _coerce_str(project.get("name"))
        if name and name not in projects:
            projects.append(name)
    return projects


def get_profile_school_names(profile: dict) -> list[str]:
    schools: list[str] = []
    for edu in profile.get("education", []):
        if not isinstance(edu, dict):
            continue
        institution = _coerce_str(edu.get("institution"))
        if institution and institution not in schools:
            schools.append(institution)
    return schools


def get_profile_verified_metrics(profile: dict) -> list[str]:
    metrics: list[str] = []
    for job in profile.get("work", []):
        if not isinstance(job, dict):
            continue
        metrics = _merge_unique(metrics, _coerce_list(job.get("key_metrics", [])))
    return metrics
