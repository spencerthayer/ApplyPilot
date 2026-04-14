"""Single-job tailoring logic."""

from __future__ import annotations

import logging

from applypilot.llm import get_client
from applypilot.scoring.artifact_naming import build_artifact_prefix
from applypilot.scoring.validator import validate_json_fields

# ── Submodule imports ────────────────────────────────────────────────────
from applypilot.scoring.tailor.keyword_extractor import extract_jd_keywords
from applypilot.scoring.tailor.prompt_builder import (
    build_tailor_prompt,
    build_judge_prompt,
)
from applypilot.scoring.tailor.response_assembler import (
    extract_json,
    normalize_bullet,
    strip_disallowed_watchlist_skills,
    assemble_resume_text,
)

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5  # max cross-run retries before giving up

# ── Backward-compat aliases (old private names) ─────────────────────────
_extract_jd_keywords = extract_jd_keywords
_build_tailor_prompt = build_tailor_prompt
_build_judge_prompt = build_judge_prompt
_normalize_bullet = normalize_bullet
_strip_disallowed_watchlist_skills = strip_disallowed_watchlist_skills


def _build_tailored_prefix(job: dict) -> str:
    """Build a deterministic, collision-resistant filename prefix for a job."""
    return build_artifact_prefix(job)


def judge_tailored_resume(original_text: str, tailored_text: str, job_title: str, profile: dict) -> dict:
    """LLM judge layer: catches subtle fabrication that programmatic checks miss."""
    judge_prompt = build_judge_prompt(profile)

    messages = [
        {"role": "system", "content": judge_prompt},
        {
            "role": "user",
            "content": (
                f"JOB TITLE: {job_title}\n\n"
                f"ORIGINAL RESUME:\n{original_text}\n\n---\n\n"
                f"TAILORED RESUME:\n{tailored_text}\n\n"
                "Judge this tailored resume:"
            ),
        },
    ]

    client = get_client(tier="mid")
    response = client.chat(messages, max_output_tokens=512)

    passed = "VERDICT: PASS" in response.upper()
    issues = "none"
    if "ISSUES:" in response.upper():
        issues_idx = response.upper().index("ISSUES:")
        issues = response[issues_idx + 7:].strip()

    return {
        "passed": passed,
        "verdict": "PASS" if passed else "FAIL",
        "issues": issues,
        "raw": response,
    }


def tailor_resume(
        resume_text: str,
        job: dict,
        profile: dict,
        max_retries: int = 3,
        validation_mode: str = "normal",
) -> tuple[str, dict]:
    """Generate a tailored resume via JSON output + fresh context on each retry."""
    job_text = (
        f"TITLE: {job['title']}\n"
        f"COMPANY: {job['site']}\n"
        f"LOCATION: {job.get('location', 'N/A')}\n\n"
        f"DESCRIPTION:\n{(job.get('full_description') or '')[:6000]}"
    )

    report: dict = {
        "attempts": 0,
        "validator": None,
        "judge": None,
        "status": "pending",
        "validation_mode": validation_mode,
    }
    avoid_notes: list[str] = []
    tailored = ""
    client = get_client(tier="premium")
    tailor_prompt_base = build_tailor_prompt(profile)

    for attempt in range(max_retries + 1):
        report["attempts"] = attempt + 1

        prompt = tailor_prompt_base
        if avoid_notes:
            prompt += "\n\n## AVOID THESE ISSUES (from previous attempt):\n" + "\n".join(
                f"- {n}" for n in avoid_notes[-5:]
            )

        messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": f"ORIGINAL RESUME:\n{resume_text}\n\n---\n\nTARGET JOB:\n{job_text}\n\nReturn the JSON:",
            },
        ]

        log.debug("[tailor] %s — prompt length: %d chars", job.get("title", "?")[:40], len(prompt))
        raw = client.chat(messages, max_output_tokens=16000)
        log.debug("[tailor] %s — LLM response: %s", job.get("title", "?")[:40], raw[:400])

        try:
            data = extract_json(raw)
        except ValueError as exc:
            log.warning(
                "Attempt %d JSON parse failed (%s). Raw response (first 500 chars):\n%s", attempt + 1, exc, raw[:1000]
            )
            avoid_notes.append("Output was not valid JSON. Return ONLY a JSON object, nothing else.")
            continue

        removed_skills = strip_disallowed_watchlist_skills(data, profile)
        if removed_skills:
            log.info(
                "Attempt %d removed disallowed watchlist skills: %s",
                attempt + 1,
                ", ".join(removed_skills[:5]),
            )

        validation = validate_json_fields(data, profile, mode=validation_mode)
        report["validator"] = validation
        report["raw_json"] = data

        if not validation["passed"]:
            log.warning("Attempt %d validation failed: %s", attempt + 1, validation["errors"])
            avoid_notes.extend(validation["errors"])
            if attempt < max_retries:
                continue
            tailored = assemble_resume_text(data, profile)
            report["status"] = "failed_validation"
            return tailored, report

        tailored = assemble_resume_text(data, profile)

        if validation_mode == "lenient":
            report["judge"] = {"verdict": "SKIPPED", "passed": True, "issues": "none"}
            report["status"] = "approved"
            return tailored, report

        judge = judge_tailored_resume(resume_text, tailored, job.get("title", ""), profile)
        report["judge"] = judge

        if not judge["passed"]:
            avoid_notes.append(f"Judge rejected: {judge['issues']}")
            if attempt < max_retries:
                if validation_mode != "lenient":
                    continue
            report["status"] = "approved_with_judge_warning"
            return tailored, report

        report["status"] = "approved"
        return tailored, report

    report["status"] = "exhausted_retries"
    return tailored, report
