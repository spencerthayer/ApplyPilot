"""Apply orchestration: acquire jobs, spawn Claude Code sessions, track results.

This is the main entry point for the apply pipeline. It pulls jobs from
the database, launches Chrome + Claude Code for each one, parses the
result, and updates the database. Supports parallel workers via --workers.
"""

import atexit
import json
import logging
import os
import platform
import queue
import re
import signal
import socketserver
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


from applypilot import config
from applypilot.database import (
    get_connection,
    categorize_apply_result,
    commit_with_retry,
    get_in_flight_by_company,
    transition_state,
)
from applypilot.apply import prompt as prompt_mod
from applypilot.apply.chrome import (  # noqa: F401  (re-exports)
    launch_chrome, cleanup_worker, kill_all_chrome,
    detect_ats, save_ats_session, clear_ats_session,
    reset_worker_dir, cleanup_on_exit, _kill_process_tree,
    BASE_CDP_PORT, HITL_LISTEN_BASE_PORT, bring_to_foreground,
    probe_existing_chrome, _AdoptedChromeProcess,
    _chrome_procs, _chrome_lock,
)
from applypilot.apply.dashboard import (  # noqa: F401  (re-exports)
    init_worker, update_state, add_event, get_state,
    render_full, get_totals, start_health_checks, stop_health_checks,
)
from applypilot.apply.result_handlers import (  # noqa: F401  (re-exports)
    PERMANENT_FAILURES,
    HITL_AUTO_ROUTE,
    RETRYABLE_AUTH_FAILURES,
    PERMANENT_PREFIXES,
    _NEXT_STEPS,
    _FAILED_LOG,
    _MANUAL_LOG,
    _parse_account_created,
    _parse_qa_lines,
    _infer_result_from_output,
    _is_permanent_failure,
    _log_failed_attempt,
    _log_manual_action,
    _record_job_history,
)
from applypilot.apply.hitl import (  # noqa: F401  (re-exports)
    _HITL_TRANSIENT_ERRORS,
    _HITL_INSTRUCTIONS,
    get_hitl_instruction,
    _action_log_cache,
    _action_log_cache_lock,
    _stdin_fallback_lock,
    _waiting_workers,
    _waiting_lock,
    _hitl_servers,
    _hitl_server_lock,
    _register_waiting,
    _unregister_waiting,
    _get_waiting_count,
    _start_hitl_listener,
    _stop_hitl_listener,
    _inject_banner_for_worker,
    mark_needs_human,
    reset_needs_human,
    _send_desktop_notification,
    notify_human_needed,
    _format_action_log,
    _run_hitl,
)

logger = logging.getLogger(__name__)

# Document format for resume/cover letter uploads ("pdf" or "docx").
# Set once by the CLI before workers start; read by run_job/gen_prompt.
_doc_format: str = "docx"


def set_doc_format(fmt: str) -> None:
    """Set the document format for apply uploads."""
    global _doc_format
    _doc_format = fmt


# Blocked sites loaded from config/sites.yaml
def _load_blocked():
    from applypilot.config import load_blocked_sites
    return load_blocked_sites()

# How often to poll the DB when the queue is empty: now lives in
# apply/orchestrator.py (mutated by main(); read by _worker_loop_body).
# Re-exported via the bottom-of-file import so `launcher.POLL_INTERVAL`
# keeps working for any external caller.

# Thread-safe shutdown coordination
_stop_event = threading.Event()

# Track active Claude Code processes for skip (Ctrl+C) handling
_claude_procs: dict[int, subprocess.Popen] = {}
_claude_lock = threading.Lock()

# Register cleanup on exit
atexit.register(cleanup_on_exit)


def _kill_all_children() -> None:
    """Kill all Claude subprocesses and mini-task procs."""
    with _claude_lock:
        procs = list(_claude_procs.values())
        _claude_procs.clear()
    for p in procs:
        if p.poll() is None:
            try:
                _kill_process_tree(p.pid)
            except Exception:
                pass
    for p in list(_mini_procs.values()):
        if p.poll() is None:
            try:
                _kill_process_tree(p.pid)
            except Exception:
                pass
    _mini_procs.clear()


atexit.register(_kill_all_children)

if platform.system() != "Windows":
    def _sigterm_handler(*_):
        _stop_event.set()
        _kill_all_children()
        kill_all_chrome()
        sys.exit(0)
    signal.signal(signal.SIGTERM, _sigterm_handler)

# Q&A interactive queue: worker threads post questions, main thread answers
# Each item: (worker_id, questions_list, answer_event)
_qa_queue: queue.Queue = queue.Queue()

# HITL state moved to apply/hitl.py and re-exported above:
#   _waiting_workers, _waiting_lock, _hitl_servers, _hitl_server_lock,
#   _stdin_fallback_lock, _action_log_cache, _action_log_cache_lock.
# HITL_LISTEN_BASE_PORT (7380 + worker_id) is defined in chrome.py.

# Always-on per-worker HTTP servers (one per worker, started once in worker_loop)
_worker_servers: dict[int, HTTPServer] = {}
_worker_server_lock = threading.Lock()

# Per-worker mutable state, closed over by each worker's HTTP handler
# Keys: job, status, reason, instructions, hitl_event, hitl_job_hash,
#       handback_instructions, mini_proc
_worker_state: dict[int, dict] = {}
_worker_state_lock = threading.Lock()

# Per-worker takeover/handback events
_takeover_events: dict[int, threading.Event] = {}
_handback_events: dict[int, threading.Event] = {}

# Per-worker active mini-task Claude processes
_mini_procs: dict[int, subprocess.Popen] = {}


def _run_mini_task(worker_id: int, cdp_port: int, instructions: str) -> subprocess.Popen:
    """Spawn a mini Claude Code session to execute user instructions in Chrome.

    The mini Claude has Playwright MCP access to the worker's Chrome window.
    It should complete the task and output TASK:COMPLETE when done.

    Args:
        worker_id: Worker whose Chrome window to use.
        cdp_port: CDP debug port for the worker's Chrome.
        instructions: What the user wants Claude to do.

    Returns:
        Running subprocess.Popen handle (stdout is readable).
    """
    prompt = (
        f"You have browser access via Playwright MCP (CDP port {cdp_port}).\n"
        f"The user needs you to do the following task:\n\n"
        f"{instructions}\n\n"
        f"Use the browser tools to complete this task. When finished, output TASK:COMPLETE.\n"
        f"Do NOT submit any job applications — only do what the user explicitly asked."
    )

    mcp_config_path = config.APP_DIR / f".mcp-apply-{worker_id}.json"

    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)

    proc = subprocess.Popen(
        [
            "claude",
            "--model", "sonnet",
            "-p",
            "--mcp-config", str(mcp_config_path),
            "--strict-mcp-config",
            "--permission-mode", "bypassPermissions",
            "--no-session-persistence",
            "--output-format", "stream-json",
            "--verbose", "-",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(config.APP_DIR),
        start_new_session=True,
    )
    proc.stdin.write(prompt)
    proc.stdin.close()
    return proc


# ---------------------------------------------------------------------------
# Always-on per-worker HTTP listener
# ---------------------------------------------------------------------------

class _ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    """HTTPServer that handles each connection in a new daemon thread."""
    daemon_threads = True


def _start_worker_listener(worker_id: int, no_hitl: bool = False) -> int:
    """Start the always-on HTTP server for a worker.

    This server starts once at the beginning of worker_loop() and lives for
    the entire worker lifetime. It exposes the Take Over, Run Task, Handback,
    and Done endpoints used by the Chrome extension popup.

    Endpoints:
        GET  /api/status         — current worker state (for extension polling)
        POST /api/takeover       — user takes over; kills current Claude proc
        POST /api/run-task       — spawn mini Claude for a user instruction
        GET  /api/task-stream    — SSE stream of mini Claude output
        POST /api/handback       — resume main agent (optionally with context)
        POST /api/done/{hash}    — HITL "done" signal (banner button)
        POST /api/action-log/{hash} — extension posts {events, snapshots} for resume prompt

    Args:
        worker_id: Numeric worker identifier.
        no_hitl: --no-hitl flag value, surfaced in /api/status as `noHitl`
            so the popup can show a "park-and-move-on" indicator.

    Returns:
        Port the server is listening on.
    """
    port = HITL_LISTEN_BASE_PORT + worker_id
    cdp_port = BASE_CDP_PORT + worker_id

    # Per-worker mutable state (closed over by handler)
    state: dict = {
        "job": None,
        "status": "idle",
        "reason": None,
        "instructions": None,
        "hitl_event": None,
        "hitl_job_hash": None,
        "hitl_watcher_proc": None,
        "handback_instructions": None,
        "mini_proc": None,
        "saved_instruction": None,
        "chrome_pid": None,
        "last_focused": 0,
        "history": [],  # list of completed job summaries for the homepage log
        "no_hitl": no_hitl,
    }

    takeover_event = threading.Event()
    handback_event = threading.Event()

    with _worker_state_lock:
        _worker_state[worker_id] = state
    _takeover_events[worker_id] = takeover_event
    _handback_events[worker_id] = handback_event

    class _Handler(BaseHTTPRequestHandler):
        def do_OPTIONS(self):
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self):
            if self.path in ("/", ""):
                self._handle_homepage()
            elif self.path == "/api/status":
                self._handle_status()
            elif self.path == "/api/log":
                self._handle_log()
            elif self.path == "/api/task-stream":
                self._handle_task_stream()
            elif self.path == "/api/focus":
                self._handle_focus()
            elif self.path.startswith("/api/jobs"):
                self._handle_jobs_list()
            elif self.path == "/api/integrations":
                self._handle_integrations()
            elif self.path.startswith("/api/qa"):
                self._handle_qa_list()
            elif self.path.startswith("/api/prefs/"):
                self._handle_prefs_get()
            elif self.path == "/api/accounts":
                self._handle_accounts_list()
            else:
                self.send_response(404)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()

        def do_POST(self):
            # Always close after POST response.  If we leave the connection
            # open (HTTP/1.1 keep-alive) and the handler doesn't consume the
            # request body, Python's HTTP server will try to parse the leftover
            # bytes as the next request, causing a deadlock where the server
            # waits for a valid request line and the client waits for a response.
            self.close_connection = True
            if self.path == "/api/takeover":
                self._handle_takeover()
            elif self.path == "/api/run-task":
                self._handle_run_task()
            elif self.path == "/api/handback":
                self._handle_handback()
            elif self.path.startswith("/api/done"):
                self._handle_done()
            elif self.path.startswith("/api/action-log/"):
                self._handle_action_log()
            elif self.path == "/api/add-job":
                self._handle_add_job()
            elif self.path == "/api/jobs/mark":
                self._handle_jobs_mark()
            elif self.path == "/api/integrations/gmail/reauth":
                self._handle_gmail_reauth()
            elif self.path == "/api/no-hitl":
                self._handle_no_hitl_toggle()
            elif self.path == "/api/qa":
                self._handle_qa_create()
            elif self.path.startswith("/api/qa/"):
                self._handle_qa_mutate()
            elif self.path.startswith("/api/prefs/"):
                self._handle_prefs_save()
            elif self.path.startswith("/api/accounts/"):
                self._handle_account_mutate()
            elif self.path.startswith("/api/sessions/"):
                self._handle_session_mutate()
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

        def _json_ok(self, data: dict) -> None:
            body = json.dumps(data).encode()
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

        def _handle_homepage(self):
            import html as _html
            job = state.get("job") or {}
            status = state.get("status", "idle")
            title = job.get("title", "") or "No active job"
            company = job.get("company", "")
            site = job.get("site", "")
            score = job.get("fit_score", 0)
            status_color = {
                "applying": "#22c55e",
                "waiting_human": "#a855f7",
                "idle": "#6b7280",
            }.get(status, "#eab308")
            meta_parts = [p for p in [company, site] if p]
            if score:
                meta_parts.append(f"Score {score}/10")
            meta_line = " · ".join(meta_parts)
            instructions = state.get("instructions", "")
            instructions_block = ""
            if instructions:
                instructions_block = (
                    f'<div class="instructions">'
                    f'<strong>Instructions:</strong><br>'
                    f'{_html.escape(instructions).replace(chr(10), "<br>")}'
                    f'</div>'
                )

            # Build activity log rows
            history = list(reversed(state.get("history", [])))
            outcome_colors = {
                "applied":       ("#22c55e", "✓ Applied"),
                "already_applied": ("#6366f1", "↩ Already applied"),
                "expired":       ("#6b7280", "⌛ Expired"),
                "needs_human":   ("#a855f7", "⚑ Needs human"),
                "failed":        ("#ef4444", "✗ Failed"),
            }
            log_rows = ""
            for h in history:
                oc = h.get("outcome", "failed")
                color, label = outcome_colors.get(oc, ("#6b7280", oc))
                ts_str = datetime.fromtimestamp(h["ts"]).strftime("%H:%M:%S") if h.get("ts") else "–"
                job_title = _html.escape(h.get("title", "–")[:60])
                job_co = _html.escape(h.get("company", "")[:30])
                sc = h.get("score", 0)
                dur = h.get("duration_s", 0)
                url = _html.escape(h.get("url", "#"))
                log_rows += (
                    f'<tr>'
                    f'<td style="color:#64748b;font-size:11px">{ts_str}</td>'
                    f'<td><span style="color:{color};font-weight:600;font-size:11px">{label}</span></td>'
                    f'<td style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'
                    f'<a href="{url}" target="_blank" style="color:#e2e8f0;text-decoration:none">{job_title}</a>'
                    f'<br><span style="font-size:10px;color:#64748b">{job_co}</span></td>'
                    f'<td style="color:#60a5fa;font-size:11px;text-align:center">{sc}/10</td>'
                    f'<td style="color:#64748b;font-size:11px;text-align:right">{dur}s</td>'
                    f'</tr>'
                )
            log_section = ""
            if log_rows:
                log_section = f"""
<div class="log-panel">
  <div class="log-title">Session Activity</div>
  <table class="log-table">
    <thead><tr>
      <th>Time</th><th>Result</th><th>Job</th><th>Score</th><th>Time</th>
    </tr></thead>
    <tbody>{log_rows}</tbody>
  </table>
</div>"""

            body = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>ApplyPilot W{worker_id}</title>
