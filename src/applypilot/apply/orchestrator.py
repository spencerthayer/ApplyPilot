"""Apply-pipeline orchestrator: per-worker loop and CLI entry point.

Extracted from launcher.py. This module owns the runtime control flow:

* ``_probe_for_reconnect`` — resume an interrupted job after the launcher
  was killed mid-run.
* ``worker_loop`` / ``_worker_loop_body`` — per-worker driver: acquire a
  job, launch Chrome, run the agent, dispatch results (HITL, takeover,
  Q&A, success, failure, credit exhaustion), and clean up.
* ``_prompt_user_for_qa`` — main-thread TUI for screening-question Q&A.
* ``main`` — the ``applypilot apply`` CLI entry: spawns N workers via a
  ThreadPoolExecutor, runs the Rich Live dashboard, drains the Q&A queue,
  and tears down on Ctrl+C.

Imports a lot of state and helpers from ``launcher`` because launcher
still owns the always-on per-worker HTTP server, the agent invocation
(``run_job``), DB ops, and the cross-worker lifecycle locks. The cycle
``launcher → orchestrator → launcher`` is broken by deferring launcher's
re-exports to the bottom of launcher.py — by the time orchestrator's
top-level imports fire, all of launcher's module state and functions are
already defined.
"""
from __future__ import annotations

import logging
import queue
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from rich.console import Console
from rich.live import Live

from applypilot import config
from applypilot.apply.chrome import (
    BASE_CDP_PORT,
    _AdoptedChromeProcess,
    _chrome_lock,
    _chrome_procs,
    _kill_process_tree,
    cleanup_worker,
    clear_ats_session,
    detect_ats,
    kill_all_chrome,
    launch_chrome,
    prevent_focus_stealing,
    probe_existing_chrome,
    restore_focus_mode,
    save_ats_session,
)
from applypilot.apply.dashboard import (
    add_event,
    get_totals,
    init_worker,
    render_full,
    start_health_checks,
    stop_health_checks,
    update_state,
)
from applypilot.apply.hitl import (
    _get_waiting_count,
    _register_waiting,
    _run_hitl,
    _unregister_waiting,
    get_hitl_instruction,
)
from applypilot.apply.result_handlers import (
    HITL_AUTO_ROUTE,
    _is_permanent_failure,
    _log_failed_attempt,
    _record_job_history,
)
from applypilot.database import (
    commit_with_retry,
    get_connection,
    transition_state,
)

logger = logging.getLogger(__name__)

# Default polling interval when the queue is empty. Mutated by ``main`` via
# ``global POLL_INTERVAL = poll_interval``; read by ``_worker_loop_body``.
POLL_INTERVAL = config.DEFAULTS["poll_interval"]


def _probe_for_reconnect(worker_id: int, port: int) -> tuple[int | None, str | None]:
    """Check if a previous Chrome session can be reconnected to.

    On startup, if a previous `applypilot apply` run was killed while Chrome
    was still running, this detects the live browser and finds the interrupted
    in-progress job so the new run can resume instead of starting fresh.

    Returns:
        (chrome_pid, interrupted_job_url) if a reconnectable Chrome is found,
        or (None, None) if Chrome is not running / profile doesn't match.
    """
    profile_dir = config.CHROME_WORKER_DIR / f"worker-{worker_id}"
    pid = probe_existing_chrome(port, profile_dir)
    if pid is None:
        return None, None

    logger.info(
        "[W%d] Existing Chrome on port %d (pid %d) — checking for interrupted job",
        worker_id, port, pid,
    )
    add_event(f"[W{worker_id}] Reconnecting to existing Chrome (pid {pid})")

    # Find the job this worker was applying to when the pipeline was killed.
    # acquire_job() sets agent_id = "worker-{N}" when locking a job.
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT url, application_url, title FROM jobs "
            "WHERE apply_status = 'in_progress' AND agent_id = ? "
            "ORDER BY last_attempted_at DESC LIMIT 1",
            (f"worker-{worker_id}",),
        ).fetchone()
        if row:
            job_url = row["url"]
            apply_url = row["application_url"] or row["url"]
            title = row["title"] or apply_url
            # Reset the lock so acquire_job() can re-acquire it normally
            conn.execute(
                "UPDATE jobs SET apply_status = NULL, agent_id = NULL WHERE url = ?",
                (job_url,),
            )
            commit_with_retry(conn)
            add_event(
                f"[W{worker_id}] Found interrupted job: {title[:40]} — will resume"
            )
            logger.info("[W%d] Interrupted job reset for reconnect: %s", worker_id, apply_url[:80])
            return pid, apply_url
        else:
            add_event(f"[W{worker_id}] No interrupted job found — Chrome has next job")
            return pid, None
    except Exception as exc:
        logger.warning("[W%d] Reconnect probe: could not look up interrupted job: %s", worker_id, exc)
        return pid, None


