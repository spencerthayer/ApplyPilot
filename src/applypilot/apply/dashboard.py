"""Rich live dashboard for the apply pipeline.

Displays real-time worker status, job progress, and recent events
in a terminal dashboard using the Rich library.
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

logger = logging.getLogger(__name__)


@dataclass
class WorkerState:
    """Tracks the current state of the apply worker."""

    worker_id: int = 0
    status: str = "starting"  # starting, applying, applied, failed, expired, captcha, idle, done
    job_title: str = ""
    company: str = ""
    score: int = 0
    start_time: float = 0.0
    actions: int = 0
    last_action: str = ""
    jobs_applied: int = 0
    jobs_failed: int = 0
    jobs_done: int = 0
    total_cost: float = 0.0
    log_file: Path | None = None
    chrome_ok: bool | None = None  # None=unknown, True=connected, False=unreachable


# Module-level state (thread-safe via _lock)
_worker_states: dict[int, WorkerState] = {}
_events: list[str] = []
_lock = threading.Lock()
MAX_EVENTS = 8


# ---------------------------------------------------------------------------
# State mutation helpers
# ---------------------------------------------------------------------------


def init_worker(worker_id: int = 0) -> None:
    """Register the worker in the dashboard state."""
    with _lock:
        _worker_states[worker_id] = WorkerState(worker_id=worker_id)


def update_state(worker_id: int = 0, **kwargs) -> None:
    """Update the worker's state fields.

    Args:
        worker_id: Which worker to update.
        **kwargs: Field names and values to set on WorkerState.
    """
    with _lock:
        state = _worker_states.get(worker_id)
        if state is not None:
            for key, value in kwargs.items():
                setattr(state, key, value)


def get_state(worker_id: int = 0) -> WorkerState | None:
    """Read the worker's current state."""
    with _lock:
        return _worker_states.get(worker_id)


def add_event(msg: str) -> None:
    """Add a timestamped event to the scrolling event log.

    Args:
        msg: Rich markup string describing the event.
    """
    ts = datetime.now().strftime("%H:%M:%S")
    with _lock:
        _events.append(f"[dim]{ts}[/dim] {msg}")
        if len(_events) > MAX_EVENTS:
            _events.pop(0)


# ---------------------------------------------------------------------------
# Chrome health checks
# ---------------------------------------------------------------------------

# CDP port formula: 9222 + worker_id  (matches launch_chrome in chrome.py)
_CDP_BASE_PORT = 9222
_HEALTH_CHECK_INTERVAL = 5  # seconds
_HEALTH_CHECK_TIMEOUT = 0.5  # seconds

_health_thread: threading.Thread | None = None
_health_stop = threading.Event()


def _check_chrome_health(worker_id: int) -> bool:
    """Return True if Chrome CDP port for this worker is reachable."""
    port = _CDP_BASE_PORT + worker_id
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version",
            timeout=_HEALTH_CHECK_TIMEOUT,
        ) as resp:
            return resp.status == 200
    except Exception:
        return False


def _health_check_loop() -> None:
    """Background thread: poll Chrome CDP ports and update chrome_ok."""
    while not _health_stop.wait(_HEALTH_CHECK_INTERVAL):
        with _lock:
            worker_ids = list(_worker_states.keys())
        for wid in worker_ids:
            ok = _check_chrome_health(wid)
            with _lock:
                state = _worker_states.get(wid)
                if state is None:
                    continue
                prev = state.chrome_ok
                state.chrome_ok = ok
            # Log only on transitions
            if prev is True and not ok:
                logger.error("[worker-%d] Chrome CDP unreachable (port %d)", wid, _CDP_BASE_PORT + wid)
            elif prev is False and ok:
                logger.info("[worker-%d] Chrome CDP reconnected (port %d)", wid, _CDP_BASE_PORT + wid)
            elif prev is None and not ok:
                logger.warning("[worker-%d] Chrome CDP not reachable at startup (port %d)", wid, _CDP_BASE_PORT + wid)


def start_health_checks() -> None:
    """Start the background Chrome health-check thread (idempotent)."""
    global _health_thread
    if _health_thread is not None and _health_thread.is_alive():
        return
    _health_stop.clear()
    _health_thread = threading.Thread(target=_health_check_loop, daemon=True, name="chrome-health")
    _health_thread.start()


def stop_health_checks() -> None:
    """Stop the background Chrome health-check thread."""
    _health_stop.set()


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

# Status -> Rich style mapping
_STATUS_STYLES: dict[str, str] = {
    "starting": "dim",
    "idle": "dim",
    "applying": "yellow",
    "applied": "bold green",
    "failed": "red",
    "expired": "dim red",
    "captcha": "magenta",
    "login_issue": "red",
    "done": "bold",
    "waiting_human": "bold magenta",
    "waiting_answer": "bold cyan",
}


