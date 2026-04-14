"""Tests for the always-on per-worker HTTP server and Chrome focus utilities.

Covers:
- POST /api/done  — fires HITL event, response arrives without deadlock
- GET  /api/focus — calls CDP bringToFront + X11 raise, tracks last_focused
- GET  /api/status — returns all required fields including chromePid/lastFocused
- bring_to_foreground_cdp — sends Page.bringToFront over WebSocket
- _raise_x11_window — sends _NET_ACTIVE_WINDOW via libX11 ctypes
"""

import json
import sys
import threading
import time
import unittest
import urllib.request
import unittest.mock as mock
from pathlib import Path
from unittest.mock import MagicMock, patch, call

# Make sure the src package is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# Helpers: minimal in-process worker-server for endpoint tests
# ---------------------------------------------------------------------------


def _start_test_server(state: dict, hitl_event: threading.Event, worker_id: int = 0) -> int:
    """Start a real instance of _start_worker_listener on an OS-assigned port.

    Returns port.
    """
    from applypilot.apply import launcher as lmod

    # Reset global dicts to avoid cross-test pollution
    lmod._worker_state.clear()
    lmod._takeover_events.clear()
    lmod._handback_events.clear()
    lmod._worker_servers.clear()

    # Start listener first — it creates the internal state dict that the
    # handler closes over and stores in _worker_state[worker_id].
    port = lmod._start_worker_listener(worker_id)

    # Now update the handler's own state dict with our test values.
    # We must mutate the existing dict (not replace it) so the closure
    # in the handler still points to the same object.
    lmod._worker_state[worker_id].update(state)

    return port


# ---------------------------------------------------------------------------
# POST /api/done
# ---------------------------------------------------------------------------


