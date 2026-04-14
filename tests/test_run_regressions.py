"""Regression tests for applypilot run bugs."""

import sqlite3
import threading

import pytest


class TestWorkdayOpenerInit:
    """Regression: _opener was undefined at module level, causing NameError on first API call."""

    def test_opener_is_defined(self):
        from applypilot.discovery.workday.api import _opener

        # Should be None (not NameError), meaning default urllib is used
        assert _opener is None

    def test_urlopen_works_without_setup_proxy(self):
        from applypilot.discovery.workday.api import _urlopen
        import urllib.request

        # Should not raise NameError — falls back to urllib.request.urlopen
        req = urllib.request.Request("https://httpbin.org/status/200")
        try:
            _urlopen(req, timeout=5)
        except Exception as e:
            # Network errors are fine — NameError is the bug
            assert not isinstance(e, NameError), f"_opener undefined: {e}"


class TestContainerThreadSafety:
    """Regression: Container repos used self._conn (main thread) in worker threads."""

    def test_repos_use_thread_local_connection(self):
        from applypilot.db.container import Container

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        c = Container(conn, auto_init=True)

        main_thread_id = threading.current_thread().ident
        errors = []

        def _worker():
            try:
                repo = c.job_repo
                # Should get a different connection than main thread
                # (won't crash with "SQLite objects created in a thread...")
                worker_conn_id = id(repo._conn)
                main_conn_id = id(conn)
                if worker_conn_id == main_conn_id:
                    errors.append("Worker got main thread's connection!")
            except Exception as e:
                errors.append(str(e))

        t = threading.Thread(target=_worker)
        t.start()
        t.join()

        assert errors == [], f"Thread safety issue: {errors}"


class TestInitDbSignature:
    """Regression: jobspy runner called init_db() without conn argument."""

    def test_init_db_requires_conn(self):
        from applypilot.db.schema import init_db

        with pytest.raises(TypeError):
            init_db()  # must fail — requires conn arg


class TestRunLogFileCreated:
    """Regression: run stages only logged to stdout, no file log."""

    def test_applypilot_logger_has_file_handler(self):
        import logging

        # Force the CLI module to load (triggers _configure_logging)
        import applypilot.cli  # noqa: F401

        run_logger = logging.getLogger("applypilot")
        file_handlers = [h for h in run_logger.handlers if isinstance(h, logging.FileHandler)]
        assert any("_run.log" in getattr(h, "baseFilename", "") for h in file_handlers), (
            f"No _run.log FileHandler on applypilot logger. Handlers: {run_logger.handlers}"
        )
