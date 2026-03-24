"""Resume assembler — builds plain-text resume from selected segments.

SRP: Only assembles text from segments. Does not persist, does not call LLMs.
"""

from __future__ import annotations

from applypilot.db.segments_repo import Segment


def assemble(segments: list[Segment], profile: dict) -> str:
    """Assemble a plain-text resume from an ordered list of segments.

    Args:
        segments: Ordered segments (root first, then children by sort_order).
        profile: User profile for header injection (name, contact).

    Returns:
        Formatted plain-text resume.
    """
    personal = _get_personal(profile)
    lines: list[str] = []

    # Header — always from profile, never from segments
    lines.append(personal.get("full_name", ""))
    lines.append(personal.get("label", ""))
    contact = _build_contact(personal)
    if contact:
        lines.append(contact)
    lines.append("")

    # Group segments by type
    by_type: dict[str, list[Segment]] = {}
    for seg in segments:
        by_type.setdefault(seg.type, []).append(seg)

    # Summary
    for seg in by_type.get("summary", []):
        lines.append("SUMMARY")
        lines.append(seg.content)
        lines.append("")

    # Skills
    skill_groups = by_type.get("skill_group", [])
    if skill_groups:
        lines.append("TECHNICAL SKILLS")
        for seg in skill_groups:
            lines.append(seg.content)
        lines.append("")

    # Experience + bullets
    experiences = by_type.get("experience", [])
    if experiences:
        lines.append("EXPERIENCE")
        # Build parent→children map for bullets
        bullet_map: dict[str, list[Segment]] = {}
        for seg in by_type.get("bullet", []):
            bullet_map.setdefault(seg.parent_id or "", []).append(seg)

        for exp in experiences:
            lines.append(exp.content)
            meta = exp.metadata
            subtitle_parts = []
            if meta.get("start"):
                date_range = meta["start"]
                if meta.get("end"):
                    date_range += f" – {meta['end']}"
                else:
                    date_range += " – Present"
                subtitle_parts.append(date_range)
            if subtitle_parts:
                lines.append(" | ".join(subtitle_parts))
            for bullet in bullet_map.get(exp.id, []):
                lines.append(f"- {bullet.content}")
            lines.append("")

    # Projects
    projects = by_type.get("project", [])
    if projects:
        lines.append("PROJECTS")
        for seg in projects:
            lines.append(seg.content)
        lines.append("")

    # Education
    for seg in by_type.get("education", []):
        lines.append("EDUCATION")
        lines.append(seg.content)
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def _get_personal(profile: dict) -> dict:
    """Extract personal info from profile, checking both flat and nested formats."""
    personal = profile.get("personal", {})
    if not personal:
        # Try resume.json meta.applypilot.personal
        personal = profile.get("meta", {}).get("applypilot", {}).get("personal", {})
    # Fallback: basics from resume.json
    basics = profile.get("basics", {})
    return {
        "full_name": personal.get("full_name") or basics.get("name", ""),
        "label": basics.get("label", ""),
        "email": personal.get("email") or basics.get("email", ""),
        "phone": personal.get("phone") or basics.get("phone", ""),
        "github_url": personal.get("github_url", ""),
        "linkedin_url": personal.get("linkedin_url", ""),
    }


def _build_contact(personal: dict) -> str:
    parts = [personal.get(k) for k in ("email", "phone", "github_url", "linkedin_url")]
    return " | ".join(p for p in parts if p)