class TestDoneEndpoint(unittest.TestCase):
    """POST /api/done fires the HITL event and returns 200 without deadlock."""

    def setUp(self):
        self.hitl_event = threading.Event()
        self.state = {
            "job": {"title": "Test Job", "site": "example", "fit_score": 9},
            "status": "waiting_human",
            "reason": "captcha",
            "instructions": "Solve the CAPTCHA.",
            "hitl_event": self.hitl_event,
            "hitl_job_hash": "abc123",
            "chrome_pid": None,
            "last_focused": 0,
            "handback_instructions": None,
            "mini_proc": None,
            "saved_instruction": None,
        }
        self.port = _start_test_server(self.state, self.hitl_event)

    def tearDown(self):
        from applypilot.apply import launcher as lmod

        lmod._stop_worker_listener(0)

    def test_done_returns_200(self):
        """POST /api/done responds with HTTP 200."""
        req = urllib.request.Request(
            f"http://localhost:{self.port}/api/done",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.read(), b"ok")

    def test_done_fires_hitl_event(self):
        """POST /api/done sets the hitl_event threading.Event."""
        self.assertFalse(self.hitl_event.is_set())
        req = urllib.request.Request(
            f"http://localhost:{self.port}/api/done",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5).close()
        self.assertTrue(self.hitl_event.is_set())

    def test_done_no_deadlock_repeated(self):
        """Multiple rapid POST /api/done calls must all receive responses."""
        for _ in range(3):
            req = urllib.request.Request(
                f"http://localhost:{self.port}/api/done",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                self.assertEqual(resp.status, 200)

    def test_done_no_hitl_event_still_ok(self):
        """POST /api/done returns 200 even when no hitl_event is registered."""
        self.state["hitl_event"] = None
        req = urllib.request.Request(
            f"http://localhost:{self.port}/api/done",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)


# ---------------------------------------------------------------------------
# GET /api/status
# ---------------------------------------------------------------------------


class TestStatusEndpoint(unittest.TestCase):
    """GET /api/status returns required fields."""

    def setUp(self):
        self.state = {
            "job": {"title": "Backend Engineer", "site": "linkedin", "fit_score": 8},
            "status": "waiting_human",
            "reason": "login_required",
            "instructions": "Log in.",
            "hitl_event": None,
            "hitl_job_hash": None,
            "chrome_pid": 12345,
            "last_focused": 1700000000.0,
            "handback_instructions": None,
            "mini_proc": None,
            "saved_instruction": None,
        }
        self.port = _start_test_server(self.state, threading.Event())

    def tearDown(self):
        from applypilot.apply import launcher as lmod

        lmod._stop_worker_listener(0)

    def _get_status(self) -> dict:
        with urllib.request.urlopen(f"http://localhost:{self.port}/api/status", timeout=5) as resp:
            return json.loads(resp.read())

    def test_required_fields_present(self):
        data = self._get_status()
        for field in (
                "workerId",
                "status",
                "jobTitle",
                "jobSite",
                "score",
                "reason",
                "instructions",
                "chromePid",
                "lastFocused",
        ):
            self.assertIn(field, data, f"Missing field: {field}")

    def test_chrome_pid_returned(self):
        data = self._get_status()
        self.assertEqual(data["chromePid"], 12345)

    def test_last_focused_returned(self):
        data = self._get_status()
        self.assertAlmostEqual(data["lastFocused"], 1700000000.0, places=0)

    def test_worker_id_correct(self):
        data = self._get_status()
        self.assertEqual(data["workerId"], 0)

    def test_status_reflects_state(self):
        data = self._get_status()
        self.assertEqual(data["status"], "waiting_human")
        self.assertEqual(data["reason"], "login_required")


# ---------------------------------------------------------------------------
# GET /api/focus
# ---------------------------------------------------------------------------


class TestFocusEndpoint(unittest.TestCase):
    """GET /api/focus calls CDP bringToFront + X11 raise and tracks last_focused."""

    def setUp(self):
        self.state = {
            "job": {"title": "SWE", "site": "builtin", "fit_score": 9},
            "status": "waiting_human",
            "reason": "captcha",
            "instructions": "Solve CAPTCHA.",
            "hitl_event": None,
            "hitl_job_hash": None,
            "chrome_pid": 99999,
            "last_focused": 0,
            "handback_instructions": None,
            "mini_proc": None,
            "saved_instruction": None,
        }
        self.port = _start_test_server(self.state, threading.Event())

    def tearDown(self):
        from applypilot.apply import launcher as lmod

        lmod._stop_worker_listener(0)

    def test_focus_returns_200(self):
        with urllib.request.urlopen(f"http://localhost:{self.port}/api/focus", timeout=5) as resp:
            self.assertEqual(resp.status, 200)

    def test_focus_updates_last_focused(self):
        from applypilot.apply import launcher as lmod

        before = time.time()
        urllib.request.urlopen(f"http://localhost:{self.port}/api/focus", timeout=5).close()
        after = time.time()
        # Read from the handler's internal state dict (not self.state, which is
        # a separate dict that was .update()-ed into the handler's state).
        lf = lmod._worker_state[0].get("last_focused", 0)
        self.assertGreaterEqual(lf, before - 1)
        self.assertLessEqual(lf, after + 1)

    def test_focus_calls_cdp_and_pid(self):
        """_handle_focus calls both bring_to_foreground_cdp and bring_to_foreground_pid."""
        with (
            patch("applypilot.apply.chrome.bring_to_foreground_cdp", return_value=True) as mock_cdp,
            patch("applypilot.apply.chrome.bring_to_foreground_pid") as mock_pid,
        ):
            urllib.request.urlopen(f"http://localhost:{self.port}/api/focus", timeout=5).close()
        mock_cdp.assert_called_once()
        mock_pid.assert_called_once_with(99999)

    def test_focus_status_reflects_last_focused(self):
        """After /api/focus, /api/status returns the updated lastFocused."""
        urllib.request.urlopen(f"http://localhost:{self.port}/api/focus", timeout=5).close()
        with urllib.request.urlopen(f"http://localhost:{self.port}/api/status", timeout=5) as resp:
            data = json.loads(resp.read())
        self.assertGreater(data["lastFocused"], 0)


# ---------------------------------------------------------------------------
# bring_to_foreground_cdp
# ---------------------------------------------------------------------------


class TestBringToForegroundCdp(unittest.TestCase):
    """bring_to_foreground_cdp sends Page.bringToFront over WebSocket."""

    def test_sends_bringttofront(self):
        """Sends Page.bringToFront to the first page tab's WebSocket URL."""
        fake_targets = json.dumps(
            [
                {"type": "page", "webSocketDebuggerUrl": "ws://localhost:9999/devtools/page/X"},
            ]
        ).encode()

        mock_ws = MagicMock()
        mock_ws.recv.return_value = json.dumps({"id": 1, "result": {}})

        with (
            patch("urllib.request.urlopen") as mock_urlopen,
            patch("websocket.WebSocket", return_value=mock_ws),
        ):
            mock_urlopen.return_value.__enter__ = lambda s: s
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value.read.return_value = fake_targets

            from applypilot.apply.chrome import bring_to_foreground_cdp

            result = bring_to_foreground_cdp(9999)

        self.assertTrue(result)
        sent = json.loads(mock_ws.send.call_args[0][0])
        self.assertEqual(sent["method"], "Page.bringToFront")

    def test_returns_false_when_no_tabs(self):
        """Returns False when Chrome has no page tabs."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = lambda s: s
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value.read.return_value = json.dumps([]).encode()

            from applypilot.apply.chrome import bring_to_foreground_cdp

            result = bring_to_foreground_cdp(9998)

        self.assertFalse(result)

    def test_returns_false_on_connection_error(self):
        """Returns False when Chrome's CDP port is unreachable."""
        from urllib.error import URLError

        with patch("urllib.request.urlopen", side_effect=URLError("refused")):
            from applypilot.apply.chrome import bring_to_foreground_cdp

            result = bring_to_foreground_cdp(9997)
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# _raise_x11_window
# ---------------------------------------------------------------------------


class TestRaiseX11Window(unittest.TestCase):
    """_raise_x11_window sends _NET_ACTIVE_WINDOW via libX11 ctypes."""

    def test_returns_false_for_no_pid(self):
        from applypilot.apply.chrome import _raise_x11_window

        self.assertFalse(_raise_x11_window(0))
        self.assertFalse(_raise_x11_window(None))

    def test_returns_false_when_libx11_unavailable(self):
        """Returns False gracefully when libX11.so.6 can't be loaded."""
        import ctypes

        with patch("ctypes.CDLL", side_effect=OSError("libX11.so.6: not found")):
            from applypilot.apply import chrome as chrome_mod

            # Reload to pick up patched CDLL
            result = chrome_mod._raise_x11_window(12345)
        self.assertFalse(result)

    def test_sends_net_active_window(self):
        """Sends _NET_ACTIVE_WINDOW ClientMessage when window is found."""
        # Build a minimal ctypes mock that returns a window with the target PID
        import ctypes

        # We'll simulate: 1 client window, its _NET_WM_PID == target PID
        TARGET_PID = 42000
        TARGET_WIN = 0xCAFE

        call_count = {"xget": 0}

        def fake_XGetWindowProperty(
                dpy, win, atom, offset, length, delete, req_type, atype_out, afmt_out, nitems_out, bafter_out, data_out
        ):
            call_count["xget"] += 1
            if call_count["xget"] == 1:
                # _NET_CLIENT_LIST: one window (TARGET_WIN)
                arr = (ctypes.c_ulong * 1)(TARGET_WIN)
                atype_out._obj.value = 33  # XA_WINDOW
                afmt_out._obj.value = 32
                nitems_out._obj.value = 1
                bafter_out._obj.value = 0
                data_out._obj.value = ctypes.cast(arr, ctypes.c_void_p).value
            else:
                # _NET_WM_PID for TARGET_WIN
                arr = (ctypes.c_ulong * 1)(TARGET_PID)
                atype_out._obj.value = 6  # XA_CARDINAL
                afmt_out._obj.value = 32
                nitems_out._obj.value = 1
                bafter_out._obj.value = 0
                data_out._obj.value = ctypes.cast(arr, ctypes.c_void_p).value
            return 0  # Success

        # Use a real but minimal mock for the X11 lib
        mock_x11 = MagicMock()
        mock_x11.XOpenDisplay.return_value = 1  # non-null display
        mock_x11.XDefaultRootWindow.return_value = 0x1000
        mock_x11.XInternAtom.side_effect = lambda dpy, name, _: {
            b"_NET_CLIENT_LIST": 100,
            b"_NET_WM_PID": 101,
            b"_NET_ACTIVE_WINDOW": 102,
        }.get(name, 0)
        mock_x11.XGetWindowProperty.side_effect = fake_XGetWindowProperty
        mock_x11.XFree = MagicMock()
        mock_x11.XSendEvent = MagicMock(return_value=1)
        mock_x11.XFlush = MagicMock()
        mock_x11.XCloseDisplay = MagicMock()

        with patch("ctypes.CDLL", return_value=mock_x11):
            from applypilot.apply import chrome as chrome_mod

            result = chrome_mod._raise_x11_window(TARGET_PID)

        self.assertTrue(result)
        mock_x11.XSendEvent.assert_called_once()
        mock_x11.XFlush.assert_called_once()


# ---------------------------------------------------------------------------
# bring_to_foreground_pid fallback chain
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    unittest.main(verbosity=2)
