"""Track discovery and management (LLD §3.3, INIT-11/12/13, P2).

Discovers career tracks from the master profile using skill clustering.
Each track is a curated subset of the master profile — filtered skills,
experiences, and projects.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Track:
    track_id: str
    name: str
    skills: list[str] = field(default_factory=list)
    active: bool = True


def discover_tracks(profile: dict) -> list[Track]:
    """Analyze profile and identify career tracks from skill clusters.

    P2 implementation: uses skill keyword grouping from profile sections.
    Future: LLM-assisted track discovery.
    """
    from applypilot.scoring.deterministic.skill_overlap import extract_known_skills

    # Collect skills per role family from work entries
    work = profile.get("work", [])
    role_skills: dict[str, set[str]] = {}

    for entry in work:
        position = (entry.get("position") or "").lower()
        family = _infer_family(position)

        # From explicit technologies field
        techs = {t.lower() for t in entry.get("technologies", [])}

        # From highlights text — extract known skills
        for h in entry.get("highlights", []):
            techs.update(extract_known_skills(h))

        # From x-applypilot.key_metrics context
        for m in entry.get("x-applypilot", {}).get("key_metrics", []):
            techs.update(extract_known_skills(m))

        if techs:
            role_skills.setdefault(family, set()).update(techs)

    # Also map skills section keywords to families
    for skill_group in profile.get("skills", []):
        group_name = (skill_group.get("name") or "").lower()
        keywords = [k.lower() for k in skill_group.get("keywords", [])]
        if not keywords:
            continue
        # Infer family from group name
        family = _infer_skill_group_family(group_name)
        if family:
            role_skills.setdefault(family, set()).update(keywords)

    if not role_skills:
        all_skills = []
        for group in profile.get("skills", []):
            all_skills.extend(k.lower() for k in group.get("keywords", []))
        return [
            Track(
                track_id=uuid.uuid4().hex[:8],
                name=profile.get("experience", {}).get("target_role", "General"),
                skills=all_skills,
            )
        ]

    return [
        Track(
            track_id=uuid.uuid4().hex[:8],
            name=family.replace("_", " ").title(),
            skills=sorted(skills),
        )
        for family, skills in role_skills.items()
    ]


def _infer_skill_group_family(group_name: str) -> str | None:
    """Map a skills section name to a role family."""
    match group_name:
        case g if any(kw in g for kw in ("mobile", "android", "ios")):
            return "mobile"
        case g if any(kw in g for kw in ("backend", "server", "api")):
            return "backend_engineering"
        case g if any(kw in g for kw in ("devops", "cloud", "infra")):
            return "devops_sre"
        case g if any(kw in g for kw in ("ml", "ai", "machine")):
            return "data_ml"
        case g if any(kw in g for kw in ("architect", "design", "system")):
            return "software_engineering"
        case _:
            return None


def _infer_family(position: str) -> str:
    """Infer role family from position title."""
    match position:
        case p if any(kw in p for kw in ("serverless", "lambda", "step function")):
            return "serverless_cloud"
        case p if any(kw in p for kw in ("backend", "api", "platform")):
            return "backend_engineering"
        case p if any(kw in p for kw in ("frontend", "ui", "react", "angular")):
            return "frontend_engineering"
        case p if any(kw in p for kw in ("fullstack", "full stack", "full-stack")):
            return "fullstack_engineering"
        case p if any(kw in p for kw in ("devops", "sre", "infrastructure", "cloud")):
            return "devops_sre"
        case p if any(kw in p for kw in ("data", "ml", "machine learning", "ai")):
            return "data_ml"
        case p if any(kw in p for kw in ("mobile", "android", "ios")):
            return "mobile"
        case p if any(kw in p for kw in ("manager", "lead", "director")):
            return "engineering_management"
        case _:
            return "software_engineering"
