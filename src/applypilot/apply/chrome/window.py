"""Window — extracted from chrome/__init__.py."""

from __future__ import annotations

import glob as _glob
import logging
import platform
import random
import subprocess

logger = logging.getLogger(__name__)

import json

# Module-level state for worker viewport assignments
_worker_viewports: dict[int, tuple[int, int]] = {}
_PANEL_H = 28  # macOS menu bar / Windows taskbar height
_VIEWPORT_POOL = [
    (1280, 800),
    (1366, 768),
    (1440, 900),
    (1536, 864),
    (1920, 1080),
]

try:
    import websocket as _websocket
except ModuleNotFoundError:
    import types as _types

    _websocket = _types.SimpleNamespace(WebSocket=type("_Missing", (), {"__init__": lambda *a, **k: None}))


def _get_screen_size() -> tuple[int, int]:
    """Return (width, height) of the total desktop area across all monitors.

    Linux: reads via GTK/GDK monitor geometry — works correctly under XWayland
    where the deprecated Screen.get_width/height() returns logical/scaled sizes.
    macOS / Windows: falls back to a reasonable default.
    """
    if platform.system() == "Linux":
        try:
            import gi  # type: ignore

            gi.require_version("Gdk", "3.0")
            from gi.repository import Gdk  # type: ignore

            d = Gdk.Display.get_default()
            max_w, max_h = 0, 0
            for i in range(d.get_n_monitors()):
                m = d.get_monitor(i)
                g = m.get_geometry()
                max_w = max(max_w, g.x + g.width)
                max_h = max(max_h, g.y + g.height)
            if max_w > 0 and max_h > 0:
                return max_w, max_h
        except Exception:
            pass
    return 1920, 1080  # safe fallback


def compute_tile(worker_id: int, total_workers: int) -> tuple[int, int, int, int]:
    """Return (x, y, width, height) for a tiled Chrome window.

    Layout on a 2560×1440 screen (panel_h = 36):
      1 worker : left ~62 % of screen, full height (leaves room for terminal)
      2 workers: side-by-side half-width columns, full height
      3 workers: 2×2 grid, bottom-right quadrant left empty for terminal
      4 workers: 2×2 grid, all quadrants filled

    On other OSes the same math applies using the detected screen size.
    """
    sw, sh = _get_screen_size()
    top = _PANEL_H
    usable_h = sh - top

    if total_workers == 1:
        # Leave the right third free for the terminal
        w = int(sw * 0.62)
        return 0, top, w, usable_h

    if total_workers == 2:
        w = sw // 2
        x = worker_id * w
        return x, top, w, usable_h

    # 3–4 workers: 2×2 grid
    col = worker_id % 2
    row = worker_id // 2
    w = sw // 2
    h = usable_h // 2
    return col * w, top + row * h, w, h


def prevent_focus_stealing() -> str | None:
    """Stop new windows from stealing keyboard focus (Linux/GNOME only).

    Sets org.gnome.desktop.wm.preferences focus-new-windows to 'strict'
    so Chrome windows launched by workers don't interrupt the user.
    Returns the previous value so it can be restored later.

    On non-Linux or when gsettings is unavailable, returns None (no-op).

    macOS note: System Preferences > Mission Control > "When switching to an
    application, switch to a Space with open windows" controls similar
    behavior; no programmatic API exists.
    Windows note: no equivalent; Chrome launch flags can request no-activate
    but this is not reliable.
    """
    if platform.system() != "Linux":
        return None
    try:
        prev = (
            subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.wm.preferences", "focus-new-windows"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            .stdout.strip()
            .strip("'")
        )
        subprocess.run(
            ["gsettings", "set", "org.gnome.desktop.wm.preferences", "focus-new-windows", "strict"],
            check=True,
            timeout=3,
        )
        logger.info("Focus-steal prevention: set focus-new-windows=strict (was '%s')", prev)
        return prev
    except Exception as e:
        logger.debug("prevent_focus_stealing: %s", e)
        return None


def restore_focus_mode(prev: str | None) -> None:
    """Restore the focus-new-windows setting saved by prevent_focus_stealing()."""
    if not prev or platform.system() != "Linux":
        return
    try:
        subprocess.run(
            ["gsettings", "set", "org.gnome.desktop.wm.preferences", "focus-new-windows", prev],
            check=True,
            timeout=3,
        )
        logger.info("Focus mode restored to '%s'", prev)
    except Exception as e:
        logger.debug("restore_focus_mode: %s", e)


