"""Apply orchestration: acquire jobs, spawn browser-agent sessions, track results.

This is the main entry point for the apply pipeline. It pulls jobs from
the database, launches Chrome + a browser agent for each one, parses the
result, and updates the database. Supports parallel workers via --workers.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import platform
import signal
import socketserver
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from applypilot import config
from applypilot.apply import prompt as prompt_mod
from applypilot.apply.backends import (
    extract_result_status,
    get_backend,
)
from applypilot.apply.chrome import (
    launch_chrome,
    cleanup_worker,
    kill_all_chrome,
    cleanup_on_exit,
    _kill_process_tree,
    BASE_CDP_PORT,
)
from applypilot.apply.dashboard import (
    init_worker,
    update_state,
    add_event,
    get_totals,
    log_info as _ui_print,
    log_warning as _ui_warn,
    show_summary as _ui_summary,
    pause_for_input as _pause_chrome,
    start as _dash_start,
    stop as _dash_stop,
)

from applypilot.apply.launcher.job_acquirer import acquire_job, _target_unavailable_reason
from applypilot.apply.launcher.result_tracker import (
    mark_result,
    release_lock,
    _is_permanent_failure,
    _fallback_failure_reason,
)

logger = logging.getLogger(__name__)


# Blocked sites loaded from config/sites.yaml
def _load_blocked():
    from applypilot.config import load_blocked_sites

    return load_blocked_sites()


def pre_navigate_to_job(job: dict, port: int, worker_id: int) -> bool:
    """Preload the target job URL in Chrome before starting OpenCode."""

    try:
        import requests
        import urllib.parse

        job_url = job.get("application_url") or job["url"]
        add_event(f"[W{worker_id}] Pre-navigating to {job_url[:50]}...")
        base_url = f"http://localhost:{port}"

        try:
            list_resp = requests.get(f"{base_url}/json/list", timeout=5)
            if list_resp.status_code == 200:
                for target in list_resp.json():
                    target_id = target.get("id")
                    if target_id and target.get("type") == "page":
                        try:
                            requests.get(f"{base_url}/json/close/{target_id}", timeout=5)
                        except Exception:
                            pass
        except Exception as exc:
            add_event(f"[W{worker_id}] Warning: Could not close existing tabs: {str(exc)[:30]}")

        encoded_url = urllib.parse.quote(job_url, safe="")
        response = requests.get(f"{base_url}/json/new?{encoded_url}", timeout=10)
        if response.status_code != 200:
            add_event(f"[W{worker_id}] Pre-navigation failed: HTTP {response.status_code}")
            return False

        time.sleep(3)
        add_event(f"[W{worker_id}] Pre-navigation complete")
        return True
    except Exception as exc:
        logger.debug("Pre-navigation failed for worker %d: %s", worker_id, exc)
        add_event(f"[W{worker_id}] Pre-navigation failed: {str(exc)[:30]}")
        return False


# How often to poll the DB when the queue is empty (seconds)
POLL_INTERVAL = config.DEFAULTS["poll_interval"]

# Thread-safe shutdown coordination
_stop_event = threading.Event()

# Track active browser-agent processes for skip (Ctrl+C) handling
_agent_procs: dict[int, subprocess.Popen] = {}
_agent_lock = threading.Lock()

# Minimal always-on worker HTTP listener state used by the extension tests and
# by manual browser-control flows. This intentionally keeps the current main
# launcher architecture intact; it only exposes the small API surface the
# extension server relies on.
_worker_servers: dict[int, HTTPServer] = {}
_worker_server_lock = threading.Lock()
_worker_state: dict[int, dict] = {}
_worker_state_lock = threading.Lock()
_takeover_events: dict[int, threading.Event] = {}
_handback_events: dict[int, threading.Event] = {}

# Register cleanup on exit
atexit.register(cleanup_on_exit)
# CHANGED: Guard signal registration to main thread only.
# human_review.py imports launcher from a background thread, and
# signal.signal() raises ValueError outside the main thread.
if platform.system() != "Windows" and threading.current_thread() is threading.main_thread():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))


# ---------------------------------------------------------------------------
# Active agent process tracking
# ---------------------------------------------------------------------------


def _register_agent_process(worker_id: int, proc: subprocess.Popen) -> None:
    with _agent_lock:
        _agent_procs[worker_id] = proc


def _unregister_agent_process(worker_id: int) -> None:
    with _agent_lock:
        _agent_procs.pop(worker_id, None)


def _kill_active_agent_processes() -> None:
    with _agent_lock:
        for proc in list(_agent_procs.values()):
            if proc.poll() is None:
                _kill_process_tree(proc.pid)


class _ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True


def _start_worker_listener(worker_id: int) -> int:
    """Start a lightweight per-worker HTTP server for extension control."""

    state: dict = {
        "job": None,
        "status": "idle",
        "reason": None,
        "instructions": None,
        "hitl_event": None,
        "hitl_job_hash": None,
        "chrome_pid": None,
        "last_focused": 0.0,
        "handback_instructions": None,
        "mini_proc": None,
        "saved_instruction": None,
    }
    takeover_event = threading.Event()
    handback_event = threading.Event()

    with _worker_state_lock:
        _worker_state[worker_id] = state
    _takeover_events[worker_id] = takeover_event
    _handback_events[worker_id] = handback_event

    class _Handler(BaseHTTPRequestHandler):
        def do_OPTIONS(self) -> None:
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self) -> None:
            if self.path == "/api/status":
                self._handle_status()
            elif self.path == "/api/focus":
                self._handle_focus()
            else:
                self.send_response(404)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()

        def do_POST(self) -> None:
            self.close_connection = True
            if self.path.startswith("/api/done"):
                self._handle_done()
            else:
                self.send_response(404)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()

        def _read_body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length))
            except Exception:
                return {}

        def _json_ok(self, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _text_ok(self, text: bytes = b"ok") -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(text)))
            self.end_headers()
            self.wfile.write(text)

        def _handle_status(self) -> None:
            job = state.get("job") or {}
            self._json_ok(
                {
                    "workerId": worker_id,
                    "status": state.get("status", "idle"),
                    "jobTitle": job.get("title", ""),
                    "jobSite": job.get("site", ""),
                    "jobCompany": job.get("company", ""),
                    "score": job.get("fit_score", 0),
                    "reason": state.get("reason", ""),
                    "instructions": state.get("instructions"),
                    "savedInstruction": state.get("saved_instruction"),
                    "chromePid": state.get("chrome_pid"),
                    "lastFocused": state.get("last_focused", 0),
                }
            )

        def _handle_focus(self) -> None:
            state["last_focused"] = time.time()
            try:
                from applypilot.apply.chrome import bring_to_foreground_cdp, bring_to_foreground_pid

                bring_to_foreground_cdp(BASE_CDP_PORT + worker_id)
                bring_to_foreground_pid(state.get("chrome_pid"))
            except Exception:
                logger.debug("Worker focus request failed", exc_info=True)
            self._text_ok()

        def _handle_done(self) -> None:
            body = self._read_body()
            instructions = (body.get("instructions") or "").strip()
            if instructions:
                state["handback_instructions"] = instructions
            hitl_event = state.get("hitl_event")
            if hitl_event:
                state["status"] = "resuming"
                hitl_event.set()
            self._text_ok()

        def log_message(self, format, *args) -> None:
            pass

    server = _ThreadedHTTPServer(("127.0.0.1", 0), _Handler)
    with _worker_server_lock:
        _worker_servers[worker_id] = server
    thread = threading.Thread(
        target=server.serve_forever,
        daemon=True,
        name=f"worker-http-w{worker_id}",
    )
    thread.start()
    return int(server.server_address[1])


def _stop_worker_listener(worker_id: int) -> None:
    """Shut down a worker's lightweight HTTP listener."""

    with _worker_server_lock:
        server = _worker_servers.pop(worker_id, None)
    if server:
        server.shutdown()
        server.server_close()
    with _worker_state_lock:
        _worker_state.pop(worker_id, None)
    _takeover_events.pop(worker_id, None)
    _handback_events.pop(worker_id, None)


