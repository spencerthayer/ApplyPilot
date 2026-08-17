"""Human-in-the-Loop pause/resume for the apply pipeline.

Extracted from launcher.py. Owns:

* The HITL-specific state — the ring-buffer ``_action_log_cache`` keyed by
  ``sha256(job_url)[:12]`` (populated by the extension content script via
  POST /api/action-log/{hash}, drained by ``_run_hitl`` after the user
  clicks Done), plus the legacy fallback HTTP listener registry, the
  one-at-a-time stdin-fallback lock, and the ``_waiting_workers`` registry.
* The HITL helpers — ``_inject_banner_for_worker`` (CDP banner injection),
  ``_start_hitl_listener`` / ``_stop_hitl_listener``, ``mark_needs_human`` /
  ``reset_needs_human`` (DB row writes), ``_send_desktop_notification`` /
  ``notify_human_needed``, and ``_format_action_log``.
* ``_run_hitl(...)`` — the single entry point that ``_worker_loop_body``
  invokes when an agent returns a needs_human result.

``_worker_state`` and ``_worker_state_lock`` stay in launcher.py because
the always-on per-worker HTTP server, the dashboard renderer, and the
orchestrator all touch them. This module lazy-imports them from
``launcher`` to avoid a circular dependency at import time.

``run_job`` is also lazy-imported inside ``_run_hitl`` for the same reason
(launcher.py re-exports HITL helpers, so a top-level
``from applypilot.apply.launcher import run_job`` here would deadlock the
import).
"""
from __future__ import annotations

import logging
import platform
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

