"""A-F Job Evaluation — enriched scoring blocks (inspired by career-ops).

Block A: Role Summary — already in JD parser
Block B: CV Match — already in deterministic baseline
Block C: Level Strategy — sell senior + if downleveled plan
Block D: Comp Research — market salary data
Block E: Personalization — top CV + LinkedIn changes
Block F: Interview Prep — STAR+R stories (see story_bank.py)
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def evaluate_level_strategy(profile_seniority: int, job_seniority: int, yoe: str) -> dict:
    """Block C: Level strategy — positioning advice based on seniority gap."""
    gap = job_seniority - profile_seniority

    if gap >= 2:
        strategy = "stretch"
        sell_plan = [
            f"Frame {yoe} YOE as depth, not just duration — highlight ownership and system design",
            "Lead with your biggest scope: team size, system scale, revenue impact",
            "Position past roles as 'senior-equivalent' by emphasizing autonomy",
        ]
        downlevel_plan = [
            "Accept if comp matches your target range",
            "Negotiate 6-month review with clear promotion criteria",
            "Get written agreement on scope matching the higher level",
        ]
    elif gap == 1:
        strategy = "natural_next"
        sell_plan = [
            "You're at the natural next step — emphasize readiness signals",
            "Show examples of already operating at this level (mentoring, design docs, cross-team)",
            f"Frame {yoe} YOE with increasing scope trajectory",
        ]
        downlevel_plan = [
            "Push back — you have the experience for this level",
            "Ask what specific gap they see and address it directly",
        ]
    elif gap <= -1:
        strategy = "overqualified"
        sell_plan = [
            "Emphasize you want depth/IC work, not just management",
            "Show enthusiasm for the specific technical challenges",
            "Frame it as intentional focus, not a step back",
        ]
        downlevel_plan = []
    else:
        strategy = "level_match"
        sell_plan = ["Direct match — focus on domain fit and culture add"]
        downlevel_plan = []

    return {
        "strategy": strategy,
        "gap": gap,
        "sell_plan": sell_plan,
        "downlevel_plan": downlevel_plan,
    }


def evaluate_personalization(matched_skills: list, missing_requirements: list, job_title: str) -> dict:
    """Block E: Top changes to CV and LinkedIn for this specific job."""
    cv_changes = []
    linkedin_changes = []

    # CV changes based on gaps
    for i, req in enumerate(missing_requirements[:5]):
        cv_changes.append(f"Add adjacent experience for '{req}' — find a bullet that demonstrates transferable skill")

    if matched_skills:
        cv_changes.insert(0, f"Move {', '.join(matched_skills[:3])} to top of skills section")

    # LinkedIn changes
    linkedin_changes.append(f"Update headline to align with '{job_title}'")
    if matched_skills:
        linkedin_changes.append(f"Add {', '.join(matched_skills[:3])} to LinkedIn skills")
    linkedin_changes.append("Update About section with metrics from your strongest matching bullets")

    return {
        "cv_changes": cv_changes[:5],
        "linkedin_changes": linkedin_changes[:5],
    }


def generate_evaluation_report(score_result: dict, profile: dict) -> dict:
    """Generate full A-F evaluation report from scoring result + profile."""
    yoe = profile.get("meta", {}).get("applypilot", {}).get("years_of_experience_total", "3+")
    profile_seniority = int(score_result.get("seniority_gap", 0)) + 2  # approximate

    # Block C
    level = evaluate_level_strategy(
        profile_seniority=2,
        job_seniority=profile_seniority,
        yoe=str(yoe),
    )

    # Block E
    personalization = evaluate_personalization(
        matched_skills=score_result.get("matched_skills", []),
        missing_requirements=score_result.get("missing_requirements", []),
        job_title=score_result.get("title", ""),
    )

    report = {
        "score": score_result.get("score", 0),
        "level_strategy": level,
        "personalization": personalization,
        "matched_skills": score_result.get("matched_skills", []),
        "missing_requirements": score_result.get("missing_requirements", []),
    }

    # Block F — Interview Stories (STAR+R)
    try:
        from applypilot.scoring.story_bank import generate_stories

        bullets = [
            {"text": h, "company": w.get("name", "")} for w in profile.get("work", []) for h in w.get("highlights", [])
        ]
        stories = generate_stories(bullets, score_result.get("missing_requirements", []))
        report["interview_stories"] = [
            {"req": s.requirement, "action": s.action, "result": s.result, "reflection": s.reflection}
            for s in stories[:6]
        ]
    except Exception:
        report["interview_stories"] = []

    # Block D — Negotiation Scripts
    try:
        from applypilot.scoring.negotiation import generate_scripts

        comp = profile.get("meta", {}).get("applypilot", {}).get("compensation", {})
        salary = comp.get("salary_expectation", "")
        if salary:
            report["negotiation"] = generate_scripts(
                salary,
                score_result.get("title", ""),
                score_result.get("company", ""),
            )
    except Exception:
        pass

    return report