def _make_mcp_config(cdp_port: int) -> dict:
    """Build the per-worker MCP config used for manual debug generation."""

    return {
        "mcpServers": {
            "playwright": {
                "command": "npx",
                "args": [
                    "@playwright/mcp@latest",
                    f"--cdp-endpoint=http://localhost:{cdp_port}",
                    f"--viewport-size={config.DEFAULTS['viewport']}",
                ],
            },
            "gmail": {
                "command": "npx",
                "args": ["-y", "@gongrzhe/server-gmail-autoauth-mcp"],
            },
        }
    }


# ---------------------------------------------------------------------------
# Database operations — delegated to job_acquirer.py and result_tracker.py
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Per-job execution (legacy dead code removed — see job_acquirer.py)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Utility modes (--gen, --mark-applied, --mark-failed, --reset-failed)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Utility modes (--gen, --mark-applied, --mark-failed, --reset-failed)
# ---------------------------------------------------------------------------


def gen_prompt(
        target_url: str,
        min_score: int = 7,
        model: str | None = None,
        worker_id: int = 0,
) -> Path | None:
    """Generate a prompt file for manual debugging.

    Returns:
        Path to the generated prompt file, or None if no job found.
    """
    job = acquire_job(target_url=target_url, min_score=min_score, worker_id=worker_id)
    if not job:
        return None

    # Read resume text
    resume_path = job.get("tailored_resume_path")
    txt_path = Path(resume_path).with_suffix(".txt") if resume_path else None
    resume_text = ""
    if txt_path and txt_path.exists():
        resume_text = txt_path.read_text(encoding="utf-8")

    prompt = prompt_mod.build_prompt(job=job, tailored_resume=resume_text)

    # Release the lock so the job stays available
    release_lock(job["url"])

    # Write prompt file
    config.ensure_dirs()
    site_slug = (job.get("site") or "unknown")[:20].replace(" ", "_")
    prompt_file = config.LOG_DIR / f"prompt_{site_slug}_{job['title'][:30].replace(' ', '_')}.txt"
    prompt_file.write_text(prompt, encoding="utf-8")
    mcp_path = config.APP_DIR / f".mcp-apply-{worker_id}.json"
    mcp_path.write_text(json.dumps(_make_mcp_config(BASE_CDP_PORT + worker_id)), encoding="utf-8")
    del model

    return prompt_file