from applypilot import config
from applypilot.apply.chrome import (
    HITL_LISTEN_BASE_PORT,
    bring_to_foreground,
    launch_chrome,
)
from applypilot.database import (
    get_connection,
    transition_state,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

# Track workers waiting for human input (thread-safe).
_waiting_workers: dict[int, str] = {}  # worker_id -> wait type
_waiting_lock = threading.Lock()

# Per-worker HITL HTTP servers (legacy — kept for backwards compat, unused
# when the always-on per-worker server in launcher.py is running).
_hitl_servers: dict[int, HTTPServer] = {}
_hitl_server_lock = threading.Lock()

# Module-level lock for the terminal-stdin Done fallback (audit #6 in the
# apply UX overhaul spec). Only one worker at a time gets the stdin reader,
# to avoid contention when N workers are paused simultaneously.
_stdin_fallback_lock = threading.Lock()

# Action-log cache for the pause-cycle data flow (spec §4.1 / §4.4). The
# extension content script POSTs /api/action-log/{hash} (handled in
# launcher.py's always-on server) with {events, snapshots} when the user
# clicks the banner Done button; that handler writes here. ``_run_hitl``
# pops the matching entry after ``hitl_event`` fires and threads it into
# the resume prompt as a USER ACTIONS DURING PAUSE section.
_action_log_cache: dict[str, dict] = {}
_action_log_cache_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Errors that are "permanent" normally but transient after a HITL pause
# (e.g. backend was down while user was completing the form; retry after 30s).
_HITL_TRANSIENT_ERRORS: frozenset[str] = frozenset(
    {"page_error", "stuck", "browser_unavailable"}
)

_HITL_INSTRUCTIONS: dict[str, str] = {
    "workday_signup": (
        "CREATE a Workday account using {email}. "
        "Click 'Create Account', fill in your email and a password, "
        "verify your email if prompted. Click Done when you're logged in."
    ),
    "login_required": (
        "LOG IN to this site using {email}. "
        "If you don't have an account, create one. "
        "Click Done once you're logged in and on the application page."
    ),
    "login_issue": (
        "LOGIN ISSUE. The agent couldn't complete login. Try logging in manually "
        "with {email} (or create an account if needed). "
        "Click Done when you're logged in and on the application page."
    ),
    "captcha": (
        "CAPTCHA DETECTED. Solve the CAPTCHA shown on the page, then click Done "
        "to let the agent continue the application."
    ),
    "account_required": (
        "ACCOUNT REQUIRED. Create an account using {email}, then navigate "
        "to the job application. Click Done when you're on the application form."
    ),
    "sso_required": (
        "SSO LOGIN REQUIRED. Log in using Google or Microsoft SSO. "
        "Click Done when you're logged in and on the application page."
    ),
    "resume_upload_blocked": (
        "RESUME UPLOAD BLOCKED. Manually upload ~/.applypilot/resume.pdf to the "
        "upload field on the page. Click Done when the file is uploaded."
    ),
    "stuck": (
        "FORM STUCK. The agent got stuck on a form element. Review the form, "
        "fix any issues or stuck fields, and submit if possible. "
        "Click Done when done (even if you only unstuck it for the agent to retry)."
    ),
    "email_verification": (
        "COMPLETE EMAIL VERIFICATION. Check {email} for a verification "
        "email/code, then enter it on the page. Click Done when verified."
    ),
    "sms_verification": (
        "COMPLETE SMS/PHONE VERIFICATION. The site requires a phone code that "
        "the agent cannot receive. Check your phone for the code, enter it on "
        "the page, then click Done."
    ),
    "form_stuck": (
        "COMPLETE THE APPLICATION FORM. The agent filled what it could but got stuck "
        "on a form element (usually a custom dropdown or validation error). "
        "Review the form, fix missing fields, and submit. "
        "Click Done when the application is submitted."
    ),
    "screening_questions": (
        "ANSWER SCREENING QUESTIONS. The agent reached screening questions it wasn't "
        "confident answering from your profile. Review and answer them, then submit. "
        "Click Done when finished."
    ),
    "security_concern": (
        "⚠️ SECURITY ALERT — The agent flagged suspicious content on this form. "
        "Check the apply log for details on what was detected (prompt injection, "
        "bot trap, credential request, or data exfiltration attempt). "
        "Review the page carefully before proceeding. "
        "If the form looks legitimate, complete it manually and click Done. "
        "If it looks malicious, close the tab and click Done to abandon."
    ),
}


def _applicant_email() -> str:
    """The applicant's email from profile.json, for HITL instructions.

    Substituted into the ``{email}`` placeholder in ``_HITL_INSTRUCTIONS`` at
    lookup time. Falls back to a neutral placeholder when no profile is
    configured so we never embed anyone else's personal address in the
    user-facing instruction text.
    """
    try:
        email = (config.load_profile().get("personal", {}) or {}).get("email", "")
        email = (email or "").strip()
    except Exception:
        email = ""
    return email or "your account email"


def get_hitl_instruction(reason: str) -> str:
    """HITL instruction for ``reason`` with the applicant's email filled in."""
    template = _HITL_INSTRUCTIONS.get(reason, f"Human action required: {reason}")
    return template.replace("{email}", _applicant_email())


# ---------------------------------------------------------------------------
# Waiting registry
# ---------------------------------------------------------------------------

def _register_waiting(worker_id: int, wait_type: str) -> None:
    """Register a worker as waiting for human input."""
    with _waiting_lock:
        _waiting_workers[worker_id] = wait_type


def _unregister_waiting(worker_id: int) -> None:
    """Remove a worker from the waiting list."""
    with _waiting_lock:
        _waiting_workers.pop(worker_id, None)


def _get_waiting_count() -> int:
    """Get the number of workers currently waiting for human input."""
    with _waiting_lock:
        return len(_waiting_workers)


# ---------------------------------------------------------------------------
# Per-worker HITL HTTP listener
# ---------------------------------------------------------------------------

def _start_hitl_listener(worker_id: int, done_event: threading.Event,
                         job_hash: str) -> int:
    """Register a HITL done event with the always-on worker listener.

    If the always-on worker listener is running (normal case), stores the
    done_event in the worker state so /api/done/{hash} can fire it.
    Falls back to creating a temporary server if the always-on server isn't up.

    Returns:
        The port the HITL listener is on.
    """
    from applypilot.apply import launcher
    port = HITL_LISTEN_BASE_PORT + worker_id
    with launcher._worker_state_lock:
        state = launcher._worker_state.get(worker_id)
    if state is not None:
        # Always-on server is running — store event reference
        state["hitl_event"] = done_event
        state["hitl_job_hash"] = job_hash
        return port

    # Fallback: start a temporary per-HITL server
    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path.rstrip("/") == f"/api/done/{job_hash}":
                done_event.set()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b"ok")
            else:
                self.send_response(404)
                self.end_headers()

        def do_OPTIONS(self):
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.end_headers()

        def log_message(self, format, *args):
            pass

    try:
        server = HTTPServer(("127.0.0.1", port), _Handler)
    except OSError:
        server = HTTPServer(("127.0.0.1", 0), _Handler)
        port = server.server_address[1]

    with _hitl_server_lock:
        _hitl_servers[worker_id] = server

    thread = threading.Thread(target=server.serve_forever, daemon=True,
                              name=f"hitl-http-w{worker_id}")
    thread.start()
    logger.debug("HITL listener (fallback) for worker %d on port %d", worker_id, port)
    return port