def worker_loop(worker_id: int = 0, limit: int = 1,
                target_url: str | None = None,
                min_score: int | None = None,
                max_score: int | None = None,
                max_age_days: int | None = None,
                headless: bool = False,
                model: str = "sonnet", dry_run: bool = False,
                fresh_sessions: bool = False,
                total_workers: int = 1,
                no_hitl: bool = False) -> tuple[int, int]:
    """Run jobs sequentially until limit is reached or queue is empty.

    Args:
        worker_id: Numeric worker identifier.
        limit: Max jobs to process (0 = continuous).
        target_url: Apply to a specific URL.
        min_score: Minimum fit_score threshold.
        max_score: Maximum fit_score threshold (optional).
        max_age_days: Maximum job age in days (optional).
        headless: Run Chrome headless.
        model: Claude model name.
        dry_run: Don't click Submit.
        fresh_sessions: Refresh Chrome session cookies before launching.
        total_workers: Total concurrent workers (used for window tiling).

    Returns:
        Tuple of (applied_count, failed_count).
    """
    from applypilot.apply.launcher import _start_worker_listener, _stop_worker_listener
    if min_score is None:
        min_score = config.DEFAULTS["min_score"]
    if max_age_days is None:
        max_age_days = config.DEFAULTS["max_job_age_days"]

    applied = 0
    failed = 0
    continuous = limit == 0
    jobs_done = 0
    empty_polls = 0
    port = BASE_CDP_PORT + worker_id

    # Start always-on worker HTTP listener (used by Chrome extension + HITL banner)
    _start_worker_listener(worker_id, no_hitl=no_hitl)
    try:
        return _worker_loop_body(
            worker_id, limit, target_url, min_score, max_score, max_age_days,
            headless, model, dry_run, fresh_sessions, applied, failed, continuous,
            jobs_done, empty_polls, port, total_workers, no_hitl=no_hitl,
        )
    finally:
        _stop_worker_listener(worker_id)


