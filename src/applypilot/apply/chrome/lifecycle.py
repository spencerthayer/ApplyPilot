"""Lifecycle — extracted from chrome/__init__.py."""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
from pathlib import Path

from applypilot import config
from applypilot.apply.chrome.profile import (
    setup_worker_profile,
    _remove_singleton_locks,
    _suppress_restore_nag,
    _get_real_user_agent,
)
from applypilot.apply.chrome.window import (
    compute_tile,
    _pick_viewport,
    _worker_viewports,
    _find_chrome_pid_for_port,
)

logger = logging.getLogger(__name__)

import threading
import time
import urllib.request as _ureq

try:
    import websocket as _websocket
except ModuleNotFoundError:
    import types as _types

    _websocket = _types.SimpleNamespace(WebSocket=type("_Missing", (), {"__init__": lambda *a, **k: None}))

BASE_CDP_PORT = 9222
HITL_CDP_PORT = 9300
HITL_WORKER_ID = 99
_chrome_procs: dict[int, subprocess.Popen] = {}
_chrome_lock = threading.Lock()


class _AdoptedChromeProcess:
    """Stub that wraps an already-running Chrome process adopted for reconnect.

    Provides the same .pid and .poll() interface as subprocess.Popen so that
    the rest of worker_loop() (HITL relaunch, cleanup_worker, etc.) works
    identically whether Chrome was freshly launched or reconnected.
    """

    def __init__(self, pid: int | None) -> None:
        self.pid: int = pid or 0
        self._pid = pid
        self.stdin = None
        self.stdout = None
        self.stderr = None

    def poll(self) -> int | None:
        """Return None if the process is alive, non-None if it has exited."""
        if not self._pid:
            return None  # Unknown PID — assume alive
        try:
            os.kill(self._pid, 0)
            return None  # Process exists and is reachable
        except (ProcessLookupError, OSError):
            return 1  # Dead

    def wait(self, timeout: float | None = None) -> int:
        return 0  # No-op; we don't manage lifecycle here