def _stop_hitl_listener(worker_id: int) -> None:
    """Clear HITL event from worker state and kill the done watcher process."""
    from applypilot.apply import launcher
    with launcher._worker_state_lock:
        state = launcher._worker_state.get(worker_id)
    if state is not None:
        state["hitl_event"] = None
        state["hitl_job_hash"] = None
        watcher = state.pop("hitl_watcher_proc", None)
        if watcher is not None and watcher.poll() is None:
            try:
                watcher.kill()
            except Exception:
                pass
    # Also shut down any legacy fallback server
    with _hitl_server_lock:
        server = _hitl_servers.pop(worker_id, None)
    if server:
        server.shutdown()


# ---------------------------------------------------------------------------
# Banner injection
# ---------------------------------------------------------------------------

def _inject_banner_for_worker(worker_id: int, cdp_port: int, job: dict,
                              reason: str, server_port: int,
                              navigate_url: str | None = None,
                              instructions: str | None = None) -> bool:
    """Inject a HITL banner into the worker's Chrome via CDP.

    Navigates to navigate_url first (so the user sees the stuck page, not
    about:blank), injects the banner, then brings Chrome to the foreground.
    """
    from applypilot.apply.human_review import _inject_banner, _navigate_chrome

    # Navigate to the stuck URL so the user sees the page (not about:blank)
    if navigate_url:
        _navigate_chrome(cdp_port, navigate_url)
        time.sleep(1)  # Give the page a moment to start loading

    # Build a job-like dict with HITL fields for the banner
    banner_job = dict(job)
    if instructions is None:
        instructions = get_hitl_instruction(reason)
    banner_job["needs_human_instructions"] = instructions

    result = _inject_banner(cdp_port, banner_job, server_port=server_port)

    # Un-minimize Chrome so the user sees the HITL request
    bring_to_foreground()

    return result


# ---------------------------------------------------------------------------
# DB row mutators
# ---------------------------------------------------------------------------

def mark_needs_human(url: str, reason: str, stuck_url: str,
                     instructions: str, duration_ms: int | None = None) -> None:
    """Park a job for human review instead of marking it as failed."""
    from applypilot.apply.launcher import _db_retry_execute, _db_retry_commit
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    _db_retry_execute(conn, """
        UPDATE jobs SET apply_status = 'needs_human',
                       needs_human_reason = ?,
                       needs_human_url = ?,
                       needs_human_instructions = ?,
                       agent_id = NULL,
                       apply_duration_ms = ?,
                       last_attempted_at = ?,
                       apply_category = 'needs_human'
        WHERE url = ?
    """, (reason, stuck_url, instructions, duration_ms, now, url))

    transition_state(conn, url, "needs_human",
        reason=(reason or "marked needs_human"),
        metadata={"hitl_url": stuck_url,
                  "instructions": instructions[:200] if instructions else None},
        force=True)
    _db_retry_commit(conn)