def _pick_viewport() -> tuple[int, int]:
    """Pick a random viewport from the pool."""
    return random.choice(_VIEWPORT_POOL)


def get_worker_viewport(worker_id: int) -> tuple[int, int]:
    """Return the stored viewport for a worker, or (1280, 800) fallback."""
    return _worker_viewports.get(worker_id, (1280, 800))


def bring_to_foreground() -> None:
    """Attempt to bring Chrome to the foreground (best-effort).

    Tries wmctrl then xdotool on Linux; AppleScript on macOS.
    Fails silently — this is UI polish, not critical.
    """
    try:
        if platform.system() == "Darwin":
            subprocess.run(
                ["osascript", "-e", 'tell application "Google Chrome" to activate'],
                timeout=3,
                capture_output=True,
            )
        else:
            # Try wmctrl first (more reliable for window managers)
            result = subprocess.run(
                ["wmctrl", "-a", "Chrome"],
                timeout=3,
                capture_output=True,
            )
            if result.returncode != 0:
                subprocess.run(
                    ["xdotool", "search", "--name", "Chrome", "windowactivate", "--sync"],
                    timeout=3,
                    capture_output=True,
                )
    except Exception:
        pass  # Best-effort only


def bring_to_foreground_cdp(cdp_port: int) -> bool:
    """Bring a Chrome window to the foreground via CDP Page.bringToFront.

    Uses websocket-client to send a CDP command directly to Chrome.
    Works on Wayland, X11, and macOS — no external tools required.

    Returns True on success, False if Chrome isn't reachable.
    """
    from urllib.request import urlopen
    from urllib.error import URLError

    try:
        with urlopen(f"http://localhost:{cdp_port}/json", timeout=2) as r:
            targets = json.loads(r.read())
    except (URLError, OSError, Exception):
        return False

    ws_url = next(
        (t.get("webSocketDebuggerUrl") for t in targets if t.get("type") == "page"),
        None,
    )
    if not ws_url:
        return False

    try:
        ws = _websocket.WebSocket()
        ws.connect(ws_url, timeout=3, origin="http://localhost")
        ws.send(json.dumps({"id": 1, "method": "Page.bringToFront"}))
        ws.recv()
        ws.close()
        return True
    except Exception:
        return False


