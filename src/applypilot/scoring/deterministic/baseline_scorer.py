"""Deterministic baseline scoring — pure computation, no LLM."""

from __future__ import annotations

import logging
import re

from applypilot.config import load_profile
from applypilot.resume.extraction import get_profile_skill_keywords
from applypilot.scoring.deterministic.job_context_extractor import extract_requirement_focused_text
from applypilot.scoring.deterministic.skill_overlap import contains_phrase, extract_known_skills
from applypilot.scoring.deterministic.title_matcher import (
    infer_role_family,
    jaccard_similarity,
    seniority_from_text,
    title_key,
    tokenize_set,
)

__all__ = [
    "HARD_MISMATCH_TERMS",
    "build_scoring_profile",
    "load_scoring_profile",
    "compute_deterministic_baseline",
]

log = logging.getLogger(__name__)

HARD_MISMATCH_TERMS = (
    "clearance",
    "active license",
    "board certification",
    "bar admission",
    "registered nurse",
    "rn license",
    "medical doctor",
    "cpa required",
    "citizenship required",
)


# ── Private helpers ───────────────────────────────────────────────────────


def _coerce_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _coerce_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value in (None, ""):
        return []
    return [str(value).strip()]


def _to_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"\d+(?:\.\d+)?", str(value))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


# ── Public API ────────────────────────────────────────────────────────────


def build_scoring_profile(profile: dict) -> dict:
    experience = profile.get("experience", {}) if isinstance(profile.get("experience"), dict) else {}
    target_role = _coerce_text(experience.get("target_role"))
    years_total = _to_float(experience.get("years_of_experience_total")) or 0.0
    work_entries = profile.get("work", []) if isinstance(profile.get("work"), list) else []

    current_titles: list[str] = []
    for item in work_entries[:4]:
        if not isinstance(item, dict):
            continue
        title = _coerce_text(item.get("position"))
        if title and title not in current_titles:
            current_titles.append(title)

    profile_skills = [skill.lower() for skill in get_profile_skill_keywords(profile)]
    for item in work_entries:
        if not isinstance(item, dict):
            continue
        for tech in _coerce_list(item.get("technologies")):
            lower = tech.lower()
            if lower and lower not in profile_skills:
                profile_skills.append(lower)

    profile_known_skills: set[str] = set()
    for skill in profile_skills:
        matched = extract_known_skills(skill)
        if matched:
            profile_known_skills.update(matched)
        elif skill:
            profile_known_skills.add(skill)

    role_text = " ".join([target_role, *current_titles]).strip()
    role_family = infer_role_family(role_text)
    seniority_from_titles = seniority_from_text(role_text)
    seniority_from_years = 1
    if years_total >= 11:
        seniority_from_years = 4
    elif years_total >= 7:
        seniority_from_years = 3
    elif years_total >= 3:
        seniority_from_years = 2
    profile_seniority = max(seniority_from_titles, seniority_from_years)

    return {
        "target_role": target_role,
        "years_total": years_total,
        "current_titles": current_titles,
        "skills": profile_skills,
        "known_skills": sorted(profile_known_skills),
        "role_tokens": tokenize_set(role_text),
        "role_family": role_family,
        "seniority": profile_seniority,
    }


def load_scoring_profile() -> dict:
    try:
        profile = load_profile()
        return build_scoring_profile(profile)
    except Exception as exc:
        log.warning("Falling back to minimal scoring profile because profile load failed: %s", exc)
        return {
            "target_role": "",
            "years_total": 0.0,
            "current_titles": [],
            "skills": [],
            "known_skills": [],
            "role_tokens": set(),
            "role_family": "unknown",
            "seniority": 2,
        }