def reset_needs_human(url: str | None = None) -> int:
    """Reset parked jobs (needs_human) back to NULL so they can be retried.

    Args:
        url: Reset a specific job URL. If None, resets all parked jobs.

    Returns:
        Number of jobs reset.
    """
    from applypilot.apply.launcher import _db_retry_execute, _db_retry_commit
    conn = get_connection()

    if url:
        cursor = _db_retry_execute(conn, """
            UPDATE jobs SET apply_status = NULL,
                           needs_human_reason = NULL,
                           needs_human_url = NULL,
                           needs_human_instructions = NULL,
                           agent_id = NULL,
                           apply_category = NULL
            WHERE url = ? AND apply_status = 'needs_human'
        """, (url,))
        if cursor.rowcount:
            transition_state(conn, url, "applying",
                reason="needs_human resolved, re-acquired",
                force=True)
    else:
        # Fetch URLs before updating so we can emit individual transitions.
        urls_to_reset = [
            r[0] for r in conn.execute(
                "SELECT url FROM jobs WHERE apply_status = 'needs_human'"
            ).fetchall()
        ]
        cursor = _db_retry_execute(conn, """
            UPDATE jobs SET apply_status = NULL,
                           needs_human_reason = NULL,
                           needs_human_url = NULL,
                           needs_human_instructions = NULL,
                           agent_id = NULL,
                           apply_category = NULL
            WHERE apply_status = 'needs_human'
        """)
        for u in urls_to_reset:
            transition_state(conn, u, "applying",
                reason="needs_human resolved, re-acquired",
                force=True)
    _db_retry_commit(conn)
    return cursor.rowcount


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def _send_desktop_notification(title: str, body: str) -> None:
    """Send a desktop notification. Silent on failure."""
    try:
        if platform.system() == "Darwin":
            subprocess.run(
                ["osascript", "-e",
                 f'display notification "{body}" with title "{title}"'],
                timeout=5, capture_output=True,
            )
        else:
            subprocess.run(
                ["notify-send", "--urgency=critical", title, body],
                timeout=5, capture_output=True,
            )
    except Exception:
        pass


def notify_human_needed(job: dict, reason: str, stuck_url: str) -> None:
    """Print a prominent terminal alert and send desktop notification."""
    title = job.get("title", "Unknown")
    company = job.get("site", "")
    score = job.get("fit_score", "?")
    instructions = get_hitl_instruction(reason)

    print(
        f"\n\033[1;35m⚑ HUMAN REVIEW NEEDED ⚑\033[0m\n"
        f"  Job:    {title} @ {company}  (score: {score}/10)\n"
        f"  Reason: {reason}\n"
        f"  URL:    {stuck_url}\n"
        f"  Action: {instructions}\n"
        f"  Review: applypilot human-review\n"
        f"\a",
        file=sys.stderr, flush=True,
    )

    _send_desktop_notification(
        "ApplyPilot: Human Review Needed",
        f"{title} @ {company} — {reason}",
    )


# ---------------------------------------------------------------------------
# Action-log formatting
# ---------------------------------------------------------------------------