<style>
  * {{box-sizing:border-box;margin:0;padding:0}}
  body {{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;
        min-height:100vh;padding:24px;display:flex;flex-direction:column;
        align-items:center;gap:16px}}
  .badge {{background:#1e293b;border:1px solid #334155;border-radius:12px;
           padding:20px 28px;width:100%;max-width:600px;text-align:center}}
  .wid {{font-size:40px;font-weight:700;color:#eab308}}
  .status {{display:inline-block;padding:4px 12px;border-radius:9999px;
            font-size:12px;font-weight:600;margin:8px 0;
            background:{status_color}22;color:{status_color};
            border:1px solid {status_color}44}}
  .title {{font-size:18px;font-weight:600;margin:6px 0}}
  .meta {{font-size:12px;color:#94a3b8}}
  .instructions {{margin-top:12px;padding:10px 12px;background:#0f172a;
                  border-left:3px solid #a855f7;border-radius:4px;
                  font-size:12px;text-align:left;line-height:1.5}}
  .log-panel {{width:100%;max-width:600px;background:#1e293b;
               border:1px solid #334155;border-radius:12px;overflow:hidden}}
  .log-title {{padding:12px 16px;font-size:12px;font-weight:700;
               text-transform:uppercase;letter-spacing:.5px;color:#64748b;
               border-bottom:1px solid #334155}}
  .log-table {{width:100%;border-collapse:collapse;font-size:12px}}
  .log-table th {{padding:6px 10px;text-align:left;font-size:10px;
                  text-transform:uppercase;color:#475569;
                  border-bottom:1px solid #1e293b}}
  .log-table td {{padding:7px 10px;border-bottom:1px solid #0f172a;vertical-align:middle}}
  .log-table tr:last-child td {{border-bottom:none}}
  .hint {{font-size:10px;color:#334155;margin-top:4px}}
</style>
</head>
<body>
<div class="badge">
  <div class="wid">W{worker_id}</div>
  <div class="status">{status.upper().replace("_", " ")}</div>
  <div class="title">{_html.escape(title)}</div>
  {'<div class="meta">' + meta_line + '</div>' if meta_line else ''}
  {instructions_block}
</div>
{log_section}
<div class="hint">ApplyPilot Worker {worker_id} &nbsp;·&nbsp; <span id="ts"></span></div>
<script>
  document.getElementById('ts').textContent = new Date().toLocaleTimeString();
  setTimeout(() => location.reload(), 5000);
</script>
</body>
</html>""".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _handle_status(self):
            job = state.get("job") or {}
            site = job.get("site", "")
            reason = state.get("reason", "")
            # Use cached saved_instruction — never hit DB on status polls.
            # (DB access from per-request daemon threads leaks SQLite fds over time.)
            self._json_ok({
                "workerId": worker_id,
                "status": state.get("status", "idle"),
                "jobTitle": job.get("title", ""),
                "jobSite": site,
                "jobCompany": job.get("company", ""),
                "score": job.get("fit_score", 0),
                "reason": reason,
                "instructions": state.get("instructions"),
                "savedInstruction": state.get("saved_instruction"),
                "chromePid": state.get("chrome_pid"),
                "lastFocused": state.get("last_focused", 0),
                "noHitl": bool(state.get("no_hitl", False)),
            })

        def _handle_log(self):
            self._json_ok({"history": list(reversed(state.get("history", [])))})

        def _handle_focus(self):
            state["last_focused"] = time.time()
            try:
                from applypilot.apply.chrome import bring_to_foreground_cdp, bring_to_foreground_pid
                # CDP bringToFront focuses the tab within Chrome.
                # bring_to_foreground_pid raises the OS window (X11/Wayland).
                # Both are needed: CDP alone doesn't always raise the window.
                bring_to_foreground_cdp(cdp_port)
                bring_to_foreground_pid(state.get("chrome_pid"))
            except Exception:
                pass
            self._text_ok()

        def _handle_takeover(self):
            takeover_event.set()
            # Kill the active Claude proc
            with _claude_lock:
                cproc = _claude_procs.get(worker_id)
            if cproc and cproc.poll() is None:
                _kill_process_tree(cproc.pid)
            state["status"] = "paused_by_user"
            self._text_ok()

        def _handle_run_task(self):
            body = self._read_body()
            instructions = body.get("instructions", "").strip()
            if not instructions:
                self.send_response(400)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b"instructions required")
                return
            # Kill any previous mini proc
            old = state.get("mini_proc")
            if old and old.poll() is None:
                _kill_process_tree(old.pid)
            proc = _run_mini_task(worker_id, cdp_port, instructions)
            state["mini_proc"] = proc
            _mini_procs[worker_id] = proc
            task_id = str(int(time.time()))
            self._json_ok({"taskId": task_id})

        def _handle_task_stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            proc = state.get("mini_proc")
            if not proc:
                try:
                    self.wfile.write(b"data: No task running\n\n")
                    self.wfile.flush()
                except Exception:
                    pass
                return
            try:
                for line in proc.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    # Extract text from stream-json format
                    try:
                        msg = json.loads(line)
                        if msg.get("type") == "assistant":
                            for block in msg.get("message", {}).get("content", []):
                                if block.get("type") == "text":
                                    text = block["text"].replace("\n", "\\n")
                                    self.wfile.write(f"data: {text}\n\n".encode())
                                    self.wfile.flush()
                    except json.JSONDecodeError:
                        safe = line.replace("\n", "\\n")
                        self.wfile.write(f"data: {safe}\n\n".encode())
                        self.wfile.flush()
                proc.wait()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _handle_handback(self):
            body = self._read_body()
            instructions = body.get("instructions", "").strip()
            save = body.get("save", False)
            if save and instructions:
                job = state.get("job") or {}
                site = job.get("site", "unknown")
                reason = state.get("reason") or "takeover"
                try:
                    from applypilot.database import store_qa
                    store_qa(
                        question=f"HITL:{site}:{reason}",
                        answer=instructions,
                        source="human",
                        field_type="hitl_instruction",
                    )
                    state["saved_instruction"] = instructions
                except Exception as e:
                    logger.debug("Failed to save HITL instruction to Q&A KB: %s", e)
            state["handback_instructions"] = instructions or None
            state["status"] = "applying"
            # Clear takeover so next job doesn't see it
            takeover_event.clear()
            # Unblock worker_loop
            handback_event.set()
            self._text_ok()

        def _handle_done(self):
            body = self._read_body()
            custom_instructions = (body.get("instructions") or "").strip()
            if custom_instructions:
                state["handback_instructions"] = custom_instructions
            hitl_evt = state.get("hitl_event")
            if hitl_evt:
                # Mark as resuming immediately so the extension shows loading state
                # before the worker loop picks it up and changes status to "applying".
                state["status"] = "resuming"
                hitl_evt.set()
            self._text_ok()

        def _handle_action_log(self):
            """Stash a content-script-posted action log keyed by job hash.

            Hash comes from the URL: /api/action-log/{hash}. Body is JSON:
              {"events": [...], "snapshots": {tab_url: {field: value, ...}}}
            _run_hitl pops the cache entry after the user-Done signal so the
            log can be threaded into the resume prompt.
            """
            hash_ = self.path.rsplit("/", 1)[-1].split("?", 1)[0].strip()
            if not hash_:
                self.send_response(400)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                return
            payload = self._read_body()
            with _action_log_cache_lock:
                _action_log_cache[hash_] = payload
            self._text_ok()

        def _handle_jobs_list(self):
            """Return actionable jobs for the extension Jobs tab."""
            from urllib.parse import parse_qs, urlparse as _up
            qs = parse_qs(_up(self.path).query)
            limit = min(int(qs.get("limit", ["50"])[0]), 200)
            try:
                from applypilot.database import get_connection
                conn = get_connection()
                rows = conn.execute("""
                    SELECT url, title, company, site, fit_score,
                           apply_status, apply_category, apply_error,
                           tailored_resume_path, cover_letter_path
                    FROM jobs
                    WHERE fit_score IS NOT NULL AND fit_score >= 6
                      AND (apply_status IS NULL
                           OR apply_status NOT IN ('applied', 'manual', 'in_progress'))
                      AND (eligibility IS NULL OR eligibility = 'eligible')
                    ORDER BY fit_score DESC, discovered_at DESC
                    LIMIT ?
                """, (limit,)).fetchall()
                self._json_ok({"jobs": [dict(r) for r in rows]})
            except Exception as e:
                logger.debug("jobs_list error: %s", e)
                self.send_response(500)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(str(e).encode())

        def _handle_add_job(self):
            """Add a job URL to the discovery queue from the extension."""
            from applypilot.database import get_connection
            body = self._read_body()
            url = (body.get("url") or "").strip()
            title = (body.get("title") or "").strip()
            if not url or not url.startswith("http"):
                self.send_response(400)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b"url required")
                return
            try:
                from urllib.parse import urlparse
                site = urlparse(url).netloc.replace("www.", "")
                conn = get_connection()
                existing = conn.execute(
                    "SELECT url, apply_status FROM jobs WHERE url=?", (url,)
                ).fetchone()
                if existing:
                    self._json_ok({"status": "exists",
                                   "applyStatus": existing["apply_status"]})
                    return
                conn.execute(
                    "INSERT INTO jobs (url, title, site, discovered_at) "
                    "VALUES (?, ?, ?, datetime('now'))",
                    (url, title or "Unknown Position", site),
                )
                commit_with_retry(conn)
                logger.info("[W%d] Added job via extension: %s", worker_id, url[:80])
                self._json_ok({"status": "queued"})
            except Exception as e:
                logger.debug("add_job error: %s", e)
                self.send_response(500)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(str(e).encode())

        def _handle_jobs_mark(self):
            """Manually mark a job's apply status from the extension Jobs tab."""
            from applypilot.database import get_connection
            from datetime import datetime, timezone as tz
            body = self._read_body()
            url    = (body.get("url") or "").strip()
            action = (body.get("action") or "").strip()
            valid  = ("applied", "skip", "error", "reset")
            if not url or action not in valid:
                self.send_response(400)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b"url and action required")
                return
            try:
                
                conn = get_connection()
                now = datetime.now(tz.utc).isoformat()
                if action == "applied":
                    conn.execute("""UPDATE jobs SET apply_status='applied', applied_at=?,
                        apply_category='applied', apply_attempts=COALESCE(apply_attempts,0)+1
                        WHERE url=?""", (now, url))
                    transition_state(conn, url, "applied",
                        reason="HTTP handler mark", force=True)
                elif action == "skip":
                    conn.execute("""UPDATE jobs SET apply_status='failed',
                        apply_category='archived_ineligible',
                        apply_error='manually skipped', apply_attempts=99 WHERE url=?""", (url,))
                    transition_state(conn, url, "manual_only",
                        reason="HTTP handler skip", force=True)
                elif action == "error":
                    conn.execute("""UPDATE jobs SET apply_status='failed',
                        apply_category='archived_platform',
                        apply_error='manually marked error', apply_attempts=99 WHERE url=?""", (url,))
                    transition_state(conn, url, "apply_failed",
                        reason="HTTP handler error", force=True)
                elif action == "reset":
                    conn.execute("""UPDATE jobs SET apply_status=NULL, apply_category='pending',
                        apply_error=NULL, apply_attempts=0, agent_id=NULL WHERE url=?""", (url,))
                    transition_state(conn, url, "ready_to_apply",
                        reason="HTTP handler reset", force=True)
                commit_with_retry(conn)
                logger.info("[W%d] Manual mark '%s': %s", worker_id, action, url[:70])
                self._json_ok({"status": "ok", "action": action})
            except Exception as e:
                logger.error("HTTP _handle_jobs_mark failed for %s: %s", url, e, exc_info=True)
                self.send_response(500)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(str(e).encode())

        def _handle_accounts_list(self):
            """List all rows in the accounts table for the credentials editor.

            Returns full rows including passwords — the page is on
            chrome-extension://{id} which is already a trusted origin for
            this user. The UI hides passwords by default with a
            click-to-reveal toggle.
            """
            try:
                from applypilot.database import get_connection
                conn = get_connection()
                rows = conn.execute(
                    "SELECT id, site, domain, email, password, "
                    "       created_at, job_url, notes "
                    "FROM accounts ORDER BY created_at DESC"
                ).fetchall()
                self._json_ok({"rows": [dict(r) for r in rows]})
            except Exception as e:
                logger.debug("accounts_list error: %s", e)
                self.send_response(500)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(str(e).encode())

        def _handle_account_mutate(self):
            """Update or delete an account row by ID."""
            try:
                row_id = int(self.path.rsplit("/", 1)[-1])
            except ValueError:
                self.send_response(400)
                self.end_headers()
                return
            body = self._read_body()
            action = (body.get("action") or "").strip()
            try:
                from applypilot.database import get_connection
                conn = get_connection()
                if action == "delete":
                    conn.execute("DELETE FROM accounts WHERE id = ?", (row_id,))
                    commit_with_retry(conn)
                    self._json_ok({"status": "deleted", "id": row_id})
                    return
                if action == "update":
                    fields = []
                    params: list = []
                    for col in ("site", "domain", "email", "password", "notes"):
                        if col in body:
                            fields.append(f"{col} = ?")
                            params.append(body[col] or None)
                    if not fields:
                        self.send_response(400)
                        self.end_headers()
                        return
                    params.append(row_id)
                    conn.execute(
                        f"UPDATE accounts SET {', '.join(fields)} WHERE id = ?",
                        params,
                    )
                    commit_with_retry(conn)
                    self._json_ok({"status": "updated", "id": row_id})
                    return
                self.send_response(400)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b"action must be 'update' or 'delete'")
            except Exception as e:
                logger.debug("account_mutate error: %s", e)
                self.send_response(500)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(str(e).encode())

        def _handle_session_mutate(self):
            """Clear a saved ATS session: /api/sessions/{slug}.

            Body: {action: "clear"}. Same backing helper as the
            `applypilot apply --clear-session` CLI flag.
            """
            slug = self.path.rsplit("/", 1)[-1]
            body = self._read_body()
            action = (body.get("action") or "").strip()
            if action != "clear":
                self.send_response(400)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b"action must be 'clear'")
                return
            try:
                from applypilot.apply.chrome import clear_ats_session
                ok = clear_ats_session(slug)
                self._json_ok({"status": "cleared" if ok else "not_found",
                               "slug": slug})
            except Exception as e:
                logger.debug("session_mutate error: %s", e)
                self.send_response(500)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(str(e).encode())

        # --- Preferences (file editor) ---
        # Maps URL key → (path, format). Format determines how we validate
        # before saving — broken YAML/JSON would brick the next pipeline run.
        _PREFS_FILES = {
            "profile":         (config.PROFILE_PATH,                                 "json"),
            "searches":        (config.SEARCH_CONFIG_PATH,                           "yaml"),
            "company-limits":  (config.APP_DIR / config.COMPANY_LIMITS_PATH_NAME,    "yaml"),
        }

        def _handle_prefs_get(self):
            """Read a known prefs file and return raw text + format."""
            key = self.path.rsplit("/", 1)[-1]
            entry = self._PREFS_FILES.get(key)
            if not entry:
                self.send_response(404)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                return
            path, fmt = entry
            try:
                text = path.read_text(encoding="utf-8") if path.exists() else ""
                self._json_ok({
                    "key": key,
                    "path": str(path),
                    "format": fmt,
                    "text": text,
                    "exists": path.exists(),
                })
            except Exception as e:
                logger.debug("prefs_get error: %s", e)
                self.send_response(500)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(str(e).encode())

        def _handle_prefs_save(self):
            """Validate + save a prefs file. Backs up the previous version."""
            key = self.path.rsplit("/", 1)[-1]
            entry = self._PREFS_FILES.get(key)
            if not entry:
                self.send_response(404)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                return
            path, fmt = entry
            body = self._read_body()
            text = body.get("text")
            if not isinstance(text, str):
                self.send_response(400)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b"text required")
                return

            # Validate before writing — bad JSON/YAML would crash the pipeline.
            try:
                if fmt == "json":
                    json.loads(text)
                elif fmt == "yaml":
                    import yaml
                    yaml.safe_load(text)
            except Exception as e:
                self.send_response(400)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(f"parse error: {e}".encode())
                return

            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                # Snapshot the current file before overwriting (single-slot
                # backup; previous backup gets overwritten).
                if path.exists():
                    backup = path.with_suffix(path.suffix + ".bak")
                    backup.write_bytes(path.read_bytes())
                path.write_text(text, encoding="utf-8")

                # Bust the company-limits cache so the change applies on
                # the next acquire_job() without a pipeline restart.
                if key == "company-limits":
                    try:
                        config._company_limits_cache = None
                    except Exception:
                        pass
                self._json_ok({"status": "saved", "path": str(path),
                               "bytes": len(text.encode())})
            except Exception as e:
                logger.warning("prefs_save error: %s", e)
                self.send_response(500)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(str(e).encode())

        def _handle_qa_list(self):
            """List/search qa_knowledge rows for the options-page editor.

            Query params: q (free text), ats, source, outcome, limit, offset.
            """
            from urllib.parse import parse_qs, urlparse as _up
            qs = parse_qs(_up(self.path).query)
            q       = (qs.get("q",       [""])[0] or "").strip()
            ats     = (qs.get("ats",     [""])[0] or "").strip()
            source  = (qs.get("source",  [""])[0] or "").strip()
            outcome = (qs.get("outcome", [""])[0] or "").strip()
            limit   = max(1, min(int(qs.get("limit",  ["200"])[0]), 500))
            offset  = max(0, int(qs.get("offset", ["0"])[0]))
            try:
                from applypilot.database import get_connection
                conn = get_connection()
                where = ["1=1"]
                params: list = []
                if q:
                    where.append("(LOWER(question_text) LIKE ? OR LOWER(answer_text) LIKE ?)")
                    needle = f"%{q.lower()}%"
                    params += [needle, needle]
                if ats:
                    where.append("ats_slug = ?")
                    params.append(ats)
                if source:
                    where.append("answer_source = ?")
                    params.append(source)
                if outcome:
                    where.append("outcome = ?")
                    params.append(outcome)
                where_sql = " AND ".join(where)
                total = conn.execute(
                    f"SELECT COUNT(*) FROM qa_knowledge WHERE {where_sql}",
                    params,
                ).fetchone()[0]
                rows = conn.execute(
                    f"SELECT id, question_text, answer_text, answer_source, "
                    f"field_type, ats_slug, options_json, outcome, "
                    f"created_at, updated_at "
                    f"FROM qa_knowledge WHERE {where_sql} "
                    f"ORDER BY updated_at DESC NULLS LAST, created_at DESC "
                    f"LIMIT ? OFFSET ?",
                    [*params, limit, offset],
                ).fetchall()
                self._json_ok({"rows": [dict(r) for r in rows], "total": total})
            except Exception as e:
                logger.debug("qa_list error: %s", e)
                self.send_response(500)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(str(e).encode())

        def _handle_qa_create(self):
            """Insert a new qa_knowledge row from the options-page editor."""
            body = self._read_body()
            question = (body.get("question") or "").strip()
            answer   = (body.get("answer")   or "").strip()
            if not question or not answer:
                self.send_response(400)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b"question and answer required")
                return
            source     = (body.get("source")     or "human").strip() or "human"
            field_type = (body.get("field_type") or "").strip() or None
            ats_slug   = (body.get("ats_slug")   or "").strip() or None
            try:
                from applypilot.database import store_qa
                row_id = store_qa(question, answer, source=source,
                                  field_type=field_type, ats_slug=ats_slug)
                self._json_ok({"status": "created", "id": row_id})
            except Exception as e:
                logger.debug("qa_create error: %s", e)
                self.send_response(500)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(str(e).encode())

        def _handle_qa_mutate(self):
            """Update or delete a qa_knowledge row by ID.

            Path: /api/qa/{id}. Body action: "update" | "delete".
            Update fields: question_text, answer_text, answer_source,
            field_type, options_json, ats_slug, outcome.
            """
            from datetime import datetime as _dt, timezone as _tz
            from applypilot.database import get_connection, question_key
            try:
                row_id = int(self.path.rsplit("/", 1)[-1])
            except ValueError:
                self.send_response(400)
                self.end_headers()
                return
            body = self._read_body()
            action = (body.get("action") or "").strip()
            try:
                conn = get_connection()
                if action == "delete":
                    conn.execute("DELETE FROM qa_knowledge WHERE id = ?", (row_id,))
                    commit_with_retry(conn)
                    self._json_ok({"status": "deleted", "id": row_id})
                    return
                if action == "update":
                    fields = []
                    params: list = []
                    for col in (
                        "question_text", "answer_text", "answer_source",
                        "field_type", "options_json", "ats_slug", "outcome",
                    ):
                        if col in body:
                            fields.append(f"{col} = ?")
                            params.append(body[col] or None)
                            # Recompute question_key whenever question_text changes
                            if col == "question_text" and body[col]:
                                fields.append("question_key = ?")
                                params.append(question_key(body[col]))
                    if not fields:
                        self.send_response(400)
                        self.end_headers()
                        return
                    fields.append("updated_at = ?")
                    params.append(_dt.now(_tz.utc).isoformat())
                    params.append(row_id)
                    conn.execute(
                        f"UPDATE qa_knowledge SET {', '.join(fields)} WHERE id = ?",
                        params,
                    )
                    commit_with_retry(conn)
                    self._json_ok({"status": "updated", "id": row_id})
                    return
                self.send_response(400)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b"action must be 'update' or 'delete'")
            except Exception as e:
                logger.debug("qa_mutate error: %s", e)
                self.send_response(500)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(str(e).encode())

        def _handle_integrations(self):
            """Health check for backend integrations (Gmail, ATS sessions).

            Powers the Integrations section of the options page.
            """
            try:
                from applypilot.apply.chrome import list_ats_sessions
                payload = {
                    "gmail": _gmail_status(),
                    "ats_sessions": list_ats_sessions(),
                }
                self._json_ok(payload)
            except Exception as e:
                logger.debug("integrations error: %s", e)
                self.send_response(500)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(str(e).encode())

        def _handle_gmail_reauth(self):
            """Spawn Gmail OAuth subprocess; user completes in browser."""
            self._json_ok(_trigger_gmail_reauth())

        def _handle_no_hitl_toggle(self):
            """Flip the per-worker no-hitl flag from the popup chip.

            Body: {enabled: bool}. Mutates worker_state["no_hitl"];
            _run_hitl reads this live each pause, so the change takes
            effect immediately for the next HITL trigger.
            """
            body = self._read_body()
            enabled = bool(body.get("enabled"))
            with _worker_state_lock:
                ws = _worker_state.get(worker_id)
                if ws is not None:
                    ws["no_hitl"] = enabled
            self._json_ok({"workerId": worker_id, "noHitl": enabled})

        def log_message(self, format, *args):
            pass  # Suppress HTTP logging

    # Retry binding to the preferred port for up to 5s (old process may be dying).
    # Fall back to a random port only as a last resort, with a warning.
    preferred_port = port
    server = None
    for _attempt in range(6):
        try:
            server = _ThreadedHTTPServer(("127.0.0.1", port), _Handler)
            break
        except OSError:
            if _attempt < 5:
                time.sleep(1)
            else:
                server = _ThreadedHTTPServer(("127.0.0.1", 0), _Handler)
                port = server.server_address[1]
                logger.warning(
                    "Worker %d: preferred port %d was busy; using random port %d "
                    "(extension will not connect — restart the pipeline to fix)",
                    worker_id, preferred_port, port,
                )

    with _worker_server_lock:
        _worker_servers[worker_id] = server

    thread = threading.Thread(target=server.serve_forever, daemon=True,
                              name=f"worker-http-w{worker_id}")
    thread.start()
    logger.debug("Worker listener for worker %d on port %d", worker_id, port)
    return port


def _stop_worker_listener(worker_id: int) -> None:
    """Shut down a worker's always-on HTTP server."""
    with _worker_server_lock:
        server = _worker_servers.pop(worker_id, None)
    if server:
        server.shutdown()
    with _worker_state_lock:
        _worker_state.pop(worker_id, None)
    _takeover_events.pop(worker_id, None)
    _handback_events.pop(worker_id, None)
    _mini_procs.pop(worker_id, None)


# ---------------------------------------------------------------------------
# Gmail token refresh
# ---------------------------------------------------------------------------

_gmail_token_lock = threading.Lock()


def _gmail_status() -> dict:
    """Report Gmail MCP token freshness for the Integrations panel.

    Returns a dict with keys:
      configured       — bool (False if creds file missing)
      status           — "missing" | "expired" | "expiring_soon" | "valid" | "error"
      expires_at_ms    — int (ms since epoch) when present
      expires_in_seconds — int seconds remaining (negative if expired)
      scopes           — string of OAuth scopes granted
    """
    creds_path = Path.home() / ".gmail-mcp" / "credentials.json"
    keys_path = Path.home() / ".gmail-mcp" / "gcp-oauth.keys.json"
    if not creds_path.exists() or not keys_path.exists():
        return {"configured": False, "status": "missing"}
    try:
        creds = json.loads(creds_path.read_text())
        expiry_ms = int(creds.get("expiry_date", 0))
        now_ms = int(time.time() * 1000)
        delta_s = (expiry_ms - now_ms) // 1000
        if delta_s < 0:
            status = "expired"
        elif delta_s < 300:
            status = "expiring_soon"
        else:
            status = "valid"
        return {
            "configured": True,
            "status": status,
            "expires_at_ms": expiry_ms,
            "expires_in_seconds": delta_s,
            "scopes": creds.get("scope") or "",
        }
    except Exception as e:
        return {"configured": True, "status": "error", "error": str(e)[:200]}


def _trigger_gmail_reauth() -> dict:
    """Spawn the Gmail MCP auth subprocess (detached). Returns immediately.

    The subprocess opens the user's browser to Google's OAuth consent
    screen, listens on a local port for the callback, exchanges the code,
    and writes ~/.gmail-mcp/credentials.json. The Integrations panel can
    poll _gmail_status() until status flips to 'valid'.
    """
    try:
        proc = subprocess.Popen(
            ["npx", "-y", "@gongrzhe/server-gmail-autoauth-mcp@1.1.11", "auth"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        logger.info("Triggered Gmail re-auth (pid %d)", proc.pid)
        return {"status": "started", "pid": proc.pid}
    except FileNotFoundError:
        return {"status": "error", "error": "npx not found in PATH"}
    except Exception as e:
        logger.warning("Gmail re-auth subprocess failed: %s", e)
        return {"status": "error", "error": str(e)[:200]}


def _refresh_gmail_token() -> bool:
    """Ensure the Gmail OAuth access token is fresh.

    The @gongrzhe/server-gmail-autoauth-mcp MCP server does NOT refresh
    tokens on its own — it reads the access_token from credentials.json
    and uses it directly.  Access tokens expire after 1 hour, so we
    must refresh proactively before each apply run.

    Returns True if token is valid/refreshed, False if Gmail is unavailable.
    """
    creds_path = Path.home() / ".gmail-mcp" / "credentials.json"
    keys_path = Path.home() / ".gmail-mcp" / "gcp-oauth.keys.json"

    if not creds_path.exists() or not keys_path.exists():
        logger.debug("Gmail MCP credentials not found, skipping refresh")
        return False

    with _gmail_token_lock:
        try:
            creds = json.loads(creds_path.read_text())
            keys_data = json.loads(keys_path.read_text())
            key_info = keys_data.get("installed") or keys_data.get("web", {})

            # Check if token expires within next 5 minutes
            expiry_ms = creds.get("expiry_date", 0)
            now_ms = time.time() * 1000
            if expiry_ms - now_ms > 300_000:  # > 5 min remaining
                return True

            logger.info("Gmail token expiring soon, refreshing...")
            import urllib.request
            import urllib.parse

            data = urllib.parse.urlencode({
                "client_id": key_info["client_id"],
                "client_secret": key_info["client_secret"],
                "refresh_token": creds["refresh_token"],
                "grant_type": "refresh_token",
            }).encode()
            req = urllib.request.Request(
                "https://oauth2.googleapis.com/token", data=data,
            )
            resp = json.loads(urllib.request.urlopen(req, timeout=15).read())

            creds["access_token"] = resp["access_token"]
            creds["expiry_date"] = int((time.time() + resp["expires_in"]) * 1000)
            creds_path.write_text(json.dumps(creds, indent=2))
            logger.info("Gmail token refreshed, expires in %ds", resp["expires_in"])
            return True
        except Exception as e:
            logger.warning("Gmail token refresh failed: %s", e)
            return False


# ---------------------------------------------------------------------------
# MCP config
# ---------------------------------------------------------------------------

def _make_mcp_config(cdp_port: int, worker_id: int = 0) -> dict:
    """Build MCP config dict for a specific CDP port.

    Passes the real Chrome user-agent to Playwright MCP so it doesn't
    override our Chrome --user-agent flag with its default
    "HeadlessChrome" UA when connecting via CDP.

    The viewport is synced with the Chrome window size chosen for this
    worker (see chrome._pick_viewport / get_worker_viewport).
    """
    from applypilot.apply.chrome import _get_real_user_agent, get_worker_viewport

    vp = get_worker_viewport(worker_id)
    return {
        "mcpServers": {
            "playwright": {
                "command": "npx",
                "args": [
                    # Pinned (was @latest — every apply run re-resolved the tag,
                    # so a compromised release would be picked up within hours).
                    # Bump deliberately after a release has soaked ~2 weeks;
                    # check `npm view @playwright/mcp@<v> dist.attestations.url`.
                    "@playwright/mcp@0.0.75",
                    f"--cdp-endpoint=http://localhost:{cdp_port}",
                    f"--viewport-size={vp[0]}x{vp[1]}",
                    f"--user-agent={_get_real_user_agent()}",
                ],
            },
            "gmail": {
                "command": "npx",
                # Pinned: this package holds the Gmail OAuth tokens. 1.1.11
                # verified byte-identical to the registry tarball 2026-06-10.
                "args": ["-y", "@gongrzhe/server-gmail-autoauth-mcp@1.1.11"],
            },
        }
    }


# ---------------------------------------------------------------------------
# Database operations
# ---------------------------------------------------------------------------

def _db_retry_execute(conn: "sqlite3.Connection", sql: str,
                      params: tuple = (), timeout: float = 300.0) -> "sqlite3.Cursor":
    """Execute a SQL statement with retry on 'database is locked' errors.

    Concurrent tailor/cover/pdf pipeline stages can hold WAL write locks for
    minutes at a time.  Plain conn.execute() raises OperationalError immediately
    when that happens.  This helper retries with exponential backoff so callers
    don't crash just because another stage is mid-write.
    """
    deadline = time.monotonic() + timeout
    delay = 2.0
    while True:
        try:
            return conn.execute(sql, params)
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                raise
            logger.debug("DB locked, retrying in %.0fs…", delay)
            time.sleep(delay)
            delay = min(delay * 1.5, 30.0)


def _db_retry_commit(conn: "sqlite3.Connection", timeout: float = 300.0) -> None:
    """Commit with retry on 'database is locked' errors."""
    deadline = time.monotonic() + timeout
    delay = 2.0
    while True:
        try:
            commit_with_retry(conn)
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                raise
            logger.debug("DB locked on commit, retrying in %.0fs…", delay)
            time.sleep(delay)
            delay = min(delay * 1.5, 30.0)


def acquire_job(target_url: str | None = None,
                min_score: int | None = None,
                max_score: int | None = None,
                max_age_days: int | None = None,
                worker_id: int = 0) -> dict | None:
    """Atomically acquire the next job to apply to.

    Enforces:
      - Minimum fit score (config.DEFAULTS["min_score"] default)
      - Job age cutoff (config.DEFAULTS["max_job_age_days"] default)
      - Per-company open-pipeline cap (YAML-configurable)
      - Per-company concurrency: at most 1 active worker per company
      - Per-ATS concurrency: at most 1 active worker per ATS family
      - Manual-ATS skip list
    """
    from applypilot import config as _cfg
    from datetime import datetime, timedelta, timezone

    if min_score is None:
        min_score = _cfg.DEFAULTS["min_score"]
    if max_age_days is None:
        max_age_days = _cfg.DEFAULTS["max_job_age_days"]

    conn = get_connection()
    try:
        _begin_deadline = time.monotonic() + 300
        _begin_delay = 2.0
        while True:
            try:
                conn.execute("BEGIN IMMEDIATE")
                break
            except sqlite3.OperationalError as _be:
                if "locked" not in str(_be).lower():
                    raise
                if time.monotonic() >= _begin_deadline:
                    raise
                logger.debug("acquire_job: DB locked, retrying in %.0fs…", _begin_delay)
                time.sleep(_begin_delay)
                _begin_delay = min(_begin_delay * 1.5, 30.0)

        # Release stale in_progress locks from crashed runs (>30 min old).
        # P0.5 leak (c) from decision #31: also revert canonical state from
        # 'applying' back to 'ready_to_apply' for each affected job. Pull
        # affected URLs first so we can emit per-row transitions after the
        # bulk UPDATE.
        stale_urls = [
            r[0] for r in conn.execute("""
                SELECT url FROM jobs
                WHERE apply_status = 'in_progress'
                  AND last_attempted_at IS NOT NULL
                  AND last_attempted_at < datetime('now', '-30 minutes')
            """).fetchall()
        ]
        conn.execute("""
            UPDATE jobs SET apply_status = NULL, agent_id = NULL
            WHERE apply_status = 'in_progress'
              AND last_attempted_at IS NOT NULL
              AND last_attempted_at < datetime('now', '-30 minutes')
        """)
        for stale_url in stale_urls:
            try:
                transition_state(
                    conn, stale_url, "ready_to_apply",
                    reason="stale-lock release (acquire_job)",
                    force=True,
                )
            except Exception:
                logger.debug("stale-lock transition failed for %s", stale_url[:60], exc_info=True)

        if target_url:
            like = f"%{target_url.split('?')[0].rstrip('/')}%"
            row = conn.execute("""
                SELECT url, title, site, application_url, tailored_resume_path,
                       fit_score, location, full_description, cover_letter_path, company
                FROM jobs
                WHERE (url = ? OR application_url = ? OR application_url LIKE ? OR url LIKE ?)
                  AND tailored_resume_path IS NOT NULL
                  AND (apply_status IS NULL OR apply_status != 'in_progress')
                ORDER BY
                    CASE WHEN url = ? OR application_url = ? THEN 0 ELSE 1 END
                LIMIT 1
            """, (target_url, target_url, like, like,
                  target_url, target_url)).fetchone()
        else:
            blocked_sites, blocked_patterns = _load_blocked()
            site_filter = " AND ".join(f"site != '{s}'" for s in blocked_sites) if blocked_sites else "1=1"
            url_filter = " AND ".join(f"url NOT LIKE '{p}'" for p in blocked_patterns) if blocked_patterns else "1=1"
            max_score_filter = f"AND j.fit_score <= {max_score}" if max_score is not None else ""

            # Per-worker concurrency: don't let two workers run the same company or ATS
            in_progress_rows = conn.execute(
                "SELECT company, application_url FROM jobs WHERE apply_status = 'in_progress'"
            ).fetchall()
            active_companies: set[str] = set()
            active_ats: set[str] = set()
            for ip in in_progress_rows:
                if ip["company"]:
                    active_companies.add(ip["company"].lower())
                ats = detect_ats(ip["application_url"] or "")
                if ats:
                    active_ats.add(ats)

            if active_companies:
                ph = ",".join("?" * len(active_companies))
                company_excl = f"AND LOWER(COALESCE(j.company, '')) NOT IN ({ph})"
                company_excl_params: list = list(active_companies)
            else:
                company_excl = ""
                company_excl_params = []

            # Age filter
            age_filter = ""
            age_params: list = []
            if max_age_days and max_age_days > 0:
                age_filter = "AND j.discovered_at > datetime('now', ?)"
                age_params = [f"-{max_age_days} days"]

            # Fetch candidates. No more soft-sort deprioritization — hard cap
            # is enforced in Python below via get_company_limit().
            #
            # Duplicate-application guard: aggregators (LinkedIn especially)
            # repost the same Greenhouse/Lever/Workday posting under multiple
            # listing URLs. We've measured 17× repostings on a single
            # Overstory job. Without this NOT EXISTS, each variant would be
            # eligible to fire after the first applies. Skip any candidate
            # whose application_url matches another row that's already in
            # flight (applied, in_progress, or needs_human).
            candidates = conn.execute(f"""
                SELECT j.url, j.title, j.site, j.application_url,
                       j.tailored_resume_path, j.fit_score, j.location,
                       j.full_description, j.cover_letter_path, j.company,
                       j.strategy
                FROM jobs j
                WHERE j.tailored_resume_path IS NOT NULL
                  AND (j.apply_status IS NULL OR j.apply_status = 'failed')
                  AND (j.apply_attempts IS NULL OR j.apply_attempts < {config.DEFAULTS["max_apply_attempts"]})
                  AND j.fit_score >= ?
                  AND j.application_url IS NOT NULL
                  AND j.application_url != ''
                  AND (j.eligibility IS NULL OR j.eligibility = 'eligible')
                  {max_score_filter}
                  AND {site_filter}
                  AND {url_filter}
                  {company_excl}
                  {age_filter}
                  AND NOT EXISTS (
                      SELECT 1 FROM jobs j2
                      WHERE j2.application_url = j.application_url
                        AND j2.application_url IS NOT NULL
                        AND j2.application_url != ''
                        AND j2.url != j.url
                        AND j2.apply_status IN ('applied', 'in_progress', 'needs_human')
                  )
                ORDER BY j.fit_score DESC, j.discovered_at DESC, j.url
                LIMIT 100
            """, (min_score, *company_excl_params, *age_params)).fetchall()

            # Build in-flight buckets once, reuse for every candidate.
            # Use resolve_company_key so Greenhouse/Workday jobs (NULL company,
            # employer name in `site`) bucket correctly.
            from applypilot.scoring.tailor import resolve_company_key
            in_flight = get_in_flight_by_company(conn)
            now_utc = datetime.now(timezone.utc)

            def _in_flight_count(key: str | None) -> int:
                """How many in-flight applies for ``key`` in the per-company
                window (defaults to 30d). Used for both cap enforcement and
                the round-robin sort below."""
                if not key:
                    return 0
                _, window = _cfg.get_company_limit(key)
                cutoff = (now_utc - timedelta(days=window)).isoformat()
                return sum(1 for ts in in_flight.get(key, [])
                           if ts and ts > cutoff)

            def over_cap(job: dict) -> bool:
                key = resolve_company_key(job)
                if not key:
                    return False
                cap, _ = _cfg.get_company_limit(key)
                if cap < 0:
                    return False
                if cap == 0:
                    return True
                return _in_flight_count(key) >= cap

            # Round-robin within a run: re-sort candidates so companies with
            # the FEWEST in-flight applies fire first. Within the same
            # in-flight count, fit_score wins (and the SQL already sorted
            # by discovered_at DESC for the third tier). Net effect: we
            # cycle through every eligible employer once before picking a
            # second role at any one of them.
            #
            # Stable sort preserves SQL ordering for ties — Python's
            # sorted() is guaranteed stable since 2.3.
            candidates = sorted(
                candidates,
                key=lambda c: (
                    _in_flight_count(resolve_company_key(dict(c))),
                    -(c["fit_score"] or 0),
                ),
            )

            # Pick first candidate whose company is under cap AND ATS lane is free.
            row = None
            for cand in candidates:
                if over_cap(dict(cand)):
                    continue
                ats = detect_ats(cand["application_url"] or cand["url"] or "")
                if ats is not None and ats in active_ats:
                    continue
                row = cand
                break

            if row is None and candidates:
                logger.debug(
                    "acquire_job: all %d candidates blocked (ATS lanes=%s, cap-blocked companies present)",
                    len(candidates), active_ats,
                )

        if not row:
            conn.rollback()
            return None

        from applypilot.config import is_manual_ats

        # Defensive: the SELECT already filters NULL/empty application_urls
        # via WHERE, but if any path mutates the row before this point we'd
        # rather mark it manual_only than fall through to row["url"] (the
        # listing URL on aggregators) and have the agent try to "apply" by
        # navigating to a LinkedIn page.
        if not (row["application_url"] or "").strip():
            conn.execute(
                "UPDATE jobs SET apply_status = 'manual', "
                "apply_error = 'no application_url', "
                "apply_category = 'manual_only' WHERE url = ?",
                (row["url"],),
            )
            transition_state(conn, row["url"], "manual_only",
                             reason="acquire_job: missing application_url",
                             force=True)
            commit_with_retry(conn)
            logger.warning(
                "acquire_job: candidate had no application_url; "
                "marked manual_only: %s", row["url"][:80],
            )
            return None

        apply_url = row["application_url"]
        if is_manual_ats(apply_url):
            conn.execute(
                "UPDATE jobs SET apply_status = 'manual', apply_error = 'manual ATS', "
                "apply_category = 'manual_only' WHERE url = ?",
                (row["url"],),
            )
            # P0.5 leak (b) from decision #31: also emit canonical state
            # transition so jobs.state matches.
            transition_state(conn, row["url"], "manual_only",
                             reason="acquire_job: manual ATS",
                             force=True)
            commit_with_retry(conn)
            logger.info("Skipping manual ATS: %s", row["url"][:80])
            return None

        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            UPDATE jobs SET apply_status = 'in_progress',
                           agent_id = ?,
                           last_attempted_at = ?
            WHERE url = ?
        """, (f"worker-{worker_id}", now, row["url"]))

        # Emit state transition: ready_to_apply → applying (force=True since
        # the in-flight job may currently be at apply_failed from a prior run).
        transition_state(conn, row["url"], "applying",
                         reason=f"worker-{worker_id} acquired",
                         metadata={"worker_id": worker_id},
                         force=True)

        commit_with_retry(conn)

        return dict(row)
    except Exception:
        conn.rollback()
        raise


def mark_result(url: str, status: str, error: str | None = None,
                permanent: bool = False, duration_ms: int | None = None,
                task_id: str | None = None) -> None:
    """Update a job's apply status in the database + emit state transition."""
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    if status == "applied":
        _db_retry_execute(conn, """
            UPDATE jobs SET apply_status = 'applied', applied_at = ?,
                           apply_error = NULL, agent_id = NULL,
                           apply_duration_ms = ?, apply_task_id = ?,
                           apply_category = 'applied'
            WHERE url = ?
        """, (now, duration_ms, task_id, url))
        transition_state(conn, url, "applied",
                         reason="submission completed",
                         metadata={"duration_ms": duration_ms, "task_id": task_id},
                         force=True)
    else:
        attempts = 99 if permanent else "COALESCE(apply_attempts, 0) + 1"
        category = categorize_apply_result(status, error)
        _db_retry_execute(conn, f"""
            UPDATE jobs SET apply_status = ?, apply_error = ?,
                           apply_attempts = {attempts}, agent_id = NULL,
                           apply_duration_ms = ?, apply_task_id = ?,
                           apply_category = ?
            WHERE url = ?
        """, (status, error or "unknown", duration_ms, task_id, category, url))

        # Map the legacy apply_status value to a state-machine state.
        to_state = {
            "failed":       "apply_failed",
            "manual":       "manual_only",
            "needs_human":  "needs_human",
        }.get(status, "apply_failed")
        transition_state(conn, url, to_state,
                         reason=(error or status)[:200],
                         metadata={"category": category,
                                   "duration_ms": duration_ms,
                                   "task_id": task_id},
                         force=True)
    _db_retry_commit(conn)


def release_lock(url: str) -> None:
    """Release the in_progress lock and revert state from applying back to ready_to_apply."""
    conn = get_connection()
    _db_retry_execute(conn,
        "UPDATE jobs SET apply_status = NULL, agent_id = NULL WHERE url = ? AND apply_status = 'in_progress'",
        (url,),
    )
    transition_state(conn, url, "ready_to_apply",
                     reason="lock released without submit",
                     force=True)
    _db_retry_commit(conn)


# ---------------------------------------------------------------------------
# Utility modes (--gen, --mark-applied, --mark-failed, --reset-failed)
# ---------------------------------------------------------------------------

def gen_prompt(target_url: str, min_score: int | None = None, max_score: int | None = None,
               model: str = "sonnet", worker_id: int = 0) -> Path | None:
    """Generate a prompt file and print the Claude CLI command for manual debugging.

    Returns:
        Path to the generated prompt file, or None if no job found.
    """
    from applypilot import config as _cfg
    if min_score is None:
        min_score = _cfg.DEFAULTS["min_score"]
    job = acquire_job(target_url=target_url, min_score=min_score, max_score=max_score,
                      worker_id=worker_id)
    if not job:
        return None

    # Read resume text
    resume_path = job.get("tailored_resume_path")
    txt_path = Path(resume_path).with_suffix(".txt") if resume_path else None
    resume_text = ""
    if txt_path and txt_path.exists():
        resume_text = txt_path.read_text(encoding="utf-8")

    prompt = prompt_mod.build_prompt(job=job, tailored_resume=resume_text, worker_id=worker_id, doc_format=_doc_format)

    # Release the lock so the job stays available
    release_lock(job["url"])

    # Write prompt file
    config.ensure_dirs()
    site_slug = (job.get("site") or "unknown")[:20].replace(" ", "_")
    prompt_file = config.LOG_DIR / f"prompt_{site_slug}_{(job.get('title') or 'unknown')[:30].replace(' ', '_')}.txt"
    prompt_file.write_text(prompt, encoding="utf-8")

    # Write MCP config for reference
    port = BASE_CDP_PORT + worker_id
    mcp_path = config.APP_DIR / f".mcp-apply-{worker_id}.json"
    mcp_path.write_text(json.dumps(_make_mcp_config(port, worker_id=worker_id)), encoding="utf-8")

    return prompt_file


def mark_job(url: str, status: str, reason: str | None = None) -> None:
    """Manually mark a job's apply status in the database.

    Args:
        url: Job URL to mark.
        status: Either 'applied' or 'failed'.
        reason: Failure reason (only for status='failed').
    """
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    if status == "applied":
        _db_retry_execute(conn, """
            UPDATE jobs SET apply_status = 'applied', applied_at = ?,
                           apply_error = NULL, agent_id = NULL,
                           apply_category = 'applied'
            WHERE url = ?
        """, (now, url))
    else:
        error = reason or "manual"
        category = categorize_apply_result("failed", error)
        _db_retry_execute(conn, """
            UPDATE jobs SET apply_status = 'failed', apply_error = ?,
                           apply_attempts = 99, agent_id = NULL,
                           apply_category = ?
            WHERE url = ?
        """, (error, category, url))
    
    state_map = {
        "applied":      "applied",
        "failed":       "apply_failed",
        "manual":       "manual_only",
        "needs_human":  "needs_human",
    }
    target = state_map.get(status)
    if target:
        transition_state(conn, url, target,
            reason=f"manually marked {status} via CLI",
            force=True)
    else:
        logger.warning("mark_job: unknown status %r, no state transition emitted", status)
    _db_retry_commit(conn)


def reset_failed() -> int:
    """Reset all failed jobs so they can be retried.

    Returns:
        Number of jobs reset.
    """
    conn = get_connection()
    # Collect URLs before the bulk UPDATE so we can emit individual transitions.
    urls_to_reset = [
        r[0] for r in conn.execute("""
            SELECT url FROM jobs
            WHERE apply_status = 'failed'
              OR (apply_status IS NOT NULL AND apply_status != 'applied'
                  AND apply_status != 'in_progress'
                  AND apply_status != 'needs_human')
        """).fetchall()
    ]
    cursor = _db_retry_execute(conn, """
        UPDATE jobs SET apply_status = NULL, apply_error = NULL,
                       apply_attempts = 0, agent_id = NULL,
                       apply_category = NULL
        WHERE apply_status = 'failed'
          OR (apply_status IS NOT NULL AND apply_status != 'applied'
              AND apply_status != 'in_progress'
              AND apply_status != 'needs_human')
    """)
    
    for u in urls_to_reset:
        transition_state(conn, u, "ready_to_apply",
            reason="reset_failed — re-queued",
            force=True)
    _db_retry_commit(conn)
    return cursor.rowcount


# ---------------------------------------------------------------------------
# Per-job execution
# ---------------------------------------------------------------------------

def _reset_browser_tabs(port: int) -> None:
    """Close all existing tabs and open a fresh about:blank tab via CDP.

    Prevents leftover tabs from a previous job confusing the next agent.
    Skips chrome:// internal pages (omnibox overlays etc.) to avoid side effects.
    """
    import urllib.request
    try:
        data = urllib.request.urlopen(f"http://localhost:{port}/json", timeout=3).read()
        tabs = json.loads(data)
        # Only close http/https/about: pages — never touch chrome:// internal targets
        closeable = [
            t for t in tabs
            if t.get("type") == "page"
            and not t.get("url", "").startswith("chrome://")
        ]
        if not closeable:
            return
        # Open a fresh blank tab first (CDP requires PUT for /json/new)
        req = urllib.request.Request(
            f"http://localhost:{port}/json/new?about:blank", method="PUT"
        )
        urllib.request.urlopen(req, timeout=3)
        # Close all old closeable tabs
        for tab in closeable:
            try:
                urllib.request.urlopen(
                    f"http://localhost:{port}/json/close/{tab['id']}", timeout=2
                )
            except Exception:
                pass
    except Exception:
        pass  # Chrome not ready yet or CDP unavailable — agent will navigate anyway


def _activate_agent_tab(port: int, timeout: float = 20.0) -> None:
    """Background thread: activate the first real (non-blank) page the agent navigates to.

    Playwright MCP creates a new tab for the agent rather than reusing the existing
    about:blank tab. Without this, the user's visible Chrome tab stays on about:blank
    while the agent works in a background tab.
    """
    import urllib.request
    deadline = time.time() + timeout
    activated_url = None
    while time.time() < deadline:
        try:
            data = urllib.request.urlopen(f"http://localhost:{port}/json", timeout=2).read()
            tabs = json.loads(data)
            for tab in tabs:
                url = tab.get("url", "")
                if (tab.get("type") == "page"
                        and url
                        and not url.startswith("about:")
                        and not url.startswith("chrome://")):
                    tab_id = tab.get("id")
                    if tab_id and url != activated_url:
                        urllib.request.urlopen(
                            f"http://localhost:{port}/json/activate/{tab_id}", timeout=2
                        )
                        activated_url = url
                        # Keep watching in case the agent opens a new tab mid-job
                    break
        except Exception:
            pass
        time.sleep(0.75)


def run_job(job: dict, port: int, worker_id: int = 0,
            model: str = "sonnet", dry_run: bool = False,
            skip_tab_reset: bool = False,
            extra_context: str | None = None) -> tuple[str, int, list[dict]]:
    """Spawn a Claude Code session for one job application.

    Args:
        job: Job dict from the database.
        port: CDP port for the worker's Chrome.
        worker_id: Numeric worker identifier.
        model: Claude model name.
        dry_run: If True, don't click Submit.
        skip_tab_reset: If True, don't close leftover tabs (used after HITL/takeover).
        extra_context: Optional instructions from a previous human takeover, prepended
            to the agent prompt so it knows what was done.

    Returns:
        Tuple of (status_string, duration_ms, screening_questions).
        screening_questions is a list of dicts with keys: question, field_type, options.
    """
    # Close leftover tabs from previous job so agent starts on a blank page
    if not skip_tab_reset:
        _reset_browser_tabs(port)

    # Hard dry-run gate: install a CDP-side script that blocks form
    # submits and clicks on submit-style buttons. Belt to the prompt's
    # suspenders — even if the model ignores the prompt instruction
    # (Haiku has done this), the click never reaches a handler.
    if dry_run:
        try:
            from applypilot.apply.chrome import inject_dry_run_gate
            inject_dry_run_gate(port)
        except Exception:
            logger.debug("dry_run gate injection failed; falling back to prompt-only",
                         exc_info=True)

    # Wipe stale resume / cover-letter / MCP-config copies from the previous
    # job BEFORE build_prompt writes the fresh files. Earlier this wipe sat
    # AFTER build_prompt and silently deleted the resume the agent was
    # about to upload — agent then burned ~5 tool calls hunting for files
    # in the legacy `current/` directory before copying them into worker-0.
    reset_worker_dir(worker_id)

    # Read tailored resume text
    resume_path = job.get("tailored_resume_path")
    txt_path = Path(resume_path).with_suffix(".txt") if resume_path else None
    resume_text = ""
    if txt_path and txt_path.exists():
        resume_text = txt_path.read_text(encoding="utf-8")

    # Build the prompt
    agent_prompt = prompt_mod.build_prompt(
        job=job,
        tailored_resume=resume_text,
        dry_run=dry_run,
        worker_id=worker_id,
        doc_format=_doc_format,
    )

    # When resuming after user takeover: inject a RESUME banner so Claude does NOT
    # follow step 1 (browser_navigate) and wipe whatever the user already filled in.
    if skip_tab_reset:
        resume_header = (
            "== ⚠ RESUMING AFTER USER TAKEOVER ⚠ ==\n"
            "The browser already has the application form open. The user may have partially filled it.\n"
            "MANDATORY FIRST ACTION: browser_snapshot — see the current page state before doing anything else.\n"
            "FORBIDDEN: browser_navigate — do NOT navigate to any URL. Do NOT load the job URL.\n"
            "SKIP steps 1, 1a, 3, and 4 in STEP-BY-STEP entirely.\n"
            "After the snapshot: check for a login wall (step 5), then continue filling remaining form\n"
            "fields from the current page state and submit.\n"
            "== END RESUME ==\n\n"
        )
        if extra_context:
            resume_header += (
                f"== USER INSTRUCTIONS ==\n"
                f"{extra_context}\n"
                f"== END USER INSTRUCTIONS ==\n\n"
            )
        agent_prompt = resume_header + agent_prompt
    elif extra_context:
        agent_prompt = (
            f"== USER INSTRUCTIONS (from previous human takeover) ==\n"
            f"{extra_context}\n"
            f"== END USER INSTRUCTIONS ==\n\n"
            f"{agent_prompt}"
        )

    # Refresh Gmail token before writing MCP config (the MCP server doesn't auto-refresh)
    _refresh_gmail_token()

    # Write per-worker MCP config
    mcp_config_path = config.APP_DIR / f".mcp-apply-{worker_id}.json"
    mcp_config_path.write_text(json.dumps(_make_mcp_config(port, worker_id=worker_id)), encoding="utf-8")

    # Build claude command
    cmd = [
        "claude",
        "--model", model,
        "-p",
        "--mcp-config", str(mcp_config_path),
        "--strict-mcp-config",
        "--permission-mode", "bypassPermissions",
        "--no-session-persistence",
        "--disallowedTools", ",".join([
            # browser_install restarts the browser in CDP mode, breaking the session
            "mcp__playwright__browser_install",
            # Block Gmail write tools (read-only access for email verification)
            "mcp__gmail__draft_email", "mcp__gmail__modify_email",
            "mcp__gmail__delete_email", "mcp__gmail__download_attachment",
            "mcp__gmail__batch_modify_emails", "mcp__gmail__batch_delete_emails",
            "mcp__gmail__create_label", "mcp__gmail__update_label",
            "mcp__gmail__delete_label", "mcp__gmail__get_or_create_label",
            "mcp__gmail__list_email_labels", "mcp__gmail__create_filter",
            "mcp__gmail__list_filters", "mcp__gmail__get_filter",
            "mcp__gmail__delete_filter",
        ]),
        "--output-format", "stream-json",
        "--verbose", "-",
    ]

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    # Remove ANTHROPIC_API_KEY so the subprocess uses the user's Max plan
    # login instead of API billing. The key is loaded by config.load_env()
    # for the Gemini/OpenAI LLM fallback chain but must NOT leak into the
    # Claude Code subprocess — it would override interactive auth and hit
    # "credit balance is too low" on an unfunded API account.
    env.pop("ANTHROPIC_API_KEY", None)

    # worker_dir was wiped+recreated above, before build_prompt populated it.
    worker_dir = config.APPLY_WORKER_DIR / f"worker-{worker_id}"

    update_state(worker_id, status="applying", job_title=job["title"],
                 company=job.get("site", ""), score=job.get("fit_score", 0),
                 start_time=time.time(), actions=0, last_action="starting")
    add_event(f"[W{worker_id}] Starting: {(job.get('title') or '')[:40]} @ {job.get('site', '')}")

    worker_log = config.LOG_DIR / f"worker-{worker_id}.log"
    ts_header = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_header = (
        f"\n{'=' * 60}\n"
        f"[{ts_header}] {job['title']} @ {job.get('site', '')}\n"
        f"URL: {job.get('application_url') or job['url']}\n"
        f"Score: {job.get('fit_score', 'N/A')}/10\n"
        f"{'=' * 60}\n"
    )

    start = time.time()
    stats: dict = {}
    proc = None

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=str(worker_dir),
            start_new_session=True,
        )
        with _claude_lock:
            _claude_procs[worker_id] = proc

        proc.stdin.write(agent_prompt)
        proc.stdin.close()

        # Background thread: activate the agent's tab as soon as it navigates.
        # Playwright MCP creates a new tab rather than reusing the existing blank tab,
        # so without this the user's visible Chrome tab stays on about:blank.
        threading.Thread(
            target=_activate_agent_tab,
            args=(port,),
            daemon=True,
            name=f"tab-activator-w{worker_id}",
        ).start()

        text_parts: list[str] = []
        screening_qs: list[dict] = []
        # Maps Claude Code tool_use_id → fully-qualified MCP tool name. Used
        # below to label tool_result blocks (which only carry the id) so we
        # can selectively log gmail results and any errors.
        tool_use_names: dict[str, str] = {}
        # Per-job ordered list of tool calls — captured for the per-ATS
        # success-path memo (apply/successful_paths.py). Populated as
        # tool_use blocks stream in; persisted on RESULT:APPLIED.
        tool_calls: list[dict] = []
        # buffering=1 → line-buffered. Without this Python defaults to
        # 8KB block buffering for text-mode files, which means short
        # tool-call entries (~30 bytes each) accumulate invisibly until
        # 250+ have happened or run_job exits. Flushing per-line keeps
        # `tail -f worker-0.log` actually live during long apply runs.
        with open(worker_log, "a", encoding="utf-8", buffering=1) as lf:
            lf.write(log_header)

            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                # Check for user takeover between output lines
                tev = _takeover_events.get(worker_id)
                if tev and tev.is_set():
                    break
                try:
                    msg = json.loads(line)
                    msg_type = msg.get("type")
                    if msg_type == "assistant":
                        for block in msg.get("message", {}).get("content", []):
                            bt = block.get("type")
                            if bt == "text":
                                text_parts.append(block["text"])
                                lf.write(block["text"] + "\n")
                                # Parse SCREENING_Q lines from text
                                for tl in block["text"].split("\n"):
                                    tl = tl.strip()
                                    if tl.startswith("SCREENING_Q:"):
                                        payload = tl[len("SCREENING_Q:"):].strip()
                                        parts = payload.split("|")
                                        if len(parts) >= 2:
                                            screening_qs.append({
                                                "question": parts[0].strip(),
                                                "field_type": parts[1].strip(),
                                                "options": parts[2].strip() if len(parts) > 2 else "",
                                            })
                            elif bt == "tool_use":
                                full_name = block.get("name", "")
                                # Remember tool_use_id → name so we can label results below.
                                tu_id = block.get("id")
                                if tu_id:
                                    tool_use_names[tu_id] = full_name
                                name = (
                                    full_name
                                    .replace("mcp__playwright__", "")
                                    .replace("mcp__gmail__", "gmail:")
                                )
                                inp = block.get("input", {})
                                if "url" in inp:
                                    desc = f"{name} {inp['url'][:60]}"
                                elif "ref" in inp:
                                    desc = f"{name} {inp.get('element', inp.get('text', ''))}"[:50]
                                elif "fields" in inp:
                                    desc = f"{name} ({len(inp['fields'])} fields)"
                                elif "paths" in inp:
                                    desc = f"{name} upload"
                                else:
                                    desc = name

                                lf.write(f"  >> {desc}\n")
                                tool_calls.append({"tool": name, "summary": desc})
                                ws = get_state(worker_id)
                                cur_actions = ws.actions if ws else 0
                                update_state(worker_id,
                                             actions=cur_actions + 1,
                                             last_action=desc[:35])
                    elif msg_type == "user":
                        # Tool results return as user messages. We don't log
                        # browser_snapshot etc. — the dumps would dwarf the log.
                        # We DO log gmail results (so we know whether the agent
                        # actually read an email) and any tool errors.
                        for block in msg.get("message", {}).get("content", []):
                            if block.get("type") != "tool_result":
                                continue
                            tu_id = block.get("tool_use_id", "")
                            full_name = tool_use_names.get(tu_id, "")
                            is_error = bool(block.get("is_error", False))
                            log_this = is_error or "gmail" in full_name
                            if not log_this:
                                continue
                            content = block.get("content", "")
                            if isinstance(content, list):
                                content = "\n".join(
                                    (c.get("text", "") if isinstance(c, dict) else str(c))
                                    for c in content
                                )
                            preview = str(content).replace("\n", " ")[:500]
                            short_name = (
                                full_name
                                .replace("mcp__playwright__", "")
                                .replace("mcp__gmail__", "gmail:")
                            ) or "?"
                            marker = " [ERROR]" if is_error else ""
                            lf.write(f"  << {short_name}{marker}: {preview}\n")
                    elif msg_type == "result":
                        stats = {
                            "input_tokens": msg.get("usage", {}).get("input_tokens", 0),
                            "output_tokens": msg.get("usage", {}).get("output_tokens", 0),
                            "cache_read": msg.get("usage", {}).get("cache_read_input_tokens", 0),
                            "cache_create": msg.get("usage", {}).get("cache_creation_input_tokens", 0),
                            "cost_usd": msg.get("total_cost_usd", 0),
                            "turns": msg.get("num_turns", 0),
                        }
                        text_parts.append(msg.get("result", ""))
                except json.JSONDecodeError:
                    text_parts.append(line)
                    lf.write(line + "\n")

        proc.wait(timeout=300)
        returncode = proc.returncode
        proc = None

        # Check if a user takeover killed the proc
        tev = _takeover_events.get(worker_id)
        if tev and tev.is_set():
            return "takeover", int((time.time() - start) * 1000), []

        if returncode and returncode < 0:
            return "skipped", int((time.time() - start) * 1000), []

        output = "\n".join(text_parts)
        elapsed = int(time.time() - start)
        duration_ms = int((time.time() - start) * 1000)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        job_log = config.LOG_DIR / f"claude_{ts}_w{worker_id}_{job.get('site', 'unknown')[:20]}.txt"
        job_log.write_text(output, encoding="utf-8")

        if stats:
            cost = stats.get("cost_usd", 0)
            ws = get_state(worker_id)
            prev_cost = ws.total_cost if ws else 0.0
            update_state(worker_id, total_cost=prev_cost + cost)

        # Detect Claude Code credit exhaustion — stop the entire worker
        if "credit balance is too low" in output.lower() or "insufficient credits" in output.lower():
            add_event(f"[W{worker_id}] CREDIT EXHAUSTED — Claude Code credits depleted")
            update_state(worker_id, status="credits_exhausted",
                         last_action="NO CREDITS")
            logger.error("Claude Code credits exhausted. Cannot auto-apply. "
                         "Top up at https://console.anthropic.com/settings/billing")
            return "failed:credits_exhausted", duration_ms, []

        # Parse ACCOUNT_CREATED lines and save to DB
        _parse_account_created(output, job.get("url"))

        # Parse QA: lines and store in knowledge base
        job_url = job.get("url")
        job_ats = detect_ats(job.get("application_url") or job_url or "")
        _parse_qa_lines(output, job_url=job_url, ats_slug=job_ats)

        def _clean_reason(s: str) -> str:
            return re.sub(r'[*`"]+$', '', s).strip()

        for result_status in ["APPLIED", "ALREADY_APPLIED", "SUCCESS", "EXPIRED", "CAPTCHA", "LOGIN_ISSUE"]:
            if f"RESULT:{result_status}" in output:
                # Normalize SUCCESS/ALREADY_APPLIED -> applied (already applied counts as applied)
                canonical = "applied" if result_status in ("SUCCESS", "ALREADY_APPLIED") else result_status.lower()
                # Mark Q&A outcomes based on application result
                if canonical == "applied" and job_url:
                    from applypilot.database import mark_qa_outcome
                    mark_qa_outcome(job_url, "accepted")
                # Memo the successful tool-call sequence for this ATS so
                # the next first-of-its-kind apply gets a "prior path"
                # hint in its prompt (apply/successful_paths.py).
                if canonical == "applied" and job_ats:
                    from applypilot.apply.successful_paths import save_path
                    save_path(job_ats, tool_calls,
                              job_url=job.get("application_url") or job_url,
                              duration_ms=duration_ms)
                display = "ALREADY APPLIED" if result_status == "ALREADY_APPLIED" else canonical.upper()
                add_event(f"[W{worker_id}] {display} ({elapsed}s): {(job.get('title') or '')[:30]}")
                update_state(worker_id, status=canonical,
                             last_action=f"{display} ({elapsed}s)")
                return canonical, duration_ms, screening_qs

        # Check for RESULT:NEEDS_HUMAN:{reason}:{stuck_url}
        # Must be parsed before RESULT:FAILED since the format includes colons
        if "RESULT:NEEDS_HUMAN:" in output:
            for out_line in output.split("\n"):
                if "RESULT:NEEDS_HUMAN:" in out_line:
                    # Format: RESULT:NEEDS_HUMAN:{reason}:{url} [reason: detail]
                    # Split on "NEEDS_HUMAN:" then split first colon to get reason vs rest
                    after = out_line.split("RESULT:NEEDS_HUMAN:", 1)[-1].strip()
                    after = _clean_reason(after)
                    # Extract optional [reason: ...] detail suffix from the end
                    reason_detail = ""
                    if " [reason: " in after:
                        after, detail_part = after.rsplit(" [reason: ", 1)
                        reason_detail = detail_part.rstrip("]").strip()
                        after = after.strip()
                    if ":" in after:
                        nh_reason, nh_url = after.split(":", 1)
                        nh_reason = nh_reason.strip()
                        nh_url = nh_url.strip()
                    else:
                        nh_reason = after
                        nh_url = job.get("application_url") or job["url"]
                    if reason_detail:
                        nh_url = f"{nh_url}|detail:{reason_detail}"
                    add_event(f"[W{worker_id}] NEEDS_HUMAN:{nh_reason} ({elapsed}s): {(job.get('title') or '')[:30]}")
                    update_state(worker_id, status="needs_human",
                                 last_action=f"NEEDS_HUMAN: {nh_reason[:25]}")
                    return f"needs_human:{nh_reason}:{nh_url}", duration_ms, screening_qs

        if "RESULT:FAILED" in output:
            for out_line in output.split("\n"):
                if "RESULT:FAILED" in out_line:
                    reason = (
                        out_line.split("RESULT:FAILED:")[-1].strip()
                        if ":" in out_line[out_line.index("FAILED") + 6:]
                        else "unknown"
                    )
                    reason = _clean_reason(reason)
                    PROMOTE_TO_STATUS = {"captcha", "expired", "login_issue"}
                    if reason in PROMOTE_TO_STATUS:
                        add_event(f"[W{worker_id}] {reason.upper()} ({elapsed}s): {(job.get('title') or '')[:30]}")
                        update_state(worker_id, status=reason,
                                     last_action=f"{reason.upper()} ({elapsed}s)")
                        return reason, duration_ms, screening_qs
                    add_event(f"[W{worker_id}] FAILED ({elapsed}s): {reason[:30]}")
                    update_state(worker_id, status="failed",
                                 last_action=f"FAILED: {reason[:25]}")
                    return f"failed:{reason}", duration_ms, screening_qs
            return "failed:unknown", duration_ms, screening_qs

        # No explicit RESULT line. Try to infer the outcome from agent output.
        inferred = _infer_result_from_output(output)
        if inferred in ("applied", "already_applied"):
            label = "ALREADY APPLIED" if inferred == "already_applied" else "APPLIED"
            add_event(f"[W{worker_id}] INFERRED {label} ({elapsed}s): {(job.get('title') or '')[:30]}")
            update_state(worker_id, status="applied",
                         last_action=f"{label} (inferred, {elapsed}s)")
            # Same memoization as the literal-RESULT path above.
            if job_ats:
                from applypilot.apply.successful_paths import save_path
                save_path(job_ats, tool_calls,
                          job_url=job.get("application_url") or job_url,
                          duration_ms=duration_ms)
            return "applied", duration_ms, screening_qs
        if inferred:
            add_event(f"[W{worker_id}] INFERRED {inferred.upper()} ({elapsed}s): {(job.get('title') or '')[:30]}")
            update_state(worker_id, status="failed",
                         last_action=f"inferred:{inferred[:25]}")
            return f"failed:{inferred}", duration_ms, screening_qs

        add_event(f"[W{worker_id}] NO RESULT ({elapsed}s)")
        update_state(worker_id, status="failed", last_action=f"no result ({elapsed}s)")
        return "failed:no_result_line", duration_ms, screening_qs

    except subprocess.TimeoutExpired:
        duration_ms = int((time.time() - start) * 1000)
        elapsed = int(time.time() - start)
        add_event(f"[W{worker_id}] TIMEOUT ({elapsed}s)")
        update_state(worker_id, status="failed", last_action=f"TIMEOUT ({elapsed}s)")
        return "failed:timeout", duration_ms, []
    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        add_event(f"[W{worker_id}] ERROR: {str(e)[:40]}")
        update_state(worker_id, status="failed", last_action=f"ERROR: {str(e)[:25]}")
        return f"failed:{str(e)[:100]}", duration_ms, []
    finally:
        with _claude_lock:
            _claude_procs.pop(worker_id, None)
        if proc is not None and proc.poll() is None:
            _kill_process_tree(proc.pid)


# ---------------------------------------------------------------------------
# Worker loop / CLI entry — moved to apply/orchestrator.py.
# Re-exports live at the very bottom of this module so the
# launcher → orchestrator → launcher import cycle resolves cleanly.
# ---------------------------------------------------------------------------


# Re-export the orchestrator so cli.py and tests can keep doing
# `from applypilot.apply.launcher import main` etc. The import is at the
# very bottom of the module on purpose: orchestrator.py's top-level
# `from applypilot.apply.launcher import ...` would otherwise see a
# partially-loaded launcher. By the time we reach this line, every name
# in launcher's module namespace is fully defined.

from applypilot.apply.orchestrator import (  # noqa: E402, F401
    POLL_INTERVAL,
    _probe_for_reconnect,
    worker_loop,
    _worker_loop_body,
    _prompt_user_for_qa,
    main,
)