def render_dashboard() -> Table:
    """Build the Rich table showing all worker statuses.

    Returns:
        A Rich Table object ready for display.
    """
    table = Table(title="ApplyPilot Dashboard", expand=True, show_lines=False)
    table.add_column("W", width=3, justify="center")
    table.add_column("Job", min_width=30, max_width=50, no_wrap=True)
    table.add_column("Status", width=12, justify="center")
    table.add_column("Time", width=6, justify="right")
    table.add_column("Acts", width=5, justify="right")
    table.add_column("Last Action", min_width=20, max_width=35, no_wrap=True)
    table.add_column("OK", width=4, justify="right", style="green")
    table.add_column("Fail", width=4, justify="right", style="red")
    table.add_column("Cost", width=8, justify="right")

    with _lock:
        states = sorted(_worker_states.values(), key=lambda s: s.worker_id)

    total_applied = 0
    total_failed = 0
    total_cost = 0.0

    for s in states:
        elapsed = ""
        if s.start_time and s.status == "applying":
            elapsed = f"{int(time.time() - s.start_time)}s"

        style = _STATUS_STYLES.get(s.status, "")
        status_text = Text(s.status.upper(), style=style)

        job_text = f"{s.job_title[:28]} @ {s.company[:16]}" if s.job_title else ""

        if s.chrome_ok is True:
            w_style = "bold blue"
        elif s.chrome_ok is False:
            w_style = "bold red"
        else:
            w_style = "bold"
        w_text = Text(str(s.worker_id), style=w_style)

        table.add_row(
            w_text,
            job_text,
            status_text,
            elapsed,
            str(s.actions) if s.actions else "",
            s.last_action[:35] if s.last_action else "",
            str(s.jobs_applied),
            str(s.jobs_failed),
            f"${s.total_cost:.3f}" if s.total_cost else "",
        )
        total_applied += s.jobs_applied
        total_failed += s.jobs_failed
        total_cost += s.total_cost

    # Totals row
    table.add_section()
    table.add_row(
        "",
        "",
        "",
        "",
        "",
        "TOTAL",
        str(total_applied),
        str(total_failed),
        f"${total_cost:.3f}",
        style="bold",
    )

    return table


def render_full() -> Table | Group:
    """Render the dashboard table plus the recent events panel.

    Returns:
        A Rich Group (table + events panel) or just the table if no events.
    """
    table = render_dashboard()

    with _lock:
        event_lines = list(_events)

    if event_lines:
        event_text = Text.from_markup("\n".join(event_lines))
        events_panel = Panel(
            event_text,
            title="Recent Events",
            border_style="dim",
            height=min(MAX_EVENTS + 2, len(event_lines) + 2),
        )
        return Group(table, events_panel)

    return table


def get_totals() -> dict[str, int | float]:
    """Compute aggregate totals across all workers.

    Returns:
        Dict with keys: applied, failed, cost.
    """
    with _lock:
        applied = sum(s.jobs_applied for s in _worker_states.values())
        failed = sum(s.jobs_failed for s in _worker_states.values())
        cost = sum(s.total_cost for s in _worker_states.values())
    return {"applied": applied, "failed": failed, "cost": cost}


# ---------------------------------------------------------------------------
# Live dashboard lifecycle — owns the Rich Live instance + refresh thread
# ---------------------------------------------------------------------------

from rich.console import Console
from rich.live import Live

_live: Live | None = None
_refresh_thread: threading.Thread | None = None
_refresh_running = False
_pause_event = threading.Event()  # set = paused
_console = Console()


def start() -> None:
    """Start the live dashboard. Call once from main()."""
    global _live, _refresh_thread, _refresh_running
    if _live is not None:
        return
    _live = Live(render_full(), console=_console, refresh_per_second=2)
    _live.start()
    _refresh_running = True
    _pause_event.clear()

    def _refresh():
        while _refresh_running:
            if not _pause_event.is_set() and _live is not None:
                try:
                    _live.update(render_full())
                except Exception:
                    pass
            time.sleep(0.5)

    _refresh_thread = threading.Thread(target=_refresh, daemon=True, name="dashboard-refresh")
    _refresh_thread.start()


def stop() -> None:
    """Stop the live dashboard and refresh thread."""
    global _live, _refresh_running
    _refresh_running = False
    if _refresh_thread is not None:
        _refresh_thread.join(timeout=2)
    if _live is not None:
        try:
            _live.update(render_full())
            _live.stop()
        except Exception:
            pass
        _live = None


def pause() -> None:
    """Pause dashboard refresh (for interactive prompts)."""
    _pause_event.set()
    if _live is not None:
        try:
            _live.stop()
        except Exception:
            pass


def resume() -> None:
    """Resume dashboard refresh after interactive prompt."""
    if _live is not None:
        try:
            _live.start()
        except Exception:
            pass
    _pause_event.clear()


def pause_for_input(msg: str, options: str = "Enter=continue") -> str:
    """Pause dashboard, show prompt, get input, resume dashboard.

    This is the single entry point for all interactive prompts during apply.
    Ensures dashboard doesn't flicker over the prompt.
    """
    import sys
    pause()
    sys.stderr.write(f"\n{msg}\n    [{options}]: ")
    sys.stderr.flush()
    try:
        result = input().strip().lower()
    except EOFError:
        result = ""
    resume()
    return result


def log_info(msg: str) -> None:
    """Print a message to the terminal. Use instead of console.print in business logic."""
    _console.print(msg)


def log_warning(msg: str) -> None:
    """Print a warning to the terminal."""
    pause()
    _console.print(msg)
    resume()


def show_summary(applied: int, failed: int, cost: float, log_dir: str) -> None:
    """Print the final summary after apply completes."""
    _console.print(f"\n[bold]Done: {applied} applied, {failed} failed (${cost:.3f})[/bold]")
    _console.print(f"Logs: {log_dir}")
