"""Server — extracted from human_review."""

from __future__ import annotations

import logging
import subprocess
import threading
import time

from applypilot import config
from applypilot.apply.chrome import HITL_CDP_PORT, HITL_WORKER_ID
from applypilot.apply.human_review.banner import (
    _cdp_list_targets,
    _inject_banner,
    _build_banner_js,
    _start_done_watcher,
)
from applypilot.apply.human_review.handler import _Handler

logger = logging.getLogger(__name__)

import hashlib
import sys
import webbrowser
from http.server import HTTPServer

from applypilot.apply.chrome import launch_chrome, cleanup_worker, bring_to_foreground
from applypilot.apply.human_review._state import (
    _sessions,
    _sessions_lock,
    _hitl_chrome_proc,
    _hitl_chrome_lock,
)


def _job_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:12]


def _latest_worker_job_log(worker_id: int, started_at: float) -> str | None:
    """Return the newest per-job log file for the worker started after started_at."""
    pattern = f"agent_*_w{worker_id}_*.txt"
    try:
        candidates = sorted(config.LOG_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for path in candidates:
        try:
            if path.stat().st_mtime + 2 < started_at:
                continue
        except OSError:
            continue
        return str(path)
    return None


def _navigate_chrome(port: int, url: str) -> bool:
    """Navigate the first Chrome tab to a URL via CDP."""
    import urllib.request

    try:
        targets = _cdp_list_targets(port)
        pages = [t for t in targets if t.get("type") == "page"]
        if not pages:
            return False
        tab_id = pages[0]["id"]
        req_url = f"http://localhost:{port}/json/activate/{tab_id}"
        urllib.request.urlopen(req_url, timeout=3)
        # Navigate by opening a new blank tab and using CDP
        # Use the existing tab's websocket to navigate
        # Simple approach: PUT /json/new with URL
        req = urllib.request.Request(f"http://localhost:{port}/json/new?{url}", method="PUT")
        urllib.request.urlopen(req, timeout=3)
        # Close the old blank tab
        try:
            urllib.request.urlopen(f"http://localhost:{port}/json/close/{tab_id}", timeout=2)
        except Exception:
            pass
        return True
    except Exception as e:
        logger.debug("CDP navigate failed: %s", e)
        return False


def _start_hitl_chrome(job: dict) -> subprocess.Popen | None:
    """Launch (or reuse) the HITL Chrome instance and navigate to the stuck URL."""
    global _hitl_chrome_proc

    with _hitl_chrome_lock:
        # Kill old HITL Chrome if still running
        if _hitl_chrome_proc and _hitl_chrome_proc.poll() is None:
            from applypilot.apply.chrome import cleanup_worker

            cleanup_worker(HITL_WORKER_ID, _hitl_chrome_proc)
            _hitl_chrome_proc = None

        stuck_url = job.get("needs_human_url") or job.get("application_url") or job["url"]

        proc = launch_chrome(
            HITL_WORKER_ID,
            port=HITL_CDP_PORT,
            headless=False,
            minimized=False,
        )
        _hitl_chrome_proc = proc

    # Give Chrome time to be ready
    time.sleep(2)

    # Navigate to the stuck URL
    _navigate_chrome(HITL_CDP_PORT, stuck_url)
    time.sleep(1)

    # Inject the banner overlay
    _inject_banner(HITL_CDP_PORT, job)

    # Start watcher that polls for Done signal and re-injects banner on navigation
    h = _job_hash(job.get("url", ""))
    banner_js = _build_banner_js(
        h,
        (job.get("title") or "Unknown").replace("\\", "\\\\").replace("'", "\\'"),
        (job.get("site") or job.get("company") or "").replace("\\", "\\\\").replace("'", "\\'"),
        job.get("fit_score", "?"),
        (job.get("needs_human_instructions") or "Complete the required action.")
        .replace("\\", "\\\\")
        .replace("'", "\\'"),
    )
    _start_done_watcher(HITL_CDP_PORT, 7373, h, banner_js=banner_js)

    # Bring to foreground
    bring_to_foreground()

    return proc


def _run_agent_for_job(h: str) -> None:
    """Background thread: reset job and run apply agent after user clicks Done."""
    from applypilot.apply.launcher import (
        reset_needs_human,
        run_job,
        mark_result,
        mark_needs_human,
        _HITL_INSTRUCTIONS,
    )
    from applypilot.bootstrap import get_app

    with _sessions_lock:
        session = _sessions.get(h)
        if not session:
            return
        job = session["job"]
        session["status"] = "agent_running"
        session.pop("custom_instructions", None)

    # Reset job back to NULL so run_job can acquire it
    reset_needs_human(job["url"])

    # Re-read the job from DB (needs_human columns cleared, fresh state)
    job_repo = get_app().container.job_repo
    fresh = job_repo.find_by_url_fuzzy(job["url"])
    if fresh:
        import dataclasses

        job = dataclasses.asdict(fresh)

    logger.info("[HITL] Spawning agent for job: %s", job.get("title"))

    from applypilot.apply.backends import resolve_backend_name

    result, duration_ms = run_job(
        job,
        port=HITL_CDP_PORT,
        worker_id=HITL_WORKER_ID,
        agent=resolve_backend_name(),
        dry_run=False,
    )

    logger.info("[HITL] Agent result: %s", result)

    # Process result
    if result == "applied":
        mark_result(job["url"], "applied", duration_ms=duration_ms)
        # Save ATS session — the user just authenticated via HITL,
        # so this session has fresh cookies to reuse on future jobs
        from applypilot.apply.chrome import detect_ats, save_ats_session
        from applypilot import config

        ats_slug = detect_ats(job.get("application_url") or job.get("url"))
        if ats_slug:
            profile_dir = config.CHROME_WORKER_DIR / f"worker-{HITL_WORKER_ID}"
            save_ats_session(profile_dir, ats_slug)
            logger.info("[HITL] Saved %s session for future jobs", ats_slug)
    elif result.startswith("needs_human:"):
        after = result[len("needs_human:"):]
        nh_reason, nh_url = after.split(":", 1) if ":" in after else (after, job.get("application_url") or job["url"])
        nh_instructions = _HITL_INSTRUCTIONS.get(nh_reason, f"Human action required: {nh_reason}")
        mark_needs_human(job["url"], nh_reason, nh_url, nh_instructions, duration_ms)
    else:
        reason = result.split(":", 1)[-1] if ":" in result else result
        from applypilot.apply.launcher import _is_permanent_failure

        perm = _is_permanent_failure(result)
        mark_result(job["url"], "failed", reason, permanent=perm, duration_ms=duration_ms)

    with _sessions_lock:
        if h in _sessions:
            _sessions[h]["status"] = "done"
            _sessions[h]["result"] = result


def serve(port: int = 7373, open_browser: bool = True) -> None:
    """Start the HITL review HTTP server.

    Args:
        port: TCP port to bind to (default 7373).
        open_browser: If True, open the review UI in the default browser.

    Raises:
        SystemExit on Ctrl+C.
    """
    global _hitl_chrome_proc

    try:
        server = HTTPServer(("127.0.0.1", port), _Handler)
    except OSError as e:
        if e.errno == 98:  # Address already in use
            print(
                f"\n[red]Port {port} is already in use.[/red]\nTry: applypilot human-review --port {port + 1}",
                file=sys.stderr,
            )
            sys.exit(1)
        raise

    url = f"http://localhost:{port}"
    print(f"\n  Human Review UI → {url}")
    print("  Press Ctrl+C to stop.\n")

    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        # Cleanup HITL Chrome if still running
        with _hitl_chrome_lock:
            if _hitl_chrome_proc and _hitl_chrome_proc.poll() is None:
                cleanup_worker(HITL_WORKER_ID, _hitl_chrome_proc)
                _hitl_chrome_proc = None
        print("\n  Review server stopped.")
