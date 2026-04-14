"""Cover letter generation — re-exports and batch orchestrator."""

from __future__ import annotations

import dataclasses
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from applypilot.config import COVER_LETTER_DIR, load_profile, load_resume_text
from applypilot.scoring.artifact_naming import build_artifact_prefix

# Re-export from decomposed modules
from applypilot.scoring.cover_letter.generator import generate_cover_letter, _strip_preamble  # noqa: F401
from applypilot.scoring.cover_letter.prompt_builder import build_cover_letter_prompt  # noqa: F401

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5

# Keep old name for backward compat
_build_cover_letter_prompt = build_cover_letter_prompt


def run_cover_letters(
    min_score: int = 7, limit: int = 0, validation_mode: str = "normal", job_url: str | None = None
) -> dict:
    """Generate cover letters for high-scoring jobs that have tailored resumes."""
    profile = load_profile()

    # Respect cover_letter.enabled from tailoring_config (default: True for backward compat)
    tc = profile.get("tailoring_config", {})
    cl_cfg = tc.get("cover_letter", {}) if isinstance(tc, dict) else {}
    if isinstance(cl_cfg, dict) and cl_cfg.get("enabled") is False:
        log.info("Cover letter generation disabled in profile.json (cover_letter.enabled=false)")
        return {"generated": 0, "errors": 0, "elapsed": 0.0, "skipped": "disabled"}

    try:
        resume_text = load_resume_text()
    except FileNotFoundError:
        log.error("Resume file not found. Run 'applypilot init' first.")
        return {"generated": 0, "errors": 0, "elapsed": 0.0}

    from applypilot.bootstrap import get_app

    _job_repo = get_app().container.job_repo
    jobs_raw = _job_repo.get_jobs_by_stage_dict(
        stage="pending_cover",
        min_score=min_score,
        limit=limit,
        job_url=job_url,
    )

    if not jobs_raw:
        log.info("No jobs needing cover letters (score >= %d).", min_score)
        return {"generated": 0, "errors": 0, "elapsed": 0.0}

    jobs = [dataclasses.asdict(j) if not isinstance(j, dict) else j for j in jobs_raw]

    COVER_LETTER_DIR.mkdir(parents=True, exist_ok=True)
    log.info("Generating cover letters for %d jobs (score >= %d)...", len(jobs), min_score)
    t0 = time.time()
    completed = 0
    results: list[dict] = []
    error_count = 0

    for job in jobs:
        completed += 1
        try:
            tailored_path = job.get("tailored_resume_path")
            if tailored_path and Path(tailored_path).exists():
                job_resume = Path(tailored_path).read_text(encoding="utf-8")
            else:
                job_resume = resume_text
            letter = generate_cover_letter(job_resume, job, profile, validation_mode=validation_mode)

            prefix = build_artifact_prefix(job)
            cl_path = COVER_LETTER_DIR / f"{prefix}_CL.txt"
            cl_path.write_text(letter, encoding="utf-8")

            pdf_path = None
            try:
                from applypilot.scoring.pdf import convert_to_pdf

                pdf_path = str(convert_to_pdf(cl_path))
            except Exception:
                log.debug("PDF generation failed for %s", cl_path, exc_info=True)

            results.append(
                {
                    "url": job["url"],
                    "path": str(cl_path),
                    "pdf_path": pdf_path,
                    "title": job["title"],
                    "site": job["site"],
                }
            )

            # Copy to organized folder
            try:
                from applypilot.config.paths import organized_job_dir, ORGANIZED_DIR
                import shutil

                org_dir = organized_job_dir(
                    ORGANIZED_DIR,
                    job.get("site", ""),
                    job.get("title", ""),
                )
                for src in [cl_path, Path(pdf_path) if pdf_path else None]:
                    if src and src.exists():
                        shutil.copy2(src, org_dir / f"cover_letter{src.suffix}")
            except Exception:
                pass

            elapsed = time.time() - t0
            rate = completed / elapsed if elapsed > 0 else 0
            log.info("%d/%d [OK] | %.1f jobs/min | %s", completed, len(jobs), rate * 60, job["title"][:40])
        except Exception as e:
            error_count += 1
            results.append(
                {
                    "url": job["url"],
                    "title": job["title"],
                    "site": job["site"],
                    "path": None,
                    "pdf_path": None,
                    "error": str(e),
                }
            )
            log.error("%d/%d [ERROR] %s -- %s", completed, len(jobs), job["title"][:40], e)

    now = datetime.now(timezone.utc).isoformat()
    saved = 0

    from applypilot.bootstrap import get_app
    from applypilot.db.dto import CoverLetterResultDTO

    _job_repo = get_app().container.job_repo

    for r in results:
        if r.get("path"):
            _job_repo.update_cover_letter(
                CoverLetterResultDTO(url=r["url"], cover_letter_path=r["path"], cover_letter_at=now)
            )
            saved += 1
        _job_repo.increment_attempts(r["url"], "cover_attempts")

    # Release shared Playwright browser
    try:
        from applypilot.scoring.pdf.pdf_renderer import close_shared_browser
        close_shared_browser()
    except Exception:
        pass

    elapsed = time.time() - t0
    log.info("Cover letters done in %.1fs: %d generated, %d errors", elapsed, saved, error_count)
    return {"generated": saved, "errors": error_count, "elapsed": round(elapsed, 1), "results": results}