# ---------------------------------------------------------------------------
# Per-job execution
# ---------------------------------------------------------------------------


def run_job(
        job: dict,
        port: int,
        worker_id: int = 0,
        agent: str = "claude",
        model: str | None = None,
        opencode_agent: str | None = None,
        dry_run: bool = False,
) -> tuple[str, int]:
    """Spawn a browser-agent session for one job application.

    Returns:
        Tuple of (status_string, duration_ms). Status is one of:
        'applied', 'expired', 'captcha', 'login_issue',
        'failed:reason', or 'skipped'.
    """
    # Read tailored resume text
    resume_path = job.get("tailored_resume_path")
    txt_path = Path(resume_path).with_suffix(".txt") if resume_path else None
    resume_text = ""
    if txt_path and txt_path.exists():
        resume_text = txt_path.read_text(encoding="utf-8")

    # Build the prompt — native agent uses a lean data-only prompt to avoid
    # contradicting its system prompt. Other backends (Codex, Claude, OpenCode)
    # use the full prompt.py which includes strategy instructions.
    if agent == "native":
        from applypilot.apply.native_prompt import build_native_prompt

        resume_pdf = job.get("tailored_resume_path", "")
        if resume_pdf:
            resume_pdf = str(Path(resume_pdf).with_suffix(".pdf"))
        agent_prompt = build_native_prompt(
            job=job,
            resume_text=resume_text,
            resume_pdf_path=resume_pdf,
            dry_run=dry_run,
        )
    else:
        agent_prompt = prompt_mod.build_prompt(
            job=job,
            tailored_resume=resume_text,
            dry_run=dry_run,
        )

    update_state(
        worker_id,
        status="applying",
        job_title=job["title"],
        company=job.get("site", ""),
        score=job.get("fit_score", 0),
        start_time=time.time(),
        actions=0,
        last_action="starting",
    )
    add_event(f"[W{worker_id}] Starting: {job['title'][:40]} @ {job.get('site', '')}")

    try:
        backend = get_backend(agent)
        backend_name = getattr(backend, "name", agent)
        original_opencode_agent = None
        if backend_name == "opencode" and opencode_agent is not None:
            original_opencode_agent = os.environ.get("APPLY_OPENCODE_AGENT")
            os.environ["APPLY_OPENCODE_AGENT"] = opencode_agent
        try:
            execution = backend.run(
                job=job,
                port=port,
                worker_id=worker_id,
                prompt=agent_prompt,
                model=model,
                register_process=_register_agent_process,
                unregister_process=_unregister_agent_process,
            )
        finally:
            if backend_name == "opencode" and opencode_agent is not None:
                if original_opencode_agent is None:
                    os.environ.pop("APPLY_OPENCODE_AGENT", None)
                else:
                    os.environ["APPLY_OPENCODE_AGENT"] = original_opencode_agent
        if execution.skipped:
            return "skipped", execution.duration_ms

        combined_output = "\n".join(
            part.strip() for part in (execution.final_output, execution.raw_output) if part and part.strip()
        )
        result = extract_result_status(combined_output)
        elapsed = max(1, execution.duration_ms // 1000)

        if result:
            if result.startswith("failed:"):
                reason = result.split(":", 1)[1]
                add_event(f"[W{worker_id}] FAILED ({elapsed}s): {reason[:30]}")
                update_state(worker_id, status="failed", last_action=f"FAILED: {reason[:25]}")
                return result, execution.duration_ms
            add_event(f"[W{worker_id}] {result.upper()} ({elapsed}s): {job['title'][:30]}")
            update_state(worker_id, status=result, last_action=f"{result.upper()} ({elapsed}s)")
            return result, execution.duration_ms

        fallback_reason = _fallback_failure_reason(
            output=combined_output,
            returncode=execution.returncode,
            agent=agent,
        )
        add_event(f"[W{worker_id}] NO RESULT ({elapsed}s)")
        update_state(worker_id, status="failed", last_action=f"FAILED: {fallback_reason[:25]}")
        return f"failed:{fallback_reason}", execution.duration_ms

    except subprocess.TimeoutExpired:
        duration_ms = config.DEFAULTS["apply_timeout"] * 1000
        elapsed = config.DEFAULTS["apply_timeout"]
        add_event(f"[W{worker_id}] TIMEOUT ({elapsed}s)")
        update_state(worker_id, status="failed", last_action=f"TIMEOUT ({elapsed}s)")
        return "failed:timeout", duration_ms
    except Exception as e:
        duration_ms = 0
        add_event(f"[W{worker_id}] ERROR: {str(e)[:40]}")
        update_state(worker_id, status="failed", last_action=f"ERROR: {str(e)[:25]}")
        return f"failed:{str(e)[:100]}", duration_ms


# ---------------------------------------------------------------------------
# Permanent failure classification
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Worker loop
# ---------------------------------------------------------------------------


def worker_loop(
        worker_id: int = 0,
        limit: int | None = None,
        target_url: str | None = None,
        min_score: int = 7,
        headless: bool = False,
        agent: str = "claude",
        model: str | None = None,
        opencode_agent: str | None = None,
        dry_run: bool = False,
        continuous: bool = False,
) -> tuple[int, int]:
    """Run jobs sequentially until the cap is reached or the queue is empty.

    Args:
        worker_id: Numeric worker identifier.
        limit: Max jobs to process. ``None`` drains the current queue.
        target_url: Apply to a specific URL.
        min_score: Minimum fit_score threshold.
        headless: Run Chrome headless.
        agent: Auto-apply browser agent backend.
        model: Backend model override.
        dry_run: Don't click Submit.
        continuous: Keep polling for newly available jobs after the queue empties.

    Returns:
        Tuple of (applied_count, failed_count).
    """
    applied = 0
    failed = 0
    jobs_done = 0
    empty_polls = 0
    port = BASE_CDP_PORT + worker_id

    # Build fair scheduler queue (unless targeting a specific URL)
    _scheduler = None
    if not target_url:
        from applypilot.apply.scheduler import JobScheduler
        _scheduler = JobScheduler()
        _scheduler.load_from_db(min_score=min_score)

    while not _stop_event.is_set():
        if not continuous and limit is not None and jobs_done >= limit:
            break

        update_state(worker_id, status="idle", job_title="", company="", last_action="waiting for job", actions=0)

        if target_url:
            job = acquire_job(target_url=target_url, min_score=min_score, worker_id=worker_id)
        elif _scheduler:
            next_job = _scheduler.next()
            if next_job:
                # Lock the job in DB
                job = acquire_job(target_url=next_job.url, min_score=0, worker_id=worker_id)
            else:
                job = None
        else:
            job = None

        if not job:
            if not continuous:
                if target_url:
                    reason = _target_unavailable_reason(target_url, min_score)
                    add_event(f"[W{worker_id}] Target unavailable: {reason}")
                    update_state(worker_id, status="done", last_action=reason[:35])
                else:
                    add_event(f"[W{worker_id}] Queue empty")
                    update_state(worker_id, status="done", last_action="queue empty")
                break
            empty_polls += 1
            update_state(worker_id, status="idle", last_action=f"polling ({empty_polls})")
            if empty_polls == 1:
                add_event(f"[W{worker_id}] Queue empty, polling every {POLL_INTERVAL}s...")
            # Use Event.wait for interruptible sleep
            if _stop_event.wait(timeout=POLL_INTERVAL):
                break  # Stop was requested during wait
            continue

        empty_polls = 0

        chrome_proc = None
        try:
            add_event(f"[W{worker_id}] Launching Chrome...")
            chrome_proc = launch_chrome(worker_id, port=port, headless=headless)

            if agent == "opencode":
                pre_navigate_to_job(job, port=port, worker_id=worker_id)

            result, duration_ms = run_job(
                job,
                port=port,
                worker_id=worker_id,
                agent=agent,
                model=model,
                opencode_agent=opencode_agent,
                dry_run=dry_run,
            )

            if result == "skipped":
                release_lock(job["url"])
                add_event(f"[W{worker_id}] Skipped: {job['title'][:30]}")
                continue

            # ── Interactive pause: uses dashboard.pause_for_input ─────

            if result == "applied":
                if dry_run:
                    add_event(f"[W{worker_id}] DRY RUN — pausing for verification")
                    _pause_chrome("✅  DRY RUN COMPLETE — Chrome is open. Verify the filled form.")
                mark_result(job["url"], "applied", duration_ms=duration_ms)
                applied += 1
                update_state(worker_id, jobs_applied=applied, jobs_done=applied + failed)
                try:
                    from applypilot.analytics.helpers import emit_job_applied
                    emit_job_applied(job["url"], job.get("site", ""), agent or "native", duration_ms)
                except Exception:
                    pass

            elif result.startswith("needs_human"):
                reason = result.split(":", 1)[-1] if ":" in result else "agent_stuck"
                add_event(f"[W{worker_id}] NEEDS_HUMAN: {reason[:30]}")
                choice = _pause_chrome(
                    f"⚠️  NEEDS HUMAN: {reason}\n"
                    f"    Chrome is open on port {port}.",
                    "Enter=skip | r=retry agent | m=I'll finish manually",
                )

                if choice == "r":
                    # Retry: re-run agent on the same Chrome session
                    add_event(f"[W{worker_id}] Retrying agent...")
                    retry_result, retry_ms = run_job(
                        job, port=port, worker_id=worker_id, agent=agent,
                        model=model, opencode_agent=opencode_agent, dry_run=dry_run,
                    )
                    if retry_result == "applied":
                        mark_result(job["url"], "applied", duration_ms=retry_ms)
                        applied += 1
                        update_state(worker_id, jobs_applied=applied, jobs_done=applied + failed)
                        continue
                    # Still failed after retry — fall through to park
                    reason = retry_result

                elif choice == "m":
                    # Manual: user finishes in Chrome, then confirms
                    _pause_chrome("🖐  Finish the application in Chrome, then press Enter to mark as applied.")
                    mark_result(job["url"], "applied", duration_ms=duration_ms)
                    applied += 1
                    update_state(worker_id, jobs_applied=applied, jobs_done=applied + failed)
                    continue

                # Default (Enter) or failed retry: park for later
                from applypilot.bootstrap import get_app
                _repo = get_app().container.job_repo
                from applypilot.db.dto import ApplyResultDTO
                _repo.update_apply_status(
                    ApplyResultDTO(
                        url=job["url"], apply_status="needs_human",
                        apply_error=reason, apply_duration_ms=duration_ms,
                    )
                )
                failed += 1
                update_state(worker_id, jobs_failed=failed, jobs_done=applied + failed)
                try:
                    from applypilot.analytics.helpers import emit_apply_needs_human
                    emit_apply_needs_human(job["url"], job.get("site", ""), agent or "native", reason)
                except Exception:
                    pass
            else:
                reason = result.split(":", 1)[-1] if ":" in result else result
                mark_result(
                    job["url"], "failed", reason, permanent=_is_permanent_failure(result), duration_ms=duration_ms
                )
                failed += 1
                update_state(worker_id, jobs_failed=failed, jobs_done=applied + failed)
                try:
                    from applypilot.analytics.helpers import emit_apply_failed

                    emit_apply_failed(job["url"], job.get("site", ""), agent or "native", reason)
                except Exception:
                    pass

        except KeyboardInterrupt:
            release_lock(job["url"])
            if _stop_event.is_set():
                break
            add_event(f"[W{worker_id}] Job skipped (Ctrl+C)")
            continue
        except Exception as e:
            logger.exception("Worker %d launcher error", worker_id)
            add_event(f"[W{worker_id}] Launcher error: {str(e)[:40]}")
            release_lock(job["url"])
            failed += 1
            update_state(worker_id, jobs_failed=failed)
        finally:
            if chrome_proc:
                cleanup_worker(worker_id, chrome_proc)

        jobs_done += 1
        if target_url:
            break

    update_state(worker_id, status="done", last_action="finished")
    return applied, failed


# ---------------------------------------------------------------------------
# Main entry point (called from cli.py)
# ---------------------------------------------------------------------------


def main(
        limit: int | None = None,
        target_url: str | None = None,
        min_score: int = 7,
        headless: bool = False,
        agent: str = "claude",
        model: str | None = None,
        opencode_agent: str | None = None,
        dry_run: bool = False,
        continuous: bool = False,
        poll_interval: int = 60,
        workers: int = 1,
) -> None:
    """Launch the apply pipeline.

    Args:
        limit: Max jobs to apply to. ``None`` drains all currently eligible jobs.
        target_url: Apply to a specific URL.
        min_score: Minimum fit_score threshold.
        headless: Run Chrome in headless mode.
        agent: Auto-apply browser agent backend.
        model: Backend model override.
        dry_run: Don't click Submit.
        continuous: Run forever, polling for new jobs after the queue empties.
        poll_interval: Seconds between DB polls when queue is empty.
        workers: Number of parallel workers (default 1).
    """
    global POLL_INTERVAL
    POLL_INTERVAL = poll_interval
    _stop_event.clear()

    config.ensure_dirs()

    if continuous:
        effective_limit = None
        mode_label = "continuous"
    elif limit in (None, 0):
        effective_limit = None
        mode_label = "all available jobs"
    else:
        effective_limit = limit
        mode_label = f"{limit} jobs"

    # Initialize dashboard for all workers
    for i in range(workers):
        init_worker(i)

    worker_label = f"{workers} worker{'s' if workers > 1 else ''}"
    _ui_print(
        f"Launching apply pipeline ({mode_label}, {worker_label}, agent={agent}, poll every {POLL_INTERVAL}s)..."
    )
    _ui_print(
        f"[dim]Agent: {agent} | Model: {model or '(default)'}"
        + (f" | OpenCode sub-agent: {opencode_agent}" if opencode_agent else "")
        + "[/dim]"
    )
    _ui_print("[dim]Ctrl+C = skip current job(s) | Ctrl+C x2 = stop[/dim]")

    # Double Ctrl+C handler
    _ctrl_c_count = 0

    def _sigint_handler(sig, frame):
        nonlocal _ctrl_c_count
        _ctrl_c_count += 1
        if _ctrl_c_count == 1:
            _ui_warn("\n[yellow]Skipping current job(s)... (Ctrl+C again to STOP)[/yellow]")
            _kill_active_agent_processes()
        else:
            _ui_warn("\n[red bold]STOPPING[/red bold]")
            _stop_event.set()
            _kill_active_agent_processes()
            kill_all_chrome()
            raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _sigint_handler)

    try:
        _dash_start()

        if workers == 1:
            # Single worker — run directly in main thread
            total_applied, total_failed = worker_loop(
                worker_id=0,
                limit=effective_limit,
                target_url=target_url,
                min_score=min_score,
                headless=headless,
                agent=agent,
                model=model,
                opencode_agent=opencode_agent,
                dry_run=dry_run,
                continuous=continuous,
            )
        else:
            # Multi-worker — distribute explicit caps across workers.
            if effective_limit is None:
                limits = [None] * workers
            elif effective_limit > 0:
                base = effective_limit // workers
                extra = effective_limit % workers
                limits = [base + (1 if i < extra else 0) for i in range(workers)]
            else:
                limits = [0] * workers

            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="apply-worker") as executor:
                futures = {
                    executor.submit(
                        worker_loop,
                        worker_id=i,
                        limit=limits[i],
                        target_url=target_url,
                        min_score=min_score,
                        headless=headless,
                        agent=agent,
                        model=model,
                        opencode_agent=opencode_agent,
                        dry_run=dry_run,
                        continuous=continuous,
                    ): i
                    for i in range(workers)
                }

                results: list[tuple[int, int]] = []
                for future in as_completed(futures):
                    wid = futures[future]
                    try:
                        results.append(future.result())
                    except Exception:
                        logger.exception("Worker %d crashed", wid)
                        results.append((0, 0))

            total_applied = sum(r[0] for r in results)
            total_failed = sum(r[1] for r in results)

        _dash_stop()

        totals = get_totals()
        _ui_summary(total_applied, total_failed, totals["cost"], str(config.LOG_DIR))

    except KeyboardInterrupt:
        pass
    finally:
        _stop_event.set()
        kill_all_chrome()