def _worker_loop_body(
    worker_id: int, limit: int, target_url: str | None,
    min_score: int, max_score: int | None, max_age_days: int | None,
    headless: bool,
    model: str, dry_run: bool, fresh_sessions: bool,
    applied: int, failed: int, continuous: bool,
    jobs_done: int, empty_polls: int, port: int,
    total_workers: int = 1, no_hitl: bool = False,
) -> tuple[int, int]:
    """Main per-worker processing loop."""
    from applypilot.apply.launcher import (
        _stop_event, _worker_state, _worker_state_lock,
        _takeover_events, _handback_events, _qa_queue,
        run_job, acquire_job, mark_result, release_lock,
    )
    # ── Reconnect probe ───────────────────────────────────────────────────────
    # If a previous run was killed while Chrome was running, adopt the existing
    # browser and resume the interrupted job rather than starting fresh.
    _reconnect_pid, _reconnect_url = _probe_for_reconnect(worker_id, port)
    # ─────────────────────────────────────────────────────────────────────────

    while not _stop_event.is_set():
        if not continuous and jobs_done >= limit:
            break

        update_state(worker_id, status="idle", job_title="", company="",
                     last_action="waiting for job", actions=0)

        # On reconnect, prioritize the interrupted job URL for this iteration only
        _effective_target = _reconnect_url or target_url
        _reconnect_url = None  # clear after first use

        job = acquire_job(target_url=_effective_target, min_score=min_score,
                          max_score=max_score, max_age_days=max_age_days,
                          worker_id=worker_id)
        if not job:
            if not continuous:
                add_event(f"[W{worker_id}] Queue empty")
                update_state(worker_id, status="done", last_action="queue empty")
                break
            empty_polls += 1
            update_state(worker_id, status="idle",
                         last_action=f"polling ({empty_polls})")
            if empty_polls == 1:
                add_event(f"[W{worker_id}] Queue empty, polling every {POLL_INTERVAL}s...")
            # Use Event.wait for interruptible sleep
            if _stop_event.wait(timeout=POLL_INTERVAL):
                break  # Stop was requested during wait
            continue

        empty_polls = 0

        # Consume reconnect state for this job iteration (cleared after first use)
        _this_reconnect_pid = _reconnect_pid
        _this_had_interrupted_job = _effective_target is not None and _effective_target != target_url
        _reconnect_pid = None

        chrome_proc = None
        was_skipped = False
        try:
            # Detect ATS for persistent session overlay
            apply_url = job.get("application_url") or job.get("url", "")
            ats_slug = detect_ats(apply_url)
            if ats_slug:
                add_event(f"[W{worker_id}] ATS: {ats_slug}")

            if _this_reconnect_pid is not None:
                # Reuse the existing Chrome — skip launch entirely
                add_event(f"[W{worker_id}] Reconnecting to Chrome (pid {_this_reconnect_pid})...")
                chrome_proc = _AdoptedChromeProcess(_this_reconnect_pid)
                with _chrome_lock:
                    _chrome_procs[worker_id] = chrome_proc
            else:
                add_event(f"[W{worker_id}] Launching Chrome...")
                chrome_proc = launch_chrome(worker_id, port=port, headless=headless,
                                            refresh_cookies=fresh_sessions,
                                            ats_slug=ats_slug,
                                            total_workers=total_workers)

            with _worker_state_lock:
                ws = _worker_state.get(worker_id)
            if ws is not None:
                ws["chrome_pid"] = chrome_proc.pid

            # Status display: handled by the Chrome extension popup (loaded via
            # --load-extension). No CDP badge injection on the always-on path —
            # on-demand HITL banners (see human_review._inject_banner) cover the
            # few moments an operator needs a visible prompt.

            # Update always-on worker state so the extension popup knows the current job
            with _worker_state_lock:
                ws = _worker_state.get(worker_id)
            if ws is not None:
                ws.update({"job": job, "status": "applying", "reason": None,
                           "instructions": None, "saved_instruction": None})

            # On reconnect with interrupted job: don't reset tabs (form is mid-fill)
            _reconnect_ctx = None
            if _this_had_interrupted_job:
                _reconnect_ctx = (
                    "PIPELINE RESTART: The apply pipeline was killed while you were "
                    "working on this application. The Chrome browser was left running "
                    "with the form potentially partially filled. "
                    "Take a browser_snapshot immediately to see the current page state, "
                    "then continue filling and submitting the application. "
                    "Do NOT navigate away from the current page unless it is completely blank."
                )

            result, duration_ms, screening_qs = run_job(
                job, port=port, worker_id=worker_id,
                model=model, dry_run=dry_run,
                skip_tab_reset=_this_had_interrupted_job,
                extra_context=_reconnect_ctx,
            )

            # --- Relaunch sub-loop: handles Q&A, HITL, and takeover without closing Chrome ---
            relaunch = True
            while relaunch:
                relaunch = False

                if result == "skipped":
                    release_lock(job["url"])
                    add_event(f"[W{worker_id}] Skipped: {(job.get('title') or '')[:30]}")
                    was_skipped = True
                    break
                elif "credits_exhausted" in result:
                    reason = result.split(":", 1)[-1] if ":" in result else result
                    mark_result(job["url"], "failed", reason, permanent=True,
                                duration_ms=duration_ms)
                    _log_failed_attempt(job, reason, worker_id, duration_ms, True)
                    failed += 1
                    _stop_event.set()
                    break
                elif result == "applied":
                    mark_result(job["url"], "applied", duration_ms=duration_ms)
                    _record_job_history(worker_id, job, result, duration_ms)
                    applied += 1
                    update_state(worker_id, jobs_applied=applied,
                                 jobs_done=applied + failed)
                    if ats_slug:
                        profile_dir = config.CHROME_WORKER_DIR / f"worker-{worker_id}"
                        save_ats_session(profile_dir, ats_slug)
                    break

                elif result == "takeover":
                    # User clicked "Take Over" in the extension popup.
                    # The Claude proc was already killed by the takeover handler.
                    # Wait for the user to click "Give Back Control" (handback event).
                    add_event(f"[W{worker_id}] PAUSED by user: {(job.get('title') or '')[:30]}")
                    update_state(worker_id, status="paused_by_user",
                                 last_action="paused by user")
                    _register_waiting(worker_id, "waiting_human")

                    hb_event = _handback_events.get(worker_id)
                    if hb_event:
                        hb_event.clear()  # Clear any stale signal from previous job

                    while not _stop_event.is_set():
                        if hb_event and hb_event.wait(timeout=5.0):
                            break
                    _unregister_waiting(worker_id)
                    if _stop_event.is_set():
                        break

                    # Collect handback instructions from always-on server state
                    extra_ctx = None
                    with _worker_state_lock:
                        ws = _worker_state.get(worker_id)
                    if ws is not None:
                        extra_ctx = ws.get("handback_instructions")
                        ws["handback_instructions"] = None
                        ws["status"] = "applying"

                    # Clear takeover event so next run_job() doesn't exit immediately
                    tev = _takeover_events.get(worker_id)
                    if tev:
                        tev.clear()

                    add_event(f"[W{worker_id}] Resuming after user takeover...")
                    update_state(worker_id, status="applying",
                                 last_action="resuming after takeover",
                                 start_time=time.time(), actions=0)
                    result, duration_ms, screening_qs = run_job(
                        job, port=port, worker_id=worker_id,
                        model=model, dry_run=dry_run,
                        skip_tab_reset=True, extra_context=extra_ctx,
                    )
                    relaunch = True
                    continue

                elif result.startswith("needs_human:"):
                    # Parse reason and URL (optional |detail:... suffix from agent)
                    after = result[len("needs_human:"):]
                    if ":" in after:
                        nh_reason, nh_url = after.split(":", 1)
                    else:
                        nh_reason, nh_url = after, job.get("application_url") or job["url"]
                    # Extract detail suffix: "https://url|detail:reason text"
                    nh_detail = ""
                    if "|detail:" in nh_url:
                        nh_url, nh_detail = nh_url.split("|detail:", 1)
                        nh_url = nh_url.strip()
                        nh_detail = nh_detail.strip()

                    # --- Screening Q&A: interactive TUI answers + relaunch ---
                    if nh_reason == "screening_questions" and screening_qs:
                        add_event(f"[W{worker_id}] Q&A: {len(screening_qs)} question(s) — waiting for answers")
                        update_state(worker_id, status="waiting_answer",
                                     last_action=f"Q&A: {len(screening_qs)} question(s)")
                        _register_waiting(worker_id, "waiting_answer")

                        # Post to the Q&A queue — main thread will prompt user
                        answer_event = threading.Event()
                        _qa_queue.put((worker_id, screening_qs, answer_event))

                        # Block until main thread provides answers (interruptible)
                        while not _stop_event.is_set():
                            if answer_event.wait(timeout=5.0):
                                break
                        _unregister_waiting(worker_id)
                        if _stop_event.is_set():
                            break

                        # Relaunch agent on same Chrome (form still open)
                        add_event(f"[W{worker_id}] Relaunching with Q&A answers...")
                        update_state(worker_id, status="applying",
                                     last_action="relaunching with answers",
                                     start_time=time.time(), actions=0)
                        result, duration_ms, screening_qs = run_job(
                            job, port=port, worker_id=worker_id,
                            model=model, dry_run=dry_run, skip_tab_reset=True)
                        relaunch = True
                        continue

                    # --- General HITL: keep Chrome open, inject banner, wait ---
                    nh_instructions = get_hitl_instruction(nh_reason)
                    if nh_detail:
                        nh_instructions = f"{nh_instructions}\n\nAgent detail: {nh_detail}"

                    hitl_outcome = _run_hitl(
                        worker_id=worker_id, port=port, job=job,
                        reason=nh_reason, instructions=nh_instructions,
                        navigate_url=nh_url, duration_ms=duration_ms,
                        headless=headless, ats_slug=ats_slug,
                        total_workers=total_workers, model=model, dry_run=dry_run,
                        no_hitl=no_hitl, chrome_proc=chrome_proc,
                        add_event=add_event, update_state=update_state,
                        stop_event=_stop_event,
                    )
                    if hitl_outcome is None:
                        # no_hitl mode parked the job, or stop was signaled.
                        break
                    result, duration_ms, screening_qs = hitl_outcome
                    relaunch = True
                    continue

                else:
                    reason = result.split(":", 1)[-1] if ":" in result else result
                    # login_required: route to HITL with banner + wait
                    if reason == "login_required":
                        if ats_slug:
                            clear_ats_session(ats_slug)
                        nh_url = job.get("application_url") or job["url"]
                        nh_instructions = get_hitl_instruction("login_required")

                        hitl_outcome = _run_hitl(
                            worker_id=worker_id, port=port, job=job,
                            reason="login_required", instructions=nh_instructions,
                            navigate_url=nh_url, duration_ms=duration_ms,
                            headless=headless, ats_slug=ats_slug,
                            total_workers=total_workers, model=model, dry_run=dry_run,
                            no_hitl=no_hitl, chrome_proc=chrome_proc,
                            add_event=add_event, update_state=update_state,
                            stop_event=_stop_event,
                        )
                        if hitl_outcome is None:
                            break
                        result, duration_ms, screening_qs = hitl_outcome
                        relaunch = True
                        continue

                    elif reason in HITL_AUTO_ROUTE:
                        # Route to HITL instead of marking as permanent failure.
                        # User intervenes via the Chrome banner, then agent relaunches.
                        nh_url = job.get("application_url") or job["url"]
                        nh_instructions = get_hitl_instruction(reason)

                        hitl_outcome = _run_hitl(
                            worker_id=worker_id, port=port, job=job,
                            reason=reason, instructions=nh_instructions,
                            navigate_url=nh_url, duration_ms=duration_ms,
                            headless=headless, ats_slug=ats_slug,
                            total_workers=total_workers, model=model, dry_run=dry_run,
                            no_hitl=no_hitl, chrome_proc=chrome_proc,
                            add_event=add_event, update_state=update_state,
                            stop_event=_stop_event,
                        )
                        if hitl_outcome is None:
                            break
                        result, duration_ms, screening_qs = hitl_outcome
                        relaunch = True
                        continue

                    else:
                        perm = _is_permanent_failure(result)
                        mark_result(job["url"], "failed", reason,
                                    permanent=perm, duration_ms=duration_ms)
                        _log_failed_attempt(job, reason, worker_id, duration_ms, perm)
                        _record_job_history(worker_id, job, result, duration_ms)
                        failed += 1
                        update_state(worker_id, jobs_failed=failed,
                                     jobs_done=applied + failed)

        except KeyboardInterrupt:
            release_lock(job["url"])
            if _stop_event.is_set():
                break
            add_event(f"[W{worker_id}] Job skipped (Ctrl+C)")
            continue
        except Exception as e:
            logger.exception("Worker %d launcher error", worker_id)
            add_event(f"[W{worker_id}] Launcher error: {type(e).__name__}: {str(e)[:35]}")
            _log_failed_attempt(job, f"launcher_error:{str(e)[:80]}", worker_id, 0, False)
            release_lock(job["url"])
            failed += 1
            update_state(worker_id, jobs_failed=failed)
        finally:
            if chrome_proc:
                cleanup_worker(worker_id, chrome_proc)

        if was_skipped:
            continue
        jobs_done += 1
        if target_url:
            break

    update_state(worker_id, status="done", last_action="finished")
    return applied, failed