def _format_action_log(payload: dict) -> str | None:
    """Render a USER ACTIONS DURING PAUSE prompt section from a content-script
    POST to /api/action-log/{hash}.

    Payload shape:
      {
        "events":    [{"type": "click"|"submit"|"nav", "t": <ms>, ...}, ...],
        "snapshots": {<tab_url>: {<field>: <value>, ...}, ...},
      }

    Returns None if the payload has no usable content. Spec §4.4.
    """
    if not isinstance(payload, dict):
        return None
    events = payload.get("events") or []
    snapshots = payload.get("snapshots") or {}
    if not events and not snapshots:
        return None

    lines: list[str] = []
    if events:
        lines.append("Timeline (in tabs the user touched during pause):")
        # Find a t0 to show offsets, falling back to first-event-as-zero.
        t0 = min((int(e.get("t") or 0) for e in events), default=0)
        for e in events[-50:]:  # cap last 50 events in the prompt
            t = int(e.get("t") or 0)
            offset_ms = max(0, t - t0)
            mins, secs = divmod(offset_ms // 1000, 60)
            ts = f"+{mins}:{secs:02d}"
            kind = e.get("type", "?")
            if kind == "click":
                txt = (e.get("text") or "").strip()[:48]
                href = e.get("href")
                if href:
                    lines.append(f"  {ts}  Clicked '{txt}' → {href[:80]}")
                else:
                    lines.append(f"  {ts}  Clicked '{txt}'")
            elif kind == "submit":
                fcount = len(e.get("fields") or [])
                lines.append(f"  {ts}  Submitted form ({fcount} fields)")
            elif kind == "nav":
                mode = e.get("mode", "")
                url = (e.get("url") or "")[:80]
                lines.append(f"  {ts}  Nav ({mode}) {url}")
            else:
                lines.append(f"  {ts}  {kind}")

    if snapshots:
        lines.append("")
        lines.append("Form values now in tabs:")
        for tab_url, fields in snapshots.items():
            if not isinstance(fields, dict) or not fields:
                continue
            lines.append(f"  {tab_url[:80]}:")
            for k, v in list(fields.items())[:20]:  # cap fields per tab
                vs = str(v)[:80]
                lines.append(f"    {k}: {vs}")

    if not lines:
        return None
    body = "\n".join(lines)
    return f"USER ACTIONS DURING PAUSE:\n{body}"


# ---------------------------------------------------------------------------
# Main HITL pause/resume cycle
# ---------------------------------------------------------------------------

def _run_hitl(
    worker_id: int,
    port: int,
    job: dict,
    reason: str,
    instructions: str,
    navigate_url: str,
    duration_ms: int,
    *,
    headless: bool = False,
    ats_slug: str | None = None,
    total_workers: int = 1,
    model: str = "sonnet",
    dry_run: bool = False,
    no_hitl: bool = False,
    chrome_proc=None,
    add_event=None,
    update_state=None,
    stop_event=None,
) -> tuple[str, int, list[dict]] | None:
    """Block on a needs_human pause; return the post-resume run_job result.

    Replaces the two near-duplicate HITL paths in _worker_loop_body
    (generic + login_required). Steps:

      1. mark_needs_human(...) — DB row says needs_human, prevents stale-lock theft.
      2. If no_hitl: return None (caller should break and move on to next job).
      3. Start hitl_listener (HTTP server on port 7380+wid) for /api/done/{hash}.
      4. Inject banner via CDP → page.
      5. Start the Node-based done watcher (polls window.__ap_hitl_done).
      6. notify_human_needed (desktop notification).
      7. Update worker state to "waiting_human".
      8. Wait on hitl_event with chrome-crash recovery.
      9. reset_needs_human(...) — DB row back to its pre-pause state.
      10. Re-launch agent on same Chrome, retry up to 3× on transient errors.

    Returns (result, duration_ms, screening_qs) from the post-resume run_job,
    or None if no_hitl or stop was signaled.
    """
    import hashlib

    # 1. Persist the needs_human row.
    mark_needs_human(job["url"], reason, navigate_url, instructions, duration_ms)

    # The popup's "no-hitl" toggle mutates worker_state["no_hitl"] live;
    # honor that in preference to the caller's CLI-time default so a user
    # can flip into park-and-move-on mode mid-run (and back) without
    # killing the pipeline.
    from applypilot.apply import launcher
    with launcher._worker_state_lock:
        _ws = launcher._worker_state.get(worker_id) or {}
        _live_no_hitl = _ws.get("no_hitl")
    if _live_no_hitl is not None:
        no_hitl = bool(_live_no_hitl)

    # 2. --no-hitl: park the job and bail.
    if no_hitl:
        if add_event:
            add_event(f"[W{worker_id}] --no-hitl: parking '{reason}' and moving on")
        if update_state:
            update_state(worker_id, last_action=f"parked: {reason[:25]}")
        return None

    # 3. HTTP listener + 4. banner + 5. done watcher.
    job_hash = hashlib.sha256(job["url"].encode()).hexdigest()[:12]
    hitl_event = threading.Event()
    hitl_port = _start_hitl_listener(worker_id, hitl_event, job_hash)

    _inject_banner_for_worker(worker_id, port, job, reason, hitl_port,
                              navigate_url=navigate_url, instructions=instructions)
    from applypilot.apply.human_review import _start_done_watcher
    _watcher = _start_done_watcher(port, hitl_port, job_hash)

    # 6. Desktop notify + 7. worker state.
    notify_human_needed(job, reason, navigate_url)
    if add_event:
        add_event(f"[W{worker_id}] WAITING for human: {reason[:20]}")
    if update_state:
        update_state(worker_id, status="waiting_human",
                     last_action=f"WAITING: {reason[:25]}")
    from applypilot.apply import launcher
    with launcher._worker_state_lock:
        ws = launcher._worker_state.get(worker_id)
    if ws is not None:
        _saved = None
        try:
            from applypilot.database import close_connection, get_qa
            _saved = get_qa(f"HITL:{job.get('site', '')}:{reason}")
            close_connection()
        except Exception:
            pass
        ws.update({"status": "waiting_human", "reason": reason,
                   "instructions": instructions,
                   "saved_instruction": _saved,
                   "hitl_watcher_proc": _watcher})
    _register_waiting(worker_id, "waiting_human")

    # 7b. Terminal-stdin Done fallback (audit #6).
    # If the in-page banner Done button breaks (e.g. CSP blocked the JS, or the
    # Node-based watcher crashed), the user can type 'done' in the launcher
    # terminal to unblock. Only one worker at a time gets the stdin reader to
    # avoid input contention; the second paused worker falls back to
    # banner-only.
    if _stdin_fallback_lock.acquire(blocking=False):
        def _stdin_done_reader() -> None:
            try:
                line = sys.stdin.readline().strip().lower()
                if line in ("done", "d", "") and not hitl_event.is_set():
                    if add_event:
                        add_event(f"[W{worker_id}] stdin fallback: 'done' received")
                    hitl_event.set()
            except Exception:
                logger.debug("stdin fallback reader crashed", exc_info=True)
            finally:
                _stdin_fallback_lock.release()
        try:
            print(
                f"[hitl] worker {worker_id} paused on {navigate_url[:60]} — "
                "type 'done' here to override the banner button",
                flush=True,
            )
        except Exception:
            pass
        threading.Thread(target=_stdin_done_reader, daemon=True).start()

    # 8. Wait, with Chrome-crash recovery.
    while stop_event is None or not stop_event.is_set():
        if hitl_event.wait(timeout=5.0):
            break
        if chrome_proc and chrome_proc.poll() is not None:
            if add_event:
                add_event(f"[W{worker_id}] Chrome crashed during HITL; relaunching...")
            try:
                chrome_proc = launch_chrome(worker_id, port=port,
                                            headless=headless, ats_slug=ats_slug,
                                            total_workers=total_workers)
                _inject_banner_for_worker(worker_id, port, job, reason,
                                          hitl_port, navigate_url=navigate_url,
                                          instructions=instructions)
            except Exception:
                logger.debug("Chrome relaunch during HITL failed", exc_info=True)
    _stop_hitl_listener(worker_id)
    _unregister_waiting(worker_id)
    if stop_event is not None and stop_event.is_set():
        return None

    # 9. Reset DB row.
    reset_needs_human(job["url"])

    # 9b. Pop the action log POSTed by the extension during the pause and
    # format it for the agent's resume prompt (spec §4.4). If no log was
    # posted (extension not ready, or user used the stdin fallback), this
    # is a no-op and we resume without USER ACTIONS context.
    with _action_log_cache_lock:
        log_payload = _action_log_cache.pop(job_hash, None)
    action_log_section = _format_action_log(log_payload) if log_payload else None

    # 10. Re-launch agent with transient-error retry.
    from applypilot.apply.launcher import run_job
    last_result = None
    last_dur = 0
    last_qs: list[dict] = []
    for _attempt in range(3):
        if add_event:
            add_event(f"[W{worker_id}] Human done, relaunching agent"
                      f" (attempt {_attempt + 1}/3)...")
        if update_state:
            update_state(worker_id, status="applying",
                         last_action=f"relaunching after HITL (attempt {_attempt + 1})",
                         start_time=time.time(), actions=0)
        last_result, last_dur, last_qs = run_job(
            job, port=port, worker_id=worker_id,
            model=model, dry_run=dry_run, skip_tab_reset=True,
            extra_context=action_log_section)
        _hitl_reason = last_result.split(":", 1)[-1] if ":" in last_result else last_result
        if _hitl_reason not in _HITL_TRANSIENT_ERRORS:
            break
        if stop_event is not None and stop_event.is_set():
            break
        if _attempt < 2:
            if add_event:
                add_event(f"[W{worker_id}] Transient ({_hitl_reason}), retrying in 30s...")
            time.sleep(30)
    return last_result, last_dur, last_qs