def _kill_process_tree(pid: int) -> None:
    """Kill a process and all its children.

    On Windows, Chrome spawns 10+ child processes (GPU, renderer, etc.),
    so taskkill /T is needed to kill the entire tree. On Unix, os.killpg
    handles the process group.
    """
    import signal as _signal

    try:
        if platform.system() == "Windows":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        else:
            # Unix: kill entire process group
            import os

            try:
                os.killpg(os.getpgid(pid), _signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                # Process already gone or owned by another user
                try:
                    os.kill(pid, _signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
    except Exception:
        logger.debug("Failed to kill process tree for PID %d", pid, exc_info=True)


def _kill_on_port(port: int) -> None:
    """Kill any process listening on a specific port (zombie cleanup).

    Uses netstat on Windows, lsof on macOS/Linux.
    """
    try:
        if platform.system() == "Windows":
            result = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    pid = line.strip().split()[-1]
                    if pid.isdigit():
                        _kill_process_tree(int(pid))
        else:
            # macOS / Linux
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            for pid_str in result.stdout.strip().splitlines():
                pid_str = pid_str.strip()
                if pid_str.isdigit():
                    _kill_process_tree(int(pid_str))
    except FileNotFoundError:
        logger.debug("Port-kill tool not found (netstat/lsof) for port %d", port)
    except Exception:
        logger.debug("Failed to kill process on port %d", port, exc_info=True)


def probe_existing_chrome(port: int, expected_profile_dir: Path) -> int | None:
    """Check if a usable Chrome instance is already running on the given CDP port.

    Verifies the instance belongs to the expected worker profile by inspecting
    the process cmdline. Only works on Linux (requires /proc filesystem).

    Returns:
        Chrome PID if a verified Chrome is running on this port, None otherwise.
    """
    # Step 1: Does CDP respond?
    try:
        _ureq.urlopen(f"http://localhost:{port}/json", timeout=2).read()
    except Exception:
        return None  # Nothing (or wrong thing) on this port

    # Step 2: Find the Chrome PID via /proc cmdline scan
    pid = _find_chrome_pid_for_port(port)
    if pid is None:
        logger.debug("Chrome detected on port %d but PID not found in /proc", port)
        return None

    # Step 3: Verify it's using our expected profile directory
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            cmdline = fh.read().decode("utf-8", errors="replace")
        if str(expected_profile_dir) not in cmdline:
            logger.debug(
                "Chrome on port %d (pid %d) uses a different profile — not ours",
                port,
                pid,
            )
            return None
    except OSError:
        # /proc entry disappeared — process exited between steps
        return None

    logger.info("Verified existing Chrome on port %d (pid %d)", port, pid)
    return pid


def launch_chrome(
        worker_id: int,
        port: int | None = None,
        headless: bool = False,
        refresh_cookies: bool = False,
        minimized: bool = False,
        ats_slug: str | None = None,
        total_workers: int = 1,
) -> subprocess.Popen:
    """Launch a Chrome instance with remote debugging for a worker.

    Args:
        worker_id: Numeric worker identifier.
        port: CDP port. Defaults to BASE_CDP_PORT + worker_id.
        headless: Run Chrome in headless mode (no visible window).
        refresh_cookies: Re-copy session files from user's Chrome profile.
        minimized: Start Chrome minimized. Defaults to False — windows are
            tiled on the desktop using compute_tile(). Pass True to override
            and start hidden (useful for background/CI runs).
        ats_slug: Optional ATS platform slug. If a persistent session
            exists for this ATS, its auth files are overlaid on the
            worker profile.
        total_workers: Total number of concurrent workers, used to compute
            the tile layout position. Defaults to 1.

    Returns:
        subprocess.Popen handle for the Chrome process.
    """
    if port is None:
        port = BASE_CDP_PORT + worker_id

    profile_dir = setup_worker_profile(worker_id, refresh_cookies=refresh_cookies, ats_slug=ats_slug)

    # Kill any zombie Chrome from a previous run on this port
    _kill_on_port(port)

    # Remove stale singleton locks (left from copied/crashed profiles)
    _remove_singleton_locks(profile_dir)

    # Patch preferences to suppress restore nag and set startup homepage
    _suppress_restore_nag(profile_dir, worker_id=worker_id)

    chrome_exe = config.get_chrome_path()

    vp = _pick_viewport()
    _worker_viewports[worker_id] = vp

    # Tile position for this worker (ignored when headless or minimized)
    tile_x, tile_y, tile_w, tile_h = compute_tile(worker_id, total_workers)

    cmd = [
        chrome_exe,
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=http://localhost",
        f"--user-data-dir={profile_dir}",
        "--profile-directory=Default",
        "--no-first-run",
        "--no-default-browser-check",
        f"--window-size={tile_w},{tile_h}",
        "--disable-session-crashed-bubble",
        "--disable-features=InfiniteSessionRestore,PasswordManagerOnboarding,"
        "SyncDisabledWithNoNetwork,ChromeSignin,Sync,"
        # Bypass XDG portal for file dialogs — portal routes through Nautilus which
        # hangs when the saved last-directory path doesn't exist on this machine.
        # GtkFileDialogPortal disables portal; FileSystemAccessAPI keeps upload working.
        "GtkFileDialogPortal",
        "--hide-crash-restore-bubble",
        "--noerrdialogs",
        "--password-store=basic",
        "--disable-save-password-bubble",
        "--disable-sync",
        "--disable-popup-blocking",
        # Use XWayland instead of native Wayland to avoid NVIDIA EGL black rendering
        "--ozone-platform=x11",
        # Block dangerous permissions at browser level
        "--deny-permission-prompts",
        "--disable-notifications",
        f"--user-agent={_get_real_user_agent()}",
        "--disable-infobars",
    ]

    # Load the per-worker ApplyPilot extension if it exists
    # Extension lives outside the user-data-dir (Chrome 75+ requirement)
    ext_path = config.CHROME_WORKER_DIR / "extensions" / f"worker-{worker_id}"
    if ext_path.exists() and not headless:
        cmd.append(f"--load-extension={ext_path}")

    if headless:
        cmd.append("--headless=new")
    elif minimized:
        cmd.append("--start-minimized")
    else:
        # Position the window in its designated tile slot
        cmd.append(f"--window-position={tile_x},{tile_y}")

    # On Unix, start in a new process group so we can kill the whole tree
    kwargs: dict = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if platform.system() != "Windows":
        import os

        kwargs["preexec_fn"] = os.setsid

    proc = subprocess.Popen(cmd, **kwargs)
    with _chrome_lock:
        _chrome_procs[worker_id] = proc

    # Give Chrome time to start and open the debug port
    time.sleep(3)
    logger.info("[worker-%d] Chrome started on port %d (pid %d)", worker_id, port, proc.pid)
    return proc


def cleanup_worker(worker_id: int, process: subprocess.Popen | None) -> None:
    """Kill a worker's Chrome instance and remove it from tracking.

    Args:
        worker_id: Numeric worker identifier.
        process: The Popen handle (or _AdoptedChromeProcess) from launch_chrome.
    """
    if process and process.poll() is None:
        if process.pid:
            _kill_process_tree(process.pid)
        else:
            # Adopted process with unknown PID — fall back to port-based kill
            _kill_on_port(BASE_CDP_PORT + worker_id)
    with _chrome_lock:
        _chrome_procs.pop(worker_id, None)
    logger.info("[worker-%d] Chrome cleaned up", worker_id)


def kill_all_chrome() -> None:
    """Kill all Chrome instances and any port zombies.

    Called during graceful shutdown to ensure no orphan Chrome processes.
    """
    with _chrome_lock:
        procs = dict(_chrome_procs)
        _chrome_procs.clear()

    for wid, proc in procs.items():
        if proc.poll() is None:
            if proc.pid:
                _kill_process_tree(proc.pid)
            else:
                _kill_on_port(BASE_CDP_PORT + wid)
        _kill_on_port(BASE_CDP_PORT + wid)

    # Sweep base port in case of zombies
    _kill_on_port(BASE_CDP_PORT)


def reset_worker_dir(worker_id: int) -> Path:
    """Wipe and recreate a worker's isolated working directory.

    Each job gets a fresh working directory so that file conflicts
    (resume PDFs, MCP configs) don't bleed between jobs.

    Args:
        worker_id: Numeric worker identifier.

    Returns:
        Path to the clean worker directory.
    """
    worker_dir = config.APPLY_WORKER_DIR / f"worker-{worker_id}"
    if worker_dir.exists():
        shutil.rmtree(str(worker_dir), ignore_errors=True)
    worker_dir.mkdir(parents=True, exist_ok=True)
    return worker_dir


def cleanup_on_exit() -> None:
    """Atexit handler: kill all Chrome processes and sweep CDP ports.

    Register this with atexit.register() at application startup.
    """
    with _chrome_lock:
        procs = dict(_chrome_procs)
        _chrome_procs.clear()

    for wid, proc in procs.items():
        if proc.poll() is None:
            _kill_process_tree(proc.pid)
        _kill_on_port(BASE_CDP_PORT + wid)

    # Sweep base port for any orphan
    _kill_on_port(BASE_CDP_PORT)