# ---------------------------------------------------------------------------
# Q&A interactive prompt (called from main thread)
# ---------------------------------------------------------------------------

def _prompt_user_for_qa(console: Console, worker_id: int,
                        questions: list[dict]) -> list[str]:
    """Prompt the user in the terminal for screening question answers.

    Args:
        console: Rich Console for pretty printing.
        worker_id: Which worker needs answers.
        questions: List of question dicts with keys: question, field_type, options.

    Returns:
        List of answer strings (one per question).
    """
    console.print(f"\n[bold cyan]Worker {worker_id} needs your help with screening questions:[/bold cyan]")
    answers: list[str] = []
    for i, q in enumerate(questions, 1):
        console.print(f"\n  [bold]Q{i}:[/bold] {q['question']}")
        if q.get("field_type"):
            console.print(f"  [dim]Type: {q['field_type']}[/dim]")
        if q.get("options"):
            opts = q["options"].split(",") if isinstance(q["options"], str) else q["options"]
            opts = [o.strip() for o in opts if o.strip()]
            if opts:
                for j, opt in enumerate(opts, 1):
                    console.print(f"    {j}. {opt}")
        try:
            answer = console.input("  [bold]Your answer: [/bold]")
            # If user typed a number and there are options, map to the option text
            if q.get("options") and answer.strip().isdigit():
                opts = [o.strip() for o in q["options"].split(",") if o.strip()]
                idx = int(answer.strip()) - 1
                if 0 <= idx < len(opts):
                    answer = opts[idx]
            answers.append(answer.strip())
        except (EOFError, KeyboardInterrupt):
            answers.append("")
    console.print(f"[green]Answers recorded. Relaunching agent for Worker {worker_id}...[/green]\n")
    return answers


