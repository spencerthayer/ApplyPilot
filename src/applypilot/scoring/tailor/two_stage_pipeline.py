"""Two-stage tailoring pipeline: Planner → Generator.

Planner (mid model): analyzes JD, maps to resume, produces strategy
Generator (premium model): writes the resume from the plan
"""

from __future__ import annotations

import json
import logging

from applypilot.llm import get_client
from applypilot.scoring.tailor.two_stage_prompts import PLANNER_PROMPT, GENERATOR_PROMPT
from applypilot.scoring.validator import BANNED_WORDS

log = logging.getLogger(__name__)


def _build_resume_for_planner(resume_text: str, job: dict) -> str:
    """Try to build resume from track-scoped pieces, fall back to full pieces, then raw text.

    Minimum 2000 chars — if track-scoped resume is too short (missing experience
    bullets), use the full resume so the planner has enough context.
    """
    _MIN_PLANNER_CHARS = 2000
    track_id = job.get("best_track_id")
    try:
        from applypilot.bootstrap import get_app
        from applypilot.resume_builder import from_pieces

        c = get_app().container

        # Try track-scoped first
        if track_id:
            text = from_pieces(c.piece_repo, track_id=track_id).render_text()
            if text and len(text) >= _MIN_PLANNER_CHARS:
                log.debug("Planner using track %s pieces (%d chars)", track_id, len(text))
                return text
            log.debug("Track %s pieces too short (%d chars), falling back to full", track_id, len(text) if text else 0)

        # Fall back to full resume from pieces
        text = from_pieces(c.piece_repo).render_text()
        if text and len(text) >= _MIN_PLANNER_CHARS:
            log.debug("Planner using full pieces (%d chars)", len(text))
            return text
    except Exception as e:
        log.debug("Pieces fallback: %s", e)
    return resume_text


def run_two_stage_tailor(
        resume_text: str,
        job: dict,
        profile: dict,
) -> tuple[str | None, dict]:
    """Run planner → generator pipeline. Returns (tailored_json_str, report)."""

    # Extract profile info
    basics = profile.get("basics", {})
    work = profile.get("work", [])
    current = work[0] if work else {}
    yoe = profile.get("meta", {}).get("applypilot", {}).get("years_of_experience_total", "3+")
    location = basics.get("location", {})
    loc_str = ", ".join(p for p in [location.get("city", ""), location.get("country", "")] if p) or "Not specified"
    profiles_str = " | ".join(p.get("url", "") for p in basics.get("profiles", []) if p.get("url"))

    jd_text = job.get("full_description") or job.get("description") or ""
    jd_title = job.get("title", "")
    jd_company = job.get("company", "Unknown")

    # Use track-scoped pieces if available
    planner_resume = _build_resume_for_planner(resume_text, job)

    # Laser matcher: pre-analyze JD requirements with adjacency graph
    adjacency_hints = ""
    try:
        from applypilot.scoring.tailor.jd_matcher import match_all_requirements, summarize_matches

        resume_skills = {w.lower() for w in planner_resume.split() if len(w) > 2}
        profile_skills = set()
        for s in profile.get("skills", []):
            if isinstance(s, dict):
                profile_skills.update(k.lower() for k in s.get("keywords", []))
        jd_reqs = [line.strip() for line in jd_text.split("\n") if len(line.strip()) > 20][:15]
        if jd_reqs:
            matches = match_all_requirements(jd_reqs, resume_skills, profile_skills)
            summary = summarize_matches(matches)
            if summary["coverage"] > 0:
                hints = []
                for m in matches:
                    if m.match_type.value == "adjacent":
                        hints.append(
                            f'- JD: "{m.requirement[:60]}" → adjacent to your skill: {m.matched_skill} ({m.action})'
                        )
                    elif m.match_type.value == "gap":
                        hints.append(f'- JD: "{m.requirement[:60]}" → GAP (address in cover letter)')
                if hints:
                    adjacency_hints = "\n\n## SKILL ADJACENCY HINTS\n" + "\n".join(hints[:10])
                    log.debug("Laser matcher: %.0f%% coverage, %d hints", summary["coverage"] * 100, len(hints))
    except Exception as e:
        log.debug("Laser matcher skipped: %s", e)

    # Stage 1: Planner (mid model — good at reasoning)
    log.info("Stage 1: Planning tailoring strategy...")
    planner_prompt = (
            PLANNER_PROMPT.format(
                yoe=yoe,
                current_role=current.get("position", ""),
                current_company=current.get("name", ""),
                resume_text=planner_resume,
                jd_title=jd_title,
                jd_company=jd_company,
                jd_text=jd_text[:6000],
            )
            + adjacency_hints
    )

    try:
        planner_client = get_client(tier="mid")
        plan_raw = planner_client.chat(
            [{"role": "user", "content": planner_prompt}],
            max_output_tokens=3000,
        )
        # Extract JSON from plan
        import re

        plan_raw = re.sub(r"<think>.*?</think>", "", plan_raw, flags=re.DOTALL).strip()
        plan_start = plan_raw.index("{")
        plan_end = plan_raw.rindex("}") + 1
        plan_json = plan_raw[plan_start:plan_end]
        plan = json.loads(plan_json)
        log.info(
            "Plan: %d requirements mapped, %d gaps",
            len(plan.get("requirements", [])),
            sum(1 for r in plan.get("requirements", []) if r.get("gap")),
        )
    except Exception as e:
        log.warning("Planner failed: %s — falling back to single-stage", e)
        return None, {"status": "planner_failed", "error": str(e)}

    # Stage 2: Generator (premium model — good at writing)
    log.info("Stage 2: Generating tailored resume from plan...")
    generator_prompt = GENERATOR_PROMPT.format(
        plan_json=plan_json,
        resume_text=resume_text,
        name=basics.get("name", ""),
        email=basics.get("email", ""),
        phone=basics.get("phone", ""),
        location=loc_str,
        profiles=profiles_str,
        banned_words=", ".join(BANNED_WORDS),
    )

    try:
        gen_client = get_client(tier="premium")
        gen_raw = gen_client.chat(
            [{"role": "user", "content": generator_prompt}],
            max_output_tokens=4000,
        )
        gen_raw = re.sub(r"<think>.*?</think>", "", gen_raw, flags=re.DOTALL).strip()
        gen_start = gen_raw.index("{")
        gen_end = gen_raw.rindex("}") + 1
        result_json = gen_raw[gen_start:gen_end]
        # Validate it parses
        json.loads(result_json)
        return result_json, {
            "status": "approved",
            "pipeline": "two_stage",
            "plan_requirements": len(plan.get("requirements", [])),
            "plan_gaps": sum(1 for r in plan.get("requirements", []) if r.get("gap")),
        }
    except Exception as e:
        log.warning("Generator failed: %s", e)
        return None, {"status": "generator_failed", "error": str(e)}
