"""Tests for Chrome profile setup and Preferences patching.

Regression test for: extensions.settings being wiped by _suppress_restore_nag(),
which caused manually-installed extensions (ApplyPilot) to disappear on every
Chrome restart.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class TestSuppressRestoreNag(unittest.TestCase):
    """_suppress_restore_nag() must patch preferences without destroying extension state."""

    def _make_prefs(self, extra: dict | None = None) -> tuple[Path, Path]:
        """Write a minimal Preferences file to a temp dir. Returns (profile_dir, prefs_file)."""
        tmp = tempfile.mkdtemp()
        profile_dir = Path(tmp)
        default_dir = profile_dir / "Default"
        default_dir.mkdir()
        prefs = {
            "profile": {"exit_type": "Crashed"},
            "session": {"restore_on_startup": 1},
        }
        if extra:
            prefs.update(extra)
        prefs_file = default_dir / "Preferences"
        prefs_file.write_text(json.dumps(prefs), encoding="utf-8")
        return profile_dir, prefs_file

    def _read_prefs(self, prefs_file: Path) -> dict:
        return json.loads(prefs_file.read_text(encoding="utf-8"))

    def test_removes_in_profile_extension_entries(self):
        """Extension registration and cleanup:
        - Injects our extension (stable ID from manifest key) with correct worker path
        - Pins our extension (adds to pinned_extensions)
        - Removes stale entries: web-store, in-profile, source-dir, old path-computed IDs
        - Keeps Chrome built-ins (/opt/google/chrome/)
        """
        import tempfile
        from unittest.mock import patch

        APPLYPILOT_EXT_ID = "almfihgbaclbghnagbfecfpppmjfmlnp"
        OLD_SRC_EXT_ID = "eloakdpcfbnnadhnohionnmicpmedapk"

        tmp = Path(tempfile.mkdtemp())
        profile_dir = tmp
        default_dir = profile_dir / "Default"
        default_dir.mkdir()
        fake_worker_dir = tmp / "chrome-workers"
        expected_worker_path = str(fake_worker_dir / "extensions" / "worker-0")

        OLD_WRONG_KEY_ID = "lafmhibgcablhganbgeffcppmpfjlmpn"
        prefs = {
            "profile": {"exit_type": "Normal"},
            "session": {"restore_on_startup": 1},
            "extensions": {
                "pinned_extensions": [OLD_SRC_EXT_ID, "momentumextensionid00000000000"],
                "settings": {
                    "momentumextensionid00000000000": {
                        "path": str(default_dir / "Extensions" / "momentumid"),
                    },
                    OLD_SRC_EXT_ID: {
                        "path": "/home/user/Code/ApplyPilot/src/applypilot/apply/extension",
                    },
                    "chromebuiltinextensionid000000": {
                        "path": "/opt/google/chrome/resources/pdf",
                    },
                    # Old wrong key-derived ID pointing to worker ext dir (same as correct ID).
                    # The keep_prefix check would have spared this, so it needs explicit deletion.
                    OLD_WRONG_KEY_ID: {
                        "path": expected_worker_path,
                    },
                },
            },
        }
        prefs_file = default_dir / "Preferences"
        prefs_file.write_text(json.dumps(prefs), encoding="utf-8")

        from applypilot.apply.chrome import _suppress_restore_nag

        with patch("applypilot.config.CHROME_WORKER_DIR", fake_worker_dir):
            _suppress_restore_nag(profile_dir, worker_id=0)

        result = self._read_prefs(prefs_file)
        settings = result.get("extensions", {}).get("settings", {})
        pinned = result.get("extensions", {}).get("pinned_extensions", [])

        # Stale entries removed
        self.assertNotIn("momentumextensionid00000000000", settings, "In-profile web-store extension should be removed")
        self.assertNotIn(OLD_SRC_EXT_ID, settings, "Old source-dir extension entry should be removed")
        # Old wrong key-derived ID must be removed unconditionally from settings,
        # even when its path starts with the worker ext prefix (keep_prefix check
        # would have spared it, causing Chrome to refuse the correct ID — same dir).
        self.assertNotIn(
            "lafmhibgcablhganbgeffcppmpfjlmpn", settings, "Old wrong key-derived ID must be deleted from settings"
        )

        # Chrome built-in preserved
        self.assertIn("chromebuiltinextensionid000000", settings, "Chrome built-in should be preserved")

        # Our extension injected with correct path and enabled
        self.assertIn(APPLYPILOT_EXT_ID, settings, "ApplyPilot extension entry should be injected")
        self.assertEqual(
            settings[APPLYPILOT_EXT_ID]["path"],
            expected_worker_path,
            "Extension path should point to worker-specific dir",
        )
        self.assertEqual(settings[APPLYPILOT_EXT_ID]["disable_reasons"], 0, "Extension should have no disable reasons")
        # active_permissions must be present so Chrome doesn't treat the extension as
        # "not yet installed" (which causes ERR_BLOCKED_BY_CLIENT on popup.html)
        self.assertIn(
            "active_permissions",
            settings[APPLYPILOT_EXT_ID],
            "active_permissions must be injected to allow Chrome to load extension",
        )
        self.assertEqual(
            sorted(settings[APPLYPILOT_EXT_ID]["active_permissions"]["api"]),
            sorted(["activeTab", "alarms", "storage"]),
        )

        # Our extension pinned, old ID removed from pinned list
        self.assertIn(APPLYPILOT_EXT_ID, pinned, "ApplyPilot extension should be in pinned_extensions")
        self.assertNotIn(OLD_SRC_EXT_ID, pinned, "Old source-dir extension ID should be removed from pinned list")
        self.assertNotIn(
            "momentumextensionid00000000000", pinned, "Web-store extensions should be removed from pinned list"
        )

    def test_enables_developer_mode(self):
        """Developer mode is set to True so --load-extension works."""
        profile_dir, prefs_file = self._make_prefs()
        from applypilot.apply.chrome import _suppress_restore_nag

        _suppress_restore_nag(profile_dir)
        result = self._read_prefs(prefs_file)
        self.assertTrue(result["extensions"]["ui"]["developer_mode"])

    def test_sets_new_tab_on_startup_without_worker_id(self):
        """Without worker_id, restore_on_startup=5 (New Tab page)."""
        profile_dir, prefs_file = self._make_prefs()
        from applypilot.apply.chrome import _suppress_restore_nag

        _suppress_restore_nag(profile_dir)
        result = self._read_prefs(prefs_file)
        self.assertEqual(result["session"]["restore_on_startup"], 5)
        self.assertNotIn("startup_urls", result["session"])

    def test_sets_startup_url_with_worker_id(self):
        """With worker_id=2, restore_on_startup=4 and startup_urls points to the worker server."""
        profile_dir, prefs_file = self._make_prefs()
        from applypilot.apply.chrome import _suppress_restore_nag

        _suppress_restore_nag(profile_dir, worker_id=2)
        result = self._read_prefs(prefs_file)
        self.assertEqual(
            result["session"]["restore_on_startup"], 4, "Expected 4 (specific URLs) when worker_id is provided"
        )
        self.assertEqual(
            result["session"]["startup_urls"],
            ["http://localhost:7382/"],
            "startup_urls should point to port 7380+worker_id",
        )

    def test_suppresses_crash_restore(self):
        """exit_type is reset to Normal to prevent the restore-session nag."""
        profile_dir, prefs_file = self._make_prefs()
        from applypilot.apply.chrome import _suppress_restore_nag

        _suppress_restore_nag(profile_dir)
        result = self._read_prefs(prefs_file)
        self.assertEqual(result["profile"]["exit_type"], "Normal")

    def test_disables_sync(self):
        """Google sync is disabled to prevent extension contamination."""
        profile_dir, prefs_file = self._make_prefs()
        from applypilot.apply.chrome import _suppress_restore_nag

        _suppress_restore_nag(profile_dir)
        result = self._read_prefs(prefs_file)
        self.assertFalse(result["sync"]["requested"])
        self.assertFalse(result["signin"]["allowed"])

    def test_no_op_when_prefs_missing(self):
        """No exception when Preferences file doesn't exist yet."""
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        from applypilot.apply.chrome import _suppress_restore_nag

        _suppress_restore_nag(tmp)  # should not raise


if __name__ == "__main__":
    unittest.main(verbosity=2)