def compute_deterministic_baseline(scoring_profile: dict, job: dict) -> dict:
    title = _coerce_text(job.get("title"))
    description = _coerce_text(job.get("full_description") or job.get("description"))
    focused_description = extract_requirement_focused_text(description, max_chars=7000)
    job_text = f"{title}\n{focused_description}".strip()
    job_text_lower = job_text.lower()

    title_tokens = tokenize_set(title)
    role_tokens = scoring_profile.get("role_tokens", set()) or set()
    title_similarity = jaccard_similarity(title_tokens, role_tokens)

    profile_known_skills = set(scoring_profile.get("known_skills") or [])
    job_known_skills = extract_known_skills(job_text)
    matched_known_skills = sorted(job_known_skills & profile_known_skills)
    missing_requirements = sorted(job_known_skills - profile_known_skills)

    # Adjacent skill matches — partial credit for skills related to yours
    adjacent_matches = []
    try:
        from applypilot.bootstrap import get_app

        graph = get_app().container.skill_graph
        for req in missing_requirements:
            edge = graph.resolve(req.lower(), {s.lower() for s in profile_known_skills})
            if edge and edge.confidence >= 0.7:
                adjacent_matches.append(req)
    except Exception:
        pass

    profile_custom_skills = [
        skill for skill in scoring_profile.get("skills", []) if skill not in profile_known_skills and len(skill) >= 3
    ]
    matched_custom_skills = sorted(skill for skill in profile_custom_skills if contains_phrase(job_text_lower, skill))

    if job_known_skills:
        # Adjacent matches count at 50% weight
        effective_matches = len(matched_known_skills) + len(adjacent_matches) * 0.5
        skill_overlap = effective_matches / max(1, len(job_known_skills))
    else:
        skill_overlap = min(1.0, len(matched_custom_skills) / 4.0)

    # Remove adjacent matches from missing_requirements
    missing_requirements = sorted(set(missing_requirements) - set(adjacent_matches))

    matched_skill_count = len(set(matched_known_skills + matched_custom_skills))
    missing_requirement_count = len(missing_requirements)
    if matched_skill_count + missing_requirement_count > 0:
        requirement_coverage = matched_skill_count / (matched_skill_count + missing_requirement_count)
    else:
        requirement_coverage = skill_overlap

    profile_family = _coerce_text(scoring_profile.get("role_family") or "unknown")
    job_family = infer_role_family(job_text)
    domain_mismatch_penalty_raw = 0.0
    if profile_family != "unknown" and job_family != "unknown" and profile_family != job_family:
        domain_mismatch_penalty_raw = 2.0

    # Generic evidence score: avoids hardcoded role exceptions while rewarding demonstrable overlap.
    evidence_strength = max(
        0.0,
        min(
            1.0,
            0.45 * skill_overlap
            + 0.30 * min(1.0, matched_skill_count / 6.0)
            + 0.20 * requirement_coverage
            + 0.05 * title_similarity,
        ),
    )
    domain_penalty_scale = max(0.35, 1.0 - 0.70 * evidence_strength)
    domain_mismatch_penalty = domain_mismatch_penalty_raw * domain_penalty_scale

    profile_seniority = int(scoring_profile.get("seniority") or 2)
    job_seniority = seniority_from_text(title)
    seniority_gap = job_seniority - profile_seniority
    if seniority_gap >= 3:
        seniority_component = -2.0
    elif seniority_gap == 2:
        seniority_component = -1.0
    elif seniority_gap <= -3:
        seniority_component = -0.5
    else:
        seniority_component = 0.8

    role_family_bonus = (
        1.0 if profile_family == job_family and profile_family in {"software_engineering", "data_ai"} else 0.0
    )

    raw_score = (
            1.5
            + 3.0 * title_similarity
            + 4.0 * skill_overlap
            + seniority_component
            + role_family_bonus
            - domain_mismatch_penalty
    )
    if title_similarity >= 0.3 and skill_overlap >= 0.3:
        raw_score += 0.7
    if job_known_skills and not matched_known_skills:
        raw_score -= 0.6

    baseline_score = int(round(max(0.0, min(10.0, raw_score))))
    matched_skills = sorted(set(matched_known_skills + matched_custom_skills))[:12]
    is_software_engineering_role = job_family == "software_engineering"

    return {
        "score": baseline_score,
        "title_similarity": round(title_similarity, 3),
        "skill_overlap": round(skill_overlap, 3),
        "matched_skills": matched_skills,
        "missing_requirements": missing_requirements[:10],
        "matched_skill_count": matched_skill_count,
        "missing_requirement_count": missing_requirement_count,
        "requirement_coverage": round(requirement_coverage, 3),
        "evidence_strength": round(evidence_strength, 3),
        "seniority_gap": seniority_gap,
        "profile_role_family": profile_family,
        "job_role_family": job_family,
        "domain_mismatch_penalty": domain_mismatch_penalty,
        "is_software_engineering_role": is_software_engineering_role,
        "focused_description": focused_description,
        "normalized_title": title_key(title),
    }
