"""One-page budget calculator — derives dynamic bullet counts per experience.

Purpose: When the user doesn't specify bullet counts, auto-calculate how many
fit on one page. More recent and more relevant experiences get more bullets.
This replaces hardcoded min/max_bullets_per_role with a space-aware distribution.

SRP: Only calculates numbers. Does not render, does not call LLMs, does not persist.
"""

from __future__ import annotations

# US Letter, 11pt, 0.5in margins, single-spaced ≈ 55 usable lines
PAGE_LINES = 55

# Fixed line costs — each section type consumes a known number of lines
_COSTS = {
    "header": 3,  # name + title + contact + blank
    "summary": 4,  # heading + 2-3 sentences + blank
    "skills_heading": 1,
    "skill_line": 1,  # one skill group = one line
    "exp_heading": 1,
    "exp_entry": 2,  # company header + subtitle (dates/tech)
    "proj_heading": 1,
    "proj_entry": 2,  # project header + subtitle
    "education": 3,  # heading + content + blank
    "gap": 1,  # blank line between sections
}

# Recency weights — most recent role gets the most bullets
# Index 0 = most recent, decays for older roles
_RECENCY_WEIGHTS = [0.50, 0.30, 0.15, 0.05]


def calculate(
    experience_count: int,
    skill_group_count: int,
    project_count: int = 0,
    has_education: bool = True,
    has_summary: bool = True,
    page_lines: int = PAGE_LINES,
) -> dict:
    """Calculate dynamic bullet budget for a one-page resume.

    Returns per-experience bullet allocation weighted by recency.
    Most recent role gets ~50% of available bullet lines.

    Args:
        experience_count: Number of work experience entries.
        skill_group_count: Number of skill category lines (condensed).
        project_count: Number of project entries.
        has_education: Whether education section exists.
        has_summary: Whether summary section exists.
        page_lines: Total lines available (override for testing).

    Returns:
        {
            "bullets_per_experience": [int, ...],  # per role, most recent first
            "total_bullet_lines": int,
            "fixed_lines": int,
            "overflow": bool,  # True if fixed content alone exceeds page
        }
    """
    fixed = _COSTS["header"]

    if has_summary:
        fixed += _COSTS["summary"] + _COSTS["gap"]

    if skill_group_count > 0:
        fixed += _COSTS["skills_heading"] + (skill_group_count * _COSTS["skill_line"]) + _COSTS["gap"]

    if experience_count > 0:
        fixed += _COSTS["exp_heading"] + (experience_count * _COSTS["exp_entry"]) + _COSTS["gap"]

    if project_count > 0:
        fixed += _COSTS["proj_heading"] + (project_count * _COSTS["proj_entry"]) + _COSTS["gap"]

    if has_education:
        fixed += _COSTS["education"]

    remaining = max(page_lines - fixed, 0)
    overflow = fixed >= page_lines

    # Distribute remaining lines across experiences by recency weight
    bullets_per_exp = _distribute(remaining, experience_count)

    return {
        "bullets_per_experience": bullets_per_exp,
        "total_bullet_lines": remaining,
        "fixed_lines": fixed,
        "overflow": overflow,
    }


def _distribute(total_lines: int, count: int) -> list[int]:
    """Distribute bullet lines across experiences by recency.

    Most recent role (index 0) gets the largest share.
    Each role gets at least 1 bullet if there's any space.
    """
    if count == 0 or total_lines == 0:
        return []

    # Pad weights if more experiences than predefined weights
    weights = _RECENCY_WEIGHTS[:count]
    if len(weights) < count:
        # Remaining roles split the leftover weight equally
        used = sum(weights)
        remaining_weight = max(1.0 - used, 0.05)
        extra = count - len(weights)
        weights.extend([remaining_weight / extra] * extra)

    # Normalize weights to sum to 1.0
    total_weight = sum(weights)
    weights = [w / total_weight for w in weights]

    # Allocate lines, minimum 1 per role
    allocation = [max(int(total_lines * w), 1) for w in weights]

    # Fix rounding — give leftover to most recent role
    allocated = sum(allocation)
    if allocated < total_lines:
        allocation[0] += total_lines - allocated

    return allocation
