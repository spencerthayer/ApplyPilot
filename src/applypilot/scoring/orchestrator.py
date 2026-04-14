"""Scoring orchestrator — batch scoring entry point.

Imports deterministic and LLM scoring from decomposed submodules.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from rich.console import Console

from applypilot.config import load_resume_text
from applypilot.llm import get_client
from applypilot.scoring.deterministic.title_matcher import (
    title_key,
)
from applypilot.scoring.deterministic.exclusion_gate import (
    evaluate_exclusion,
)
from applypilot.scoring.deterministic.job_context_extractor import (
    extract_requirement_focused_text,
)
from applypilot.scoring.deterministic.baseline_scorer import (
    compute_deterministic_baseline,
    load_scoring_profile,
)
from applypilot.scoring.llm.prompt_builder import (
    SCORE_PROMPT,
    SCORING_RESPONSE_FORMAT,
    format_scoring_profile_for_prompt,
)
from applypilot.scoring.llm.calibrator import (
    ScoreResponseParseError,
    apply_score_calibration,
    parse_score_response,
)

log = logging.getLogger(__name__)

_SCORE_TRACE_ENABLED = os.environ.get("APPLYPILOT_SCORE_TRACE", "").strip().lower() in {"1", "true", "yes", "on"}
_TRACE_CONSOLE = Console(stderr=True, highlight=False, soft_wrap=True)
_LEGACY_SCORE_ERROR_PATTERN = "%LLM error:%"
_MODEL_RESPONSE_SNIPPET_LIMIT = 500
MAX_SCORE_ATTEMPTS_PER_JOB = 3
SCORE_ATTEMPT_BACKOFF_SECONDS = 60

_title_key = title_key


# ── Helpers ─────────────────────────────────────────────────────────


def _scoring_config() -> dict:
    try:
        from applypilot.scoring.tailoring_config import load_tailoring_config

        return load_tailoring_config()
    except Exception:
        return {}


def _compose_score_reasoning(baseline: dict, llm_result: dict | None, final_score: int) -> str:
    parts = [f"Score: {final_score}/10"]
    if baseline.get("reasoning"):
        parts.append(f"Baseline: {baseline['reasoning']}")
    if llm_result and llm_result.get("reasoning"):
        parts.append(f"LLM: {llm_result['reasoning']}")
    return " | ".join(parts)


def _normalize_llm_error(error: str) -> str:
    return re.sub(r"\s+", " ", str(error)).strip()[:200]


def _next_score_retry_at_iso(retry_count: int) -> str:
    delay = SCORE_ATTEMPT_BACKOFF_SECONDS * (2 ** min(retry_count, 5))
    return (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()


def _classify_score_outcome(result: dict) -> str:
    if result.get("exclusion_reason_code"):
        return "excluded"
    if result.get("error") or result.get("parse_error_category"):
        return "llm_failed"
    return "scored"


def _autoheal_legacy_llm_failures(job_repo) -> int:
    return job_repo.autoheal_legacy_llm_failures(_LEGACY_SCORE_ERROR_PATTERN)


def _load_scoring_resume_text() -> str:
    return load_resume_text()


def _safe_response_snippet(text: str) -> str:
    return text[:_MODEL_RESPONSE_SNIPPET_LIMIT].replace("\n", " ")


def _score_color(score: int) -> str:
    if score >= 8:
        return "green"
    if score >= 6:
        return "yellow"
    return "red"


def _outcome_markers(outcome: str) -> tuple[str, str]:
    if outcome == "excluded":
        return (" [EXCLUDED]", " [bold red][EXCLUDED][/bold red]")
    if outcome == "llm_failed":
        return (" [LLM ERROR]", " [bold yellow][LLM ERROR][/bold yellow]")
    return ("", "")


def _log_file_only(level: int, message: str, *args) -> None:
    record = log.makeRecord(log.name, level, "(trace)", 0, message, args, None, func="_log_file_only")
    for handler in log.handlers:
        if hasattr(handler, "baseFilename"):
            handler.handle(record)


def _emit_job_block_header(completed: int, total: int, score: int, title: str, outcome: str) -> None:
    marker_plain, marker_rich = _outcome_markers(outcome)
    _TRACE_CONSOLE.print(f"[bold cyan][{completed}/{total}][/bold cyan] {title}{marker_rich}")
    _TRACE_CONSOLE.print(f"          └─ score = [bold {_score_color(score)}]{score}[/bold {_score_color(score)}]")
    _log_file_only(logging.INFO, "[%d/%d] %s%s", completed, total, title, marker_plain)
    _log_file_only(logging.INFO, "          └─ score = %d", score)


def _emit_score_trace(result: dict) -> None:
    prefix = "          "
    if result.get("exclusion_reason_code"):
        reason = result.get("exclusion_reason_code", "")
        _TRACE_CONSOLE.print(f"{prefix}[yellow]excluded[/yellow] [dim]{reason}[/dim]")
        return
    baseline = result.get("baseline_score", "?")
    delta = result.get("score_delta", "?")
    _TRACE_CONSOLE.print(f"{prefix}baseline={baseline}  delta={delta}")


def _log_score_trace(result: dict) -> None:
    _log_file_only(logging.DEBUG, "  trace: %s", {k: v for k, v in result.items() if k != "full_response"})


def _score_telemetry_summary(results: list[dict], elapsed: float) -> str:
    scored = sum(1 for r in results if r.get("outcome") == "scored")
    excluded = sum(1 for r in results if r.get("outcome") == "excluded")
    errors = sum(1 for r in results if r.get("outcome") == "llm_failed")
    return f"{scored} scored, {excluded} excluded, {errors} errors in {elapsed:.1f}s"


# ── Single job scoring ──────────────────────────────────────────────


def score_job(resume_text: str, job: dict, scoring_profile: dict) -> dict:
    """Score a single job against the resume."""
    exclusion = evaluate_exclusion(job)
    if exclusion is not None:
        return exclusion

    baseline = compute_deterministic_baseline(scoring_profile, job)
    baseline_score = baseline.get("score", 5)

    jd_text = extract_requirement_focused_text(job.get("full_description") or job.get("description") or "")
    if not jd_text or len(jd_text.strip()) < 50:
        return {
            "score": baseline_score,
            "baseline_score": baseline_score,
            "reasoning": baseline.get("reasoning", "No JD for LLM calibration"),
            "outcome": "scored",
            "score_delta": 0,
        }

    # LLM calibration
    try:
        client = get_client(tier="cheap")
        prompt = format_scoring_profile_for_prompt(scoring_profile)
        messages = [
            {"role": "system", "content": SCORE_PROMPT + "\n\n" + prompt},
            {
                "role": "user",
                "content": f"RESUME:\n{resume_text[:4000]}\n\nJOB:\n{jd_text[:4000]}\n\n{SCORING_RESPONSE_FORMAT}",
            },
        ]
        response = client.chat(messages, max_output_tokens=2000)
        parsed = parse_score_response(response)
        calibrated_score, delta = apply_score_calibration(
            baseline,
            parsed.get("score", baseline_score),
            parsed.get("confidence", 0.7),
            parsed.get("matched_skills", []),
            parsed.get("missing_requirements", []),
            job.get("full_description") or job.get("description") or "",
        )
        final_score = calibrated_score
        reasoning = _compose_score_reasoning(baseline, parsed, final_score)

        return {
            "score": final_score,
            "baseline_score": baseline_score,
            "score_delta": final_score - baseline_score,
            "reasoning": reasoning,
            "outcome": "scored",
            "normalized_title": parsed.get("normalized_title", ""),
            "keywords": parsed.get("keywords", ""),
            "matched_skills": parsed.get("matched_skills", []),
            "missing_requirements": parsed.get("missing_requirements", []),
            "confidence": parsed.get("confidence"),
            "why_short": parsed.get("why_short", ""),
        }
    except ScoreResponseParseError as exc:
        return {
            "score": baseline_score,
            "baseline_score": baseline_score,
            "score_delta": 0,
            "reasoning": f"LLM parse error: {exc}",
            "outcome": "scored",
            "parse_error_category": "parse_error",
        }
    except Exception as exc:
        return {
            "score": 0,
            "baseline_score": baseline_score,
            "error": _normalize_llm_error(str(exc)),
            "reasoning": f"LLM error: {exc}",
            "outcome": "llm_failed",
        }


# ── Batch scoring ───────────────────────────────────────────────────


def run_scoring(
        min_score: int = 7,
        limit: int = 0,
        job_url: str | None = None,
        rescore: bool = False,
) -> dict:
    """Run scoring on pending jobs."""
    from applypilot.bootstrap import get_app
    from applypilot.db.dto import ScoreResultDTO, ExclusionResultDTO, ScoreFailureDTO

    job_repo = get_app().container.job_repo
    app = get_app()

    auto_healed = _autoheal_legacy_llm_failures(job_repo)
    if auto_healed:
        log.info("Auto-healed %d legacy scoring failure row(s).", auto_healed)

    if rescore:
        jobs_raw = job_repo.get_jobs_by_stage_dict(stage="enriched", limit=limit, job_url=job_url)
    else:
        jobs_raw = job_repo.get_jobs_by_stage_dict(stage="pending_score", limit=limit, job_url=job_url)

    if not jobs_raw:
        log.info("No unscored jobs with descriptions found.")
        return {"scored": 0, "errors": 0, "elapsed": 0.0, "distribution": [], "excluded": 0, "auto_healed": auto_healed}

    jobs = [dataclasses.asdict(j) if not isinstance(j, dict) else j for j in jobs_raw]

    try:
        resume_text = _load_scoring_resume_text()
    except FileNotFoundError:
        log.error("Resume not found. Run 'applypilot init' first.")
        return {"scored": 0, "errors": 0, "elapsed": 0.0, "distribution": [], "excluded": 0, "auto_healed": auto_healed}

    scoring_profile = load_scoring_profile()

    log.info("Scoring %d jobs sequentially...", len(jobs))
    t0 = time.time()
    completed = 0
    errors = 0
    excluded_count = 0
    results: list[dict] = []
    baseline_distribution: Counter = Counter()
    title_scores: dict[str, list[int]] = defaultdict(list)

    for job in jobs:
        from applypilot.logging_config import correlation_id

        correlation_id.set(job["url"][:80])
        result = evaluate_exclusion(job)
        if result is not None:
            excluded_count += 1
        else:
            result = score_job(resume_text, job, scoring_profile)

        result["outcome"] = _classify_score_outcome(result)
        result["url"] = job["url"]
        result["score_retry_count"] = int(job.get("score_retry_count") or 0)
        completed += 1

        if result["outcome"] == "llm_failed":
            errors += 1
        else:
            tk = str(result.get("normalized_title") or _title_key(job.get("title", "")))
            title_scores[tk].append(int(result.get("score", 0)))

        baseline_score = result.get("baseline_score")
        if isinstance(baseline_score, int):
            baseline_distribution[baseline_score] += 1

        results.append(result)

        score_value = int(result.get("score", 0))
        title_text = job.get("title", "?")[:60]
        if _SCORE_TRACE_ENABLED or result["outcome"] == "llm_failed":
            _emit_job_block_header(completed, len(jobs), score_value, title_text, str(result.get("outcome", "")))
            _emit_score_trace(result)
            _log_score_trace(result)
            _TRACE_CONSOLE.print("[bright_black]" + ("─" * 110) + "[/bright_black]")
            _log_file_only(logging.INFO, "          %s", "-" * 110)
        else:
            marker, _ = _outcome_markers(str(result.get("outcome", "")))
            log.info("[%d/%d] score=%d  %s%s", completed, len(jobs), score_value, title_text, marker)

    # Persist results
    now = datetime.now(timezone.utc).isoformat()
    for r in results:
        url = r["url"]
        outcome = r.get("outcome", "")
        if outcome == "excluded":
            job_repo.update_exclusion(
                ExclusionResultDTO(
                    url=url,
                    exclusion_reason_code=r.get("exclusion_reason_code", "unknown"),
                    exclusion_rule_id=r.get("exclusion_rule_id", ""),
                    score_reasoning=r.get("reasoning", ""),
                    scored_at=now,
                )
            )
        elif outcome == "llm_failed":
            retry_count = r.get("score_retry_count", 0) + 1
            job_repo.update_score_failure(
                ScoreFailureDTO(
                    url=url,
                    score_error=r.get("error", "unknown"),
                    score_reasoning=r.get("reasoning", ""),
                    score_retry_count=retry_count,
                    score_next_retry_at=_next_score_retry_at_iso(retry_count),
                )
            )
        else:
            fit_score = int(r.get("score", 0))
            # Generate evaluation report and embed level_strategy in reasoning
            reasoning_str = r.get("reasoning", "")
            try:
                from applypilot.scoring.evaluation_report import generate_evaluation_report
                from applypilot.config import load_resume_json

                profile = load_resume_json()
                eval_input = {
                    "score": fit_score,
                    "matched_skills": r.get("matched_skills") or [],
                    "missing_requirements": r.get("missing_requirements") or [],
                    "seniority_gap": 0,
                    "title": r.get("normalized_title") or "",
                }
                eval_report = generate_evaluation_report(eval_input, profile)
                level = eval_report.get("level_strategy", {})
                r["level_strategy"] = level.get("strategy", "")
                # Merge level strategy into reasoning — preserve LLM data
                import json

                reasoning_data = {
                    "reasoning": reasoning_str,
                    "level_strategy": level.get("strategy", ""),
                    "seniority_gap": level.get("gap", 0),
                }
                if r.get("matched_skills"):
                    reasoning_data["matched_skills"] = r["matched_skills"]
                if r.get("missing_requirements"):
                    reasoning_data["missing_requirements"] = r["missing_requirements"]
                if r.get("why_short"):
                    reasoning_data["why_short"] = r["why_short"]
                if r.get("confidence") is not None:
                    reasoning_data["confidence"] = r["confidence"]
                reasoning_str = json.dumps(reasoning_data)
            except Exception:
                pass

            job_repo.update_score(
                ScoreResultDTO(
                    url=url,
                    fit_score=fit_score,
                    score_reasoning=reasoning_str,
                    scored_at=now,
                )
            )
            from applypilot.analytics.helpers import emit_job_scored

            emit_job_scored(
                url,
                r.get("site", ""),
                fit_score,
                r.get("matched_skills"),
                r.get("missing_requirements"),
                r.get("level_strategy"),
            )

            # Assign best-matching track (Phase 4)
            _assign_best_track(job_repo, url, r, app)

    elapsed = time.time() - t0
    scored = completed - excluded_count - errors
    distribution = sorted(baseline_distribution.items(), reverse=True)

    log.info(_score_telemetry_summary(results, elapsed))

    return {
        "scored": scored,
        "errors": errors,
        "elapsed": round(elapsed, 1),
        "distribution": distribution,
        "excluded": excluded_count,
        "auto_healed": auto_healed,
    }


def _assign_best_track(job_repo, url: str, score_result: dict, app) -> None:
    """Match a scored job to the best career track.

    Strategy: score each track by how many JD requirements (both matched AND missing)
    overlap with the track's skill set. The track that covers the most JD requirements
    is the best fit — even if the candidate is missing some of those skills.
    """
    try:
        tracks = app.container.track_repo.get_all_tracks()
        if not tracks:
            return

        # ALL JD requirements — both what we have and what we're missing
        jd_skills = {s.lower() for s in score_result.get("matched_skills", [])}
        jd_skills |= {s.lower() for s in score_result.get("missing_requirements", [])}
        if not jd_skills:
            return

        best_track = None
        best_score = 0.0
        for track in tracks:
            if not track["active"]:
                continue
            track_skills = {s.lower() for s in track["skills"]}

            # Score: how many JD requirements does this track's skill set cover?
            score = 0.0
            for jd_skill in jd_skills:
                # Exact match
                if jd_skill in track_skills:
                    score += 1.0
                    continue
                # Substring match (e.g. "spring boot" in "spring_boot", "rest api" in "restful_apis")
                jd_norm = jd_skill.replace(" ", "_").replace("-", "_")
                if any(jd_norm in ts or ts in jd_norm for ts in track_skills):
                    score += 0.8
                    continue
                # Word overlap (e.g. JD "distributed systems" vs track "system design")
                jd_words = set(jd_skill.split())
                for ts in track_skills:
                    if len(jd_words & set(ts.split())) >= 1 and len(jd_words) > 1:
                        score += 0.3
                        break

            if score > best_score:
                best_score = score
                best_track = track

        if best_track:
            job_repo.update_job_fields_generic(url, {"best_track_id": best_track["track_id"]})
            from applypilot.analytics.helpers import emit_track_selected

            emit_track_selected(url, best_track["track_id"], score_result.get("score"))
    except Exception as e:
        log.debug("Track assignment failed for %s: %s", url[:50], e)