# ---------------------------------------------------------------------------
# Main entry point (called from cli.py)
# ---------------------------------------------------------------------------

def main(limit: int = 1, target_url: str | None = None,
         min_score: int | None = None, max_score: int | None = None,
         max_age_days: int | None = None,
         headless: bool = False, model: str = "sonnet",
         dry_run: bool = False, continuous: bool = False,
         poll_interval: int = 60, workers: int = 1,
         fresh_sessions: bool = False, no_hitl: bool = False,
         no_focus: bool = False) -> None:
    """Launch the apply pipeline.

    Args:
        limit: Max jobs to apply to (0 or with continuous=True means run forever).
        target_url: Apply to a specific URL.
        min_score: Minimum fit_score threshold.
        max_score: Maximum fit_score threshold (optional, for testing on lower-score jobs).
        max_age_days: Maximum job age in days (optional).
        headless: Run Chrome in headless mode.
        model: Claude model name.
        dry_run: Don't click Submit.
        continuous: Run forever, polling for new jobs.
        poll_interval: Seconds between DB polls when queue is empty.
        workers: Number of parallel workers (default 1).
        fresh_sessions: Refresh Chrome session cookies from user's real profile.
        no_hitl: Skip human-in-the-loop waits; park jobs as needs_human and move on.
        no_focus: Prevent Chrome windows from stealing keyboard focus (Linux/GNOME only).
    """
    from applypilot.apply.launcher import (
        _stop_event, _claude_lock, _claude_procs, _qa_queue,
    )
    global POLL_INTERVAL
    POLL_INTERVAL = poll_interval
    _stop_event.clear()

    config.ensure_dirs()
    console = Console()
    _prev_focus_mode: str | None = None  # set before workers start; restored in finally

    # Re-queue any jobs stuck in needs_human from a previous session.
    # Their Chrome windows are gone (killed by _kill_on_port() when workers start),
    # so reset them to NULL so they get picked up as normal jobs.
    _boot_conn = get_connection()
    # P0.5 leak (d) from decision #31: pull URLs first, then bulk-update,
    # then emit per-row state transitions back to ready_to_apply.
    _nh_urls = [r[0] for r in _boot_conn.execute(
        "SELECT url FROM jobs WHERE apply_status='needs_human'"
    ).fetchall()]
    _nh_count = len(_nh_urls)
    if _nh_count > 0:
        _boot_conn.execute(
            "UPDATE jobs SET apply_status=NULL, apply_category=NULL, "
            "needs_human_reason=NULL, needs_human_url=NULL, "
            "needs_human_instructions=NULL WHERE apply_status='needs_human'"
        )
        for _nh_url in _nh_urls:
            try:
                transition_state(
                    _boot_conn, _nh_url, "ready_to_apply",
                    reason="startup re-queue from needs_human",
                    force=True,
                )
            except Exception:
                logger.debug("startup re-queue transition failed for %s",
                             _nh_url[:60], exc_info=True)
        commit_with_retry(_boot_conn)
        console.print(f"[yellow]Re-queued {_nh_count} needs_human job(s) from previous session[/yellow]")
        logger.info("Startup: re-queued %d needs_human jobs from previous session", _nh_count)

    if continuous:
        effective_limit = 0
        mode_label = "continuous"
    else:
        effective_limit = limit
        mode_label = f"{limit} jobs"

    # Initialize dashboard for all workers
    for i in range(workers):
        init_worker(i)

    start_health_checks()

    worker_label = f"{workers} worker{'s' if workers > 1 else ''}"
    console.print(f"Launching apply pipeline ({mode_label}, {worker_label}, poll every {POLL_INTERVAL}s)...")
    console.print("[dim]Ctrl+C = skip current job(s) | Ctrl+C x2 = stop[/dim]")

    # Double Ctrl+C handler
    _ctrl_c_count = 0

    def _sigint_handler(sig, frame):
        nonlocal _ctrl_c_count
        _ctrl_c_count += 1
        if _ctrl_c_count == 1:
            console.print("\n[yellow]Skipping current job(s)... (Ctrl+C again to STOP)[/yellow]")
            # Kill all active Claude processes to skip current jobs
            with _claude_lock:
                for wid, cproc in list(_claude_procs.items()):
                    if cproc.poll() is None:
                        _kill_process_tree(cproc.pid)
        else:
            console.print("\n[red bold]STOPPING[/red bold]")
            _stop_event.set()
            with _claude_lock:
                for wid, cproc in list(_claude_procs.items()):
                    if cproc.poll() is None:
                        _kill_process_tree(cproc.pid)
            kill_all_chrome()
            raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _sigint_handler)

    try:
        with Live(render_full(), console=console, refresh_per_second=2) as live:
            # Daemon thread for display refresh only (no business logic)
            _dashboard_running = True

            def _refresh():
                while _dashboard_running:
                    try:
                        live.update(render_full())
                    except Exception:
                        pass
                    time.sleep(0.5)

            refresh_thread = threading.Thread(target=_refresh, daemon=True)
            refresh_thread.start()

            # Always use executor — main thread handles Q&A input
            if effective_limit:
                base = effective_limit // workers
                extra = effective_limit % workers
                limits = [base + (1 if i < extra else 0)
                          for i in range(workers)]
            else:
                limits = [0] * workers  # continuous mode

            # Prevent Chrome windows from stealing keyboard focus while workers run.
            # Restores the previous GNOME focus-new-windows setting when done.
            _prev_focus_mode = prevent_focus_stealing() if (no_focus and not headless) else None

            with ThreadPoolExecutor(max_workers=workers,
                                    thread_name_prefix="apply-worker") as executor:
                futures = {
                    executor.submit(
                        worker_loop,
                        worker_id=i,
                        limit=limits[i],
                        target_url=target_url,
                        min_score=min_score,
                        max_score=max_score,
                        max_age_days=max_age_days,
                        headless=headless,
                        model=model,
                        dry_run=dry_run,
                        fresh_sessions=fresh_sessions,
                        total_workers=workers,
                        no_hitl=no_hitl,
                    ): i
                    for i in range(workers)
                }

                # --- Main thread event loop: Q&A input + all-blocked detection ---
                _all_blocked_prompted = False
                while not all(f.done() for f in futures):
                    # Check Q&A queue for screening questions from workers
                    try:
                        wid, questions, answer_event = _qa_queue.get(timeout=0.5)
                        _dashboard_running = False  # pause refresh thread
                        time.sleep(0.6)  # let refresh thread finish current cycle
                        live.stop()

                        answers = _prompt_user_for_qa(console, wid, questions)
                        # Store answers in Q&A knowledge base
                        from applypilot.database import store_qa
                        for q_dict, ans in zip(questions, answers):
                            if ans:
                                store_qa(q_dict["question"], ans, source="human",
                                         field_type=q_dict.get("field_type"))
                        answer_event.set()  # unblock the worker

                        live.start()
                        _dashboard_running = True
                    except queue.Empty:
                        pass

                    # Check if all workers are blocked
                    waiting = _get_waiting_count()
                    active_workers = sum(
                        1 for f in futures if not f.done()
                    )
                    if waiting > 0 and waiting >= active_workers and not _all_blocked_prompted:
                        _all_blocked_prompted = True
                        add_event(f"[bold magenta]All {waiting} active worker(s) waiting for human input[/bold magenta]")

                    if _all_blocked_prompted and _get_waiting_count() == 0:
                        _all_blocked_prompted = False

                results: list[tuple[int, int]] = []
                for future in futures:
                    wid = futures[future]
                    try:
                        results.append(future.result())
                    except Exception:
                        logger.exception("Worker %d crashed", wid)
                        results.append((0, 0))

            total_applied = sum(r[0] for r in results)
            total_failed = sum(r[1] for r in results)

            _dashboard_running = False
            refresh_thread.join(timeout=2)
            live.update(render_full())

        totals = get_totals()
        console.print(
            f"\n[bold]Done: {total_applied} applied, {total_failed} failed "
            f"(${totals['cost']:.3f})[/bold]"
        )
        console.print(f"Logs: {config.LOG_DIR}")

    except KeyboardInterrupt:
        pass
    finally:
        _stop_event.set()
        stop_health_checks()
        kill_all_chrome()
        restore_focus_mode(_prev_focus_mode)