def _raise_x11_window(pid: int) -> bool:
    """Raise an X11/XWayland window belonging to a PID via libX11 ctypes.

    Sends a _NET_ACTIVE_WINDOW ClientMessage to the root window, which asks
    the compositor (Mutter, KWin, etc.) to raise and focus the window.
    Works without xdotool or wmctrl installed.

    Returns True on success, False if libX11 is unavailable or the window
    can't be found.
    """
    if not pid:
        return False
    try:
        import ctypes

        X11 = ctypes.CDLL("libX11.so.6")
        X11.XOpenDisplay.restype = ctypes.c_void_p
        X11.XDefaultRootWindow.restype = ctypes.c_ulong
        X11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        X11.XInternAtom.restype = ctypes.c_ulong
        X11.XGetWindowProperty.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_long,
            ctypes.c_long,
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        X11.XFree.argtypes = [ctypes.c_void_p]
        X11.XSendEvent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_long,
            ctypes.c_void_p,
        ]
        X11.XFlush.argtypes = [ctypes.c_void_p]
        X11.XCloseDisplay.argtypes = [ctypes.c_void_p]

        dpy = X11.XOpenDisplay(None)
        if not dpy:
            return False
        try:
            root = X11.XDefaultRootWindow(dpy)
            XA_WINDOW = ctypes.c_ulong(33)
            XA_CARDINAL = ctypes.c_ulong(6)
            NET_CLIENT_LIST = X11.XInternAtom(dpy, b"_NET_CLIENT_LIST", 0)
            NET_WM_PID = X11.XInternAtom(dpy, b"_NET_WM_PID", 0)
            NET_ACTIVE_WIN = X11.XInternAtom(dpy, b"_NET_ACTIVE_WINDOW", 0)

            atype = ctypes.c_ulong()
            afmt = ctypes.c_int()
            nitems = ctypes.c_ulong()
            bafter = ctypes.c_ulong()
            data = ctypes.c_void_p()

            # Fetch all managed windows
            X11.XGetWindowProperty(
                dpy,
                root,
                NET_CLIENT_LIST,
                0,
                0x7FFFFFFF,
                0,
                XA_WINDOW,
                ctypes.byref(atype),
                ctypes.byref(afmt),
                ctypes.byref(nitems),
                ctypes.byref(bafter),
                ctypes.byref(data),
            )
            wins = list(ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))[: nitems.value])
            X11.XFree(data)

            # Find the first window belonging to this PID
            target = None
            for win in wins:
                X11.XGetWindowProperty(
                    dpy,
                    win,
                    NET_WM_PID,
                    0,
                    1,
                    0,
                    XA_CARDINAL,
                    ctypes.byref(atype),
                    ctypes.byref(afmt),
                    ctypes.byref(nitems),
                    ctypes.byref(bafter),
                    ctypes.byref(data),
                )
                if atype.value == XA_CARDINAL.value and nitems.value:
                    win_pid = ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))[0]
                    X11.XFree(data)
                    if win_pid == pid:
                        target = win
                        break
                else:
                    X11.XFree(data)

            if not target:
                return False

            # Send _NET_ACTIVE_WINDOW ClientMessage to root
            class _EvData(ctypes.Union):
                _fields_ = [("l", ctypes.c_long * 5), ("b", ctypes.c_char * 20)]

            class _XClientMsg(ctypes.Structure):
                _fields_ = [
                    ("type", ctypes.c_int),
                    ("serial", ctypes.c_ulong),
                    ("send_event", ctypes.c_int),
                    ("display", ctypes.c_void_p),
                    ("window", ctypes.c_ulong),
                    ("message_type", ctypes.c_ulong),
                    ("format", ctypes.c_int),
                    ("data", _EvData),
                ]

            ev = _XClientMsg()
            ev.type = 33  # ClientMessage
            ev.window = target
            ev.message_type = NET_ACTIVE_WIN
            ev.format = 32
            ev.data.l[0] = 2  # source = application
            ev.data.l[1] = 0  # timestamp (0 = current)
            ev.data.l[2] = 0  # currently active window

            # SubstructureRedirectMask | SubstructureNotifyMask
            X11.XSendEvent(dpy, root, 0, (1 << 20) | (1 << 19), ctypes.byref(ev))
            X11.XFlush(dpy)
            return True
        finally:
            X11.XCloseDisplay(dpy)
    except Exception:
        logger.debug("X11 window raise failed", exc_info=True)
        return False


def bring_to_foreground_pid(pid: int) -> None:
    """Bring a Chrome window to the foreground by PID.

    Tries (in order): xdotool, wmctrl, X11 ctypes (_NET_ACTIVE_WINDOW),
    generic bring_to_foreground(). Best-effort — fails silently.
    """
    if not pid:
        bring_to_foreground()
        return
    try:
        if platform.system() == "Darwin":
            result = subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'tell application "System Events" to set frontmost of '
                    f"(first process whose unix id is {pid}) to true",
                ],
                timeout=3,
                capture_output=True,
            )
            if result.returncode == 0:
                return
        # xdotool
        result = subprocess.run(
            ["xdotool", "search", "--pid", str(pid), "windowactivate", "--sync"],
            timeout=3,
            capture_output=True,
        )
        if result.returncode == 0:
            return
        # wmctrl
        lp = subprocess.run(
            ["wmctrl", "-l", "-p"],
            timeout=3,
            capture_output=True,
            text=True,
        )
        if lp.returncode == 0:
            for line in lp.stdout.splitlines():
                parts = line.split(None, 4)
                if len(parts) >= 3 and parts[2] == str(pid):
                    subprocess.run(
                        ["wmctrl", "-i", "-a", parts[0]],
                        timeout=3,
                        capture_output=True,
                    )
                    return
        # X11 ctypes — works on X11/XWayland without external tools
        if _raise_x11_window(pid):
            return
        bring_to_foreground()
    except Exception:
        pass  # Best-effort only


def _find_chrome_pid_for_port(port: int) -> int | None:
    """Find the PID of a Chrome/Chromium process listening on the given CDP port.

    Scans /proc/*/cmdline (Linux only). Returns None on non-Linux or if not found.
    """
    target = f"--remote-debugging-port={port}"
    for path in _glob.glob("/proc/*/cmdline"):
        try:
            with open(path, "rb") as fh:
                cmdline = fh.read().decode("utf-8", errors="replace").replace("\x00", " ")
            if target in cmdline and ("chrome" in cmdline or "chromium" in cmdline):
                return int(path.split("/")[2])
        except (OSError, ValueError, IndexError):
            pass
    return None
