"""Track discovery — LLM-driven + user-guided hybrid (INIT-11).

Flow:
  1. LLM analyzes profile and suggests tracks with confidence
  2. User reviews: confirm, remove, rename, add custom tracks
  3. Tracks with insufficient profile data get flagged (user can still keep them)
  4. Per-track base resumes generated only for tracks with enough data
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

_DISCOVER_PROMPT = """\
You are a career strategist analyzing a professional's resume to identify \
distinct career tracks they could pursue.

Resume (JSON Resume format):
{resume_json}

Analyze this profile and identify 2-6 distinct career tracks this person \
could realistically target. For each track:
1. Name it clearly (e.g. "Android Engineering", "Backend/API Development")
2. List the relevant skills FROM THEIR PROFILE that support this track
3. Rate data_strength as "strong", "moderate", or "weak" based on how much \
   evidence exists in their profile for this track
4. Note any skill gaps that would need filling

Return JSON:
{{
  "tracks": [
    {{
      "name": "Track Name",
      "skills": ["skill1", "skill2"],
      "data_strength": "strong|moderate|weak",
      "supporting_experience": "which roles/projects support this",
      "gaps": ["missing skill 1", "missing skill 2"]
    }}
  ]
}}

Only return the JSON, nothing else."""


@dataclass
class DiscoveredTrack:
    track_id: str
    name: str
    skills: list[str] = field(default_factory=list)
    data_strength: str = "moderate"
    gaps: list[str] = field(default_factory=list)
    source: str = "llm"  # "llm" or "user"
    active: bool = True


def discover_tracks_llm(resume_data: dict) -> list[DiscoveredTrack]:
    """Use LLM to discover career tracks from profile."""
    try:
        from applypilot.llm import get_client

        client = get_client(tier="mid")
        raw = client.chat(
            [
                {
                    "role": "user",
                    "content": _DISCOVER_PROMPT.format(
                        resume_json=json.dumps(resume_data, indent=2, ensure_ascii=False)[:8000]
                    ),
                }
            ],
            max_output_tokens=2048,
        )
        start = raw.index("{")
        end = raw.rindex("}") + 1
        result = json.loads(raw[start:end])

        tracks = []
        for t in result.get("tracks", []):
            tracks.append(
                DiscoveredTrack(
                    track_id=uuid.uuid4().hex[:8],
                    name=t.get("name", "Unknown"),
                    skills=[s.lower() for s in t.get("skills", [])],
                    data_strength=t.get("data_strength", "moderate"),
                    gaps=t.get("gaps", []),
                    source="llm",
                )
            )
        return tracks
    except Exception as e:
        log.warning("LLM track discovery failed: %s — falling back to heuristic", e)
        return _discover_tracks_heuristic(resume_data)


def _discover_tracks_heuristic(resume_data: dict) -> list[DiscoveredTrack]:
    """Fallback: infer tracks from skills section names + work titles."""
    from applypilot.scoring.deterministic.skill_overlap import extract_known_skills

    tracks_map: dict[str, set[str]] = {}

    # From skills section names
    for group in resume_data.get("skills", []):
        name = (group.get("name") or "").lower()
        keywords = [k.lower() for k in group.get("keywords", [])]
        if "mobile" in name or "android" in name or "ios" in name:
            tracks_map.setdefault("Mobile Engineering", set()).update(keywords)
        elif "backend" in name or "api" in name:
            tracks_map.setdefault("Backend Engineering", set()).update(keywords)
        elif "devops" in name or "cloud" in name:
            tracks_map.setdefault("DevOps & Cloud", set()).update(keywords)
        elif "ml" in name or "ai" in name:
            tracks_map.setdefault("ML & AI", set()).update(keywords)

    # From work highlights
    for job in resume_data.get("work", []):
        for h in job.get("highlights", []):
            skills = extract_known_skills(h)
            if skills:
                tracks_map.setdefault("Software Engineering", set()).update(skills)

    return [
        DiscoveredTrack(
            track_id=uuid.uuid4().hex[:8],
            name=name,
            skills=sorted(skills),
            data_strength="moderate" if len(skills) >= 3 else "weak",
            source="heuristic",
        )
        for name, skills in tracks_map.items()
        if skills
    ]


def merge_user_tracks(
        discovered: list[DiscoveredTrack],
        user_additions: list[str],
) -> list[DiscoveredTrack]:
    """Merge LLM-discovered tracks with user-specified ones.

    User additions that match an existing track name are ignored (already covered).
    New ones are added with data_strength="unknown" (user believes in them).
    """
    existing_names = {t.name.lower() for t in discovered}
    for name in user_additions:
        clean = name.strip()
        if not clean or clean.lower() in existing_names:
            continue
        discovered.append(
            DiscoveredTrack(
                track_id=uuid.uuid4().hex[:8],
                name=clean,
                skills=[],
                data_strength="unknown",
                source="user",
                gaps=["No profile data yet — strengthen with `applypilot strengthen`"],
            )
        )
    return discovered
