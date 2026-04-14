"""Tailoring batch orchestrator."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from applypilot.config import TAILORED_DIR, load_profile, load_resume_text

# ── Submodule imports ────────────────────────────────────────────────────
from applypilot.scoring.tailor.skill_gap_detector import check_skill_gaps
from applypilot.scoring.tailor.prompt_builder import (
    build_tailor_prompt,
    build_judge_prompt,
)
from applypilot.scoring.tailor.response_assembler import (
    normalize_bullet,
    strip_disallowed_watchlist_skills,
)

log = logging.getLogger(__name__)


def _load_track_resumes() -> dict[str, str]:
    """Load per-track base resume texts. Returns {track_id: resume_text}."""
    from applypilot.config import APP_DIR

    track_dir = APP_DIR / "tracks"
    if not track_dir.exists():
        return {}
    resumes = {}
    for f in track_dir.glob("*.txt"):
        track_id = f.stem.split("_")[0]
        resumes[track_id] = f.read_text(encoding="utf-8")
    return resumes


def _resolve_resume_for_job(job: dict, generic_resume: str, track_resumes: dict[str, str]) -> str:
    """Pick the best resume for a job — track-specific if available, else generic."""
    track_id = job.get("best_track_id")
    if track_id and track_id in track_resumes:
        log.debug("Using track %s resume for %s", track_id, job.get("url", "")[:50])
        return track_resumes[track_id]
    return generic_resume


def _two_stage_with_fallback(resume_text: str, job: dict, profile: dict, validation_mode: str) -> tuple[str, dict]:
    """Try cache → two-stage pipeline → single-stage fallback. Store overlays on success."""
    from applypilot.scoring.tailor.two_stage_pipeline import run_two_stage_tailor
    from applypilot.scoring.tailor.response_assembler import assemble_resume_text
    from applypilot.scoring.tailor.hybrid_bridge import store_overlays, try_cache
    from applypilot.analytics.helpers import emit_job_tailored, emit_cache_hit

    job_url = job.get("url", "")
    track_id = job.get("best_track_id")

    # 1. Cache check — reassemble from pieces if overlays exist
    try:
        from applypilot.bootstrap import get_app

        container = get_app().container
        jd_text = job.get("full_description") or job.get("description") or ""
        cached = try_cache(job_url, track_id, container.piece_repo, container.overlay_repo, jd_text=jd_text)
        if cached:
            emit_cache_hit(job_url, track_id)
            return cached, {"status": "approved", "pipeline": "cache_hit", "attempts": 0}
    except Exception:
        pass  # No pieces yet or DB issue — continue to LLM

    # 2. Two-stage pipeline
    result_json, report = run_two_stage_tailor(resume_text, job, profile)

    if result_json:
        try:
            import json

            data = json.loads(result_json)
            tailored = assemble_resume_text(data, profile)
            report["attempts"] = 1

            # 3. Store overlays for future cache hits
            overlay_count = 0
            try:
                container = get_app().container
                overlay_count = store_overlays(data, job_url, track_id, container.piece_repo, container.overlay_repo)
            except Exception as e:
                log.debug("Overlay storage skipped: %s", e)

            emit_job_tailored(
                job_url,
                report.get("pipeline", "two_stage"),
                track_id,
                report.get("plan_requirements", 0),
                report.get("plan_gaps", 0),
                overlay_count,
            )
            return tailored, report
        except Exception as e:
            log.warning("Two-stage assembly failed: %s — falling back", e)

    # 4. Fallback to single-stage
    log.info("Falling back to single-stage tailoring")
    tailored, report = tailor_resume(resume_text, job, profile, validation_mode=validation_mode)
    report["pipeline"] = "single_stage_fallback"
    emit_job_tailored(job_url, "single_stage_fallback", track_id)
    return tailored, report


MAX_ATTEMPTS = 5  # max cross-run retries before giving up

# ── Backward-compat aliases (old private names) ─────────────────────────
_build_tailor_prompt = build_tailor_prompt
_build_judge_prompt = build_judge_prompt
_normalize_bullet = normalize_bullet
_strip_disallowed_watchlist_skills = strip_disallowed_watchlist_skills

from applypilot.scoring.tailor.tailor_job import tailor_resume, judge_tailored_resume, _build_tailored_prefix  # noqa: F401


def run_tailoring(
    min_score: int = 7,
    limit: int = 0,
    validation_mode: str = "normal",
    target_url: str | None = None,
    force: bool = False,
) -> dict:
    """Generate tailored resumes for high-scoring jobs."""
    profile = load_profile()
    try:
        resume_text = load_resume_text()
    except FileNotFoundError:
        log.error("Resume file not found. Run 'applypilot init' first.")
        return {"approved": 0, "failed": 0, "errors": 0, "elapsed": 0.0}

    # Load track base resumes if available
    track_resumes = _load_track_resumes()

    # Use repo for structured access
    from applypilot.bootstrap import get_app

    app = get_app()
    _job_repo = app.container.job_repo

    # Decompose resume into pieces (idempotent — skips if already done)
    try:
        from applypilot.scoring.tailor.hybrid_bridge import ensure_decomposed, map_track
        from applypilot.config import load_resume_json
        from applypilot.analytics.helpers import emit_pieces_decomposed

        resume_data = load_resume_json()
        piece_count = ensure_decomposed(resume_data, app.container.piece_repo)
        log.debug("Piece store: %d pieces", piece_count)
        emit_pieces_decomposed(piece_count, len(resume_data.get("work", [{}])[0].get("highlights", [])))

        # Map tracks to pieces
        from applypilot.db.connection import get_connection

        tracks = app.container.track_repo.get_all_tracks()
        for t in tracks:
            skills = t["skills"] if isinstance(t.get("skills"), list) else []
            if skills:
                map_track(t["track_id"], skills, app.container.piece_repo, get_connection())
    except Exception as e:
        log.debug("Piece decomposition skipped: %s", e)

    if target_url:
        target_job = _job_repo.find_by_url_fuzzy(target_url)
        if not target_job:
            log.info("Target URL not found in database: %s", target_url)
            return {"approved": 0, "failed": 0, "errors": 0, "elapsed": 0.0}

        if not target_job.full_description:
            log.error("Target job has no full description. Run 'applypilot run enrich' first.")
            return {"approved": 0, "failed": 0, "errors": 0, "elapsed": 0.0}
        score = target_job.fit_score
        if not force and score is not None and score < min_score:
            log.info(
                "Target job score %s is below min-score %d. Use --force to override.",
                score,
                min_score,
            )
            return {"approved": 0, "failed": 0, "errors": 0, "elapsed": 0.0}
        if not force and target_job.tailored_resume_path:
            log.info("Target job already has a tailored resume. Use --force to regenerate.")
            return {"approved": 0, "failed": 0, "errors": 0, "elapsed": 0.0}
        jobs_raw = [target_job]
    else:
        jobs_raw = _job_repo.get_jobs_by_stage_dict(stage="pending_tailor", min_score=min_score, limit=limit)

    if not jobs_raw:
        log.info("No untailored jobs with score >= %d.", min_score)
        return {"approved": 0, "failed": 0, "errors": 0, "elapsed": 0.0}

    # Convert DTOs to dicts for tailoring functions (legacy interface)
    import dataclasses

    jobs = [dataclasses.asdict(j) if not isinstance(j, dict) else j for j in jobs_raw]

    TAILORED_DIR.mkdir(parents=True, exist_ok=True)
    log.info("Tailoring resumes for %d jobs (score >= %d)...", len(jobs), min_score)
    t0 = time.time()
    completed = 0
    results: list[dict] = []
    stats: dict[str, int] = {"approved": 0, "failed_validation": 0, "failed_judge": 0, "error": 0}

    for job in jobs:
        completed += 1
        # Set correlation ID for tracing this job across stages
        from applypilot.logging_config import correlation_id

        correlation_id.set(job.get("url", "")[:80])
        try:
            from applypilot.scoring.tailor.tiered import (
                TailoringLevel,
                classify_tailoring_level,
            )

            tl = classify_tailoring_level(job.get("fit_score"))

            match tl:
                case TailoringLevel.TL0_SKIP:
                    log.info(
                        "%d/%d [TL0-SKIP] score=%s | %s",
                        completed,
                        len(jobs),
                        job.get("fit_score"),
                        job.get("title", "?")[:40],
                    )
                    _job_repo.set_pipeline_status(job["url"], "skipped")
                    results.append(
                        {
                            "url": job["url"],
                            "title": job.get("title", ""),
                            "site": job.get("site", ""),
                            "status": "skipped",
                            "attempts": 0,
                            "path": None,
                            "pdf_path": None,
                        }
                    )
                    stats["skipped"] = stats.get("skipped", 0) + 1
                    continue

                case TailoringLevel.TL2_FULL:
                    job_resume = _resolve_resume_for_job(job, resume_text, track_resumes)
                    tailored, report = _two_stage_with_fallback(job_resume, job, profile, validation_mode)
                    report["tailoring_level"] = "TL2"

                case TailoringLevel.TL3_PREMIUM:
                    job_resume = _resolve_resume_for_job(job, resume_text, track_resumes)
                    tailored, report = _two_stage_with_fallback(job_resume, job, profile, validation_mode)
                    report["tailoring_level"] = "TL3"
                    # TL3: flag for HITL review before submission
                    if report["status"] in ("approved", "approved_with_judge_warning"):
                        report["needs_hitl_review"] = True

            jd_text = job.get("full_description") or ""
            if jd_text and tailored:
                report["skill_gaps"] = check_skill_gaps(jd_text, tailored)
                coverage = report["skill_gaps"]["coverage"]
                log.debug(
                    "[tailor] %s — skill coverage: %.0f%% missing: %s",
                    job.get("title", "?")[:40],
                    coverage * 100,
                    report["skill_gaps"]["missing"][:10],
                )
                if coverage < 0.5:
                    log.warning("Low JD keyword coverage (%.0f%%) for %s", coverage * 100, job["title"][:50])
            bullet_count = tailored.count("\n- ")
            log.debug(
                "[tailor] %s — bullets: %d, status: %s, attempts: %d",
                job.get("title", "?")[:40],
                bullet_count,
                report["status"],
                report["attempts"],
            )

            prefix = _build_tailored_prefix(job)

            txt_path = TAILORED_DIR / f"{prefix}.txt"
            txt_path.write_text(tailored, encoding="utf-8")
            if not txt_path.exists() or txt_path.stat().st_size == 0:
                raise RuntimeError(f"Failed to persist tailored TXT: {txt_path}")

            if "raw_json" in report:
                data_path = TAILORED_DIR / f"{prefix}_DATA.json"
                data_path.write_text(json.dumps(report["raw_json"], indent=2, ensure_ascii=False), encoding="utf-8")

            job_path = TAILORED_DIR / f"{prefix}_JOB.txt"
            job_desc = (
                f"Title: {job['title']}\n"
                f"Company: {job['site']}\n"
                f"Location: {job.get('location', 'N/A')}\n"
                f"Score: {job.get('fit_score', 'N/A')}\n"
                f"URL: {job['url']}\n\n"
                f"{job.get('full_description', '')}"
            )
            job_path.write_text(job_desc, encoding="utf-8")

            report_path = TAILORED_DIR / f"{prefix}_REPORT.json"
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

            pdf_path = None
            status = report["status"]
            if status in ("approved", "approved_with_judge_warning"):
                try:
                    from applypilot.scoring.pdf import convert_to_pdf

                    generated_pdf = convert_to_pdf(txt_path)
                    pdf_path = str(generated_pdf)
                    if not generated_pdf.exists() or generated_pdf.stat().st_size == 0:
                        raise RuntimeError(f"Generated PDF missing or empty: {generated_pdf}")
                except Exception as exc:
                    log.error("PDF generation failed for %s: %s", txt_path, exc)
                    status = "error"

            result = {
                "url": job["url"],
                "path": str(txt_path),
                "pdf_path": pdf_path,
                "title": job["title"],
                "site": job["site"],
                "status": status,
                "attempts": report["attempts"],
            }

            # Copy to organized folder: ~/Documents/ApplyPilot_Applications/Company/Role/
            try:
                from applypilot.config.paths import organized_job_dir, ORGANIZED_DIR

                org_dir = organized_job_dir(
                    ORGANIZED_DIR,
                    job.get("site", ""),
                    job.get("title", ""),
                )
                import shutil

                for src in [txt_path, Path(pdf_path) if pdf_path else None]:
                    if src and src.exists():
                        shutil.copy2(src, org_dir / f"resume{src.suffix}")
                (org_dir / "job_info.txt").write_text(job_desc, encoding="utf-8")
            except Exception:
                pass  # non-critical
            if status in ("approved", "approved_with_judge_warning"):
                log.info("Saved tailored artifacts: txt=%s | pdf=%s", txt_path.resolve(), Path(pdf_path).resolve())
            else:
                log.info("Saved tailored TXT: %s", txt_path.resolve())
        except Exception as e:
            result = {
                "url": job["url"],
                "title": job["title"],
                "site": job["site"],
                "status": "error",
                "attempts": 0,
                "path": None,
                "pdf_path": None,
            }
            log.error("%d/%d [ERROR] %s -- %s", completed, len(jobs), job["title"][:40], e)

        results.append(result)
        stats[result.get("status", "error")] = stats.get(result.get("status", "error"), 0) + 1

        elapsed = time.time() - t0
        rate = completed / elapsed if elapsed > 0 else 0
        log.info(
            "%d/%d [%s] attempts=%s | %.1f jobs/min | %s",
            completed,
            len(jobs),
            result["status"].upper(),
            result.get("attempts", "?"),
            rate * 60,
            result["title"][:40],
        )

    now = datetime.now(timezone.utc).isoformat()
    _success_statuses = {"approved", "approved_with_judge_warning"}

    # Use repo for structured writes
    from applypilot.bootstrap import get_app
    from applypilot.db.dto import TailorResultDTO

    _job_repo = get_app().container.job_repo

    for r in results:
        if r["status"] in _success_statuses:
            _job_repo.update_tailoring(
                TailorResultDTO(
                    url=r["url"],
                    tailored_resume_path=r["path"],
                    tailored_at=now,
                )
            )
            _job_repo.increment_attempts(r["url"], "tailor_attempts")
            # TL3 premium: park for HITL review before auto-apply
            if r.get("needs_hitl_review"):
                _job_repo.park_for_human_review(
                    url=r["url"],
                    reason="TL3 premium tailoring — review before submission",
                    apply_url=r.get("url", ""),
                    instructions="Review the tailored resume, then approve for auto-apply.",
                )
        elif r.get("status") == "exhausted_retries":
            _job_repo.increment_attempts(r["url"], "tailor_attempts")
            _job_repo.park_for_human_review(
                url=r["url"],
                reason="Tailoring guardrail failed after max retries",
                apply_url=r.get("url", ""),
                instructions="Manually review and edit the tailored resume.",
            )
        elif r.get("status") == "skipped":
            pass  # TL0 — already handled above
        else:
            _job_repo.increment_attempts(r["url"], "tailor_attempts")

    # Release shared Playwright browser
    try:
        from applypilot.scoring.pdf.pdf_renderer import close_shared_browser
        close_shared_browser()
    except Exception:
        pass

    elapsed = time.time() - t0
    log.info(
        "Tailoring done in %.1fs: %d approved, %d failed_validation, %d failed_judge, %d errors",
        elapsed,
        stats.get("approved", 0),
        stats.get("failed_validation", 0),
        stats.get("failed_judge", 0),
        stats.get("error", 0),
    )

    return {
        "approved": stats.get("approved", 0),
        "failed": stats.get("failed_validation", 0) + stats.get("failed_judge", 0),
        "errors": stats.get("error", 0),
        "elapsed": elapsed,
    }
