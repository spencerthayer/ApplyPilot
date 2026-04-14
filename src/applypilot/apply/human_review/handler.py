"""Handler — extracted from human_review."""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from applypilot.apply.chrome import HITL_CDP_PORT, HITL_WORKER_ID
from applypilot.apply.human_review._state import _sessions, _sessions_lock
from applypilot.apply.human_review.banner import _cdp_list_targets
from applypilot.apply.human_review.ui import _build_ui_html

logger = logging.getLogger(__name__)

import hashlib
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse


def _job_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:12]


def _server_lazy():
    """Lazy import to avoid circular dependency with server.py."""
    from applypilot.apply.human_review import server

    return server


class _Handler(BaseHTTPRequestHandler):
    """HTTP handler for the HITL review UI."""

    def log_message(self, format, *args):
        # Suppress default request logging (use our logger instead)
        pass

    def _send_json(self, data: dict | list, status: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_sse_headers(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/" or path == "":
            self._send_html(_build_ui_html())
            return

        if path == "/api/jobs":
            from applypilot.apply.human_review._compat import get_needs_human_jobs

            jobs = get_needs_human_jobs()
            # Add hash for each job
            for j in jobs:
                j["hash"] = _job_hash(j["url"])
            self._send_json(jobs)
            return

        if path.startswith("/api/status/"):
            h = path[len("/api/status/"):]
            with _sessions_lock:
                session = _sessions.get(h, {})
            self._send_json(
                {
                    "status": session.get("status", "idle"),
                    "result": session.get("result"),
                }
            )
            return

        if path.startswith("/api/result-stream/"):
            h = path[len("/api/result-stream/"):]
            self._handle_sse(h)
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path

        if path.startswith("/api/start/"):
            h = path[len("/api/start/"):]
            self._handle_start(h)
            return

        if path.startswith("/api/done/"):
            h = path[len("/api/done/"):]
            self._handle_done(h)
            return

        if path.startswith("/api/skip/"):
            h = path[len("/api/skip/"):]
            self._handle_skip(h)
            return

        self.send_response(404)
        self.end_headers()

    def _handle_start(self, h: str) -> None:
        """Launch HITL Chrome and inject banner for the given job hash."""
        from applypilot.apply.human_review._compat import get_needs_human_jobs

        # Find the job
        jobs = get_needs_human_jobs()
        job = next((j for j in jobs if _job_hash(j["url"]) == h), None)
        if not job:
            self._send_json({"error": "Job not found"}, 404)
            return

        # Check if another session is already active
        with _sessions_lock:
            active = [
                s
                for s in _sessions.values()
                if s.get("status") in ("chrome_open", "agent_running") and _job_hash(s["job"]["url"]) != h
            ]
            if active:
                self._send_json({"error": "Another session is active"}, 409)
                return

            _sessions[h] = {
                "job": job,
                "status": "chrome_open",
                "result": None,
                "log_offset": 0,
                "log_file": None,
                "agent_started_at": 0.0,
            }

        # Launch Chrome in background thread (may take a few seconds)
        def _launch():
            try:
                proc = _server_lazy()._start_hitl_chrome(job)
                with _sessions_lock:
                    if h in _sessions:
                        _sessions[h]["chrome_proc"] = proc
            except Exception as e:
                logger.error("Failed to start HITL Chrome: %s", e)
                with _sessions_lock:
                    if h in _sessions:
                        _sessions[h]["status"] = "error"
                        _sessions[h]["result"] = f"launch_error:{e}"

        threading.Thread(target=_launch, daemon=True).start()
        self._send_json({"ok": True, "hash": h})

    def _handle_done(self, h: str) -> None:
        """User clicked Done or Continue — spawn agent thread."""
        length = int(self.headers.get("Content-Length", 0))
        body = {}
        if length:
            try:
                body = json.loads(self.rfile.read(length))
            except Exception:
                pass
        custom_instructions = (body.get("instructions") or "").strip()

        with _sessions_lock:
            session = _sessions.get(h)

        if not session:
            self._send_json({"error": "Session not found"}, 404)
            return

        if session.get("status") == "agent_running":
            self._send_json({"error": "Agent already running"}, 409)
            return

        with _sessions_lock:
            if h in _sessions:
                _sessions[h]["status"] = "agent_running"
                _sessions[h]["log_offset"] = 0
                _sessions[h]["log_file"] = None
                _sessions[h]["agent_started_at"] = time.time()
                if custom_instructions:
                    _sessions[h]["custom_instructions"] = custom_instructions

        threading.Thread(target=_server_lazy()._run_agent_for_job, args=(h,), daemon=True).start()
        self._send_json({"ok": True})

    def _handle_skip(self, h: str) -> None:
        """Permanently skip a job (mark as failed)."""
        from applypilot.apply.human_review._compat import get_needs_human_jobs

        jobs = get_needs_human_jobs()
        job = next((j for j in jobs if _job_hash(j["url"]) == h), None)
        if not job:
            self._send_json({"error": "Job not found"}, 404)
            return

        from applypilot.apply.launcher import mark_result

        mark_result(job["url"], "failed", "human_skipped", permanent=True)

        with _sessions_lock:
            _sessions.pop(h, None)

        self._send_json({"ok": True})

    def _handle_sse(self, h: str) -> None:
        """Stream agent log output via SSE while the agent runs."""
        self._send_sse_headers()

        with _sessions_lock:
            session = _sessions.get(h, {})
            offset = session.get("log_offset", 0)
            active_log_file = session.get("log_file")
            started_at = float(session.get("agent_started_at") or 0.0)

        try:
            while True:
                with _sessions_lock:
                    session = _sessions.get(h, {})
                    status = session.get("status", "idle")
                    result = session.get("result")
                    started_at = float(session.get("agent_started_at") or started_at)
                    active_log_file = session.get("log_file") or active_log_file

                if not active_log_file:
                    active_log_file = _server_lazy()._latest_worker_job_log(HITL_WORKER_ID, started_at)
                    if active_log_file:
                        offset = 0
                        with _sessions_lock:
                            if h in _sessions:
                                _sessions[h]["log_file"] = active_log_file
                                _sessions[h]["log_offset"] = 0

                # Tail the log file
                if active_log_file:
                    try:
                        content = Path(active_log_file).read_text(encoding="utf-8", errors="replace")
                        if offset > len(content):
                            offset = 0
                        if len(content) > offset:
                            new_text = content[offset:].replace("\n", "\\n")
                            offset = len(content)
                            with _sessions_lock:
                                if h in _sessions:
                                    _sessions[h]["log_offset"] = offset
                            data = json.dumps({"text": new_text})
                            self.wfile.write(f"data: {data}\n\n".encode())
                            self.wfile.flush()
                    except OSError:
                        pass

                # Check if Chrome closed (ConnectionRefusedError on CDP)
                if status == "chrome_open":
                    targets = _cdp_list_targets(HITL_CDP_PORT)
                    if not targets:
                        self.wfile.write(b"event: chrome_closed\ndata: {}\n\n")
                        self.wfile.flush()
                        break

                # Done
                if status == "done" and result is not None:
                    data = json.dumps({"result": result})
                    self.wfile.write(f"event: done\ndata: {data}\n\n".encode())
                    self.wfile.flush()
                    break

                time.sleep(1)
        except (BrokenPipeError, ConnectionResetError):
            pass
