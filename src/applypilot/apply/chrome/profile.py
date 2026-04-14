"""Profile — extracted from chrome/__init__.py."""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess
from pathlib import Path

from applypilot import config

logger = logging.getLogger(__name__)

_TOP_LEVEL_FILES = ["Cookies", "Cookies-journal", "Login Data", "Login Data-journal", "Web Data"]
_DEFAULT_FILES = [
    "Cookies",
    "Cookies-journal",
    "Login Data",
    "Login Data-journal",
    "Web Data",
    "Preferences",
    "Secure Preferences",
]
_DEFAULT_DIRS = ["Local Storage", "IndexedDB", "Session Storage", "Service Worker"]

import json


def _copy_auth_files(source: Path, dest: Path) -> int:
    """Copy only auth-essential files from a Chrome profile.

    Uses a whitelist approach: only copies cookies, login data, local storage,
    and preferences. Skips session/tab state, history, caches, and everything
    else that makes profiles huge.

    Returns:
        Number of files/dirs successfully copied.
    """
    copied = 0
    dest.mkdir(parents=True, exist_ok=True)

    # Top-level files
    for fname in _TOP_LEVEL_FILES:
        src = source / fname
        if src.exists():
            try:
                shutil.copy2(str(src), str(dest / fname))
                copied += 1
            except (PermissionError, OSError):
                pass

    # Default/ directory
    src_default = source / "Default"
    dst_default = dest / "Default"
    if not src_default.exists():
        return copied
    dst_default.mkdir(parents=True, exist_ok=True)

    # Individual files in Default/
    for fname in _DEFAULT_FILES:
        src = src_default / fname
        if src.exists():
            try:
                shutil.copy2(str(src), str(dst_default / fname))
                copied += 1
            except (PermissionError, OSError):
                pass

    # Directories in Default/ (local storage, IndexedDB, etc.)
    for dname in _DEFAULT_DIRS:
        src = src_default / dname
        if src.is_dir():
            try:
                shutil.copytree(
                    str(src),
                    str(dst_default / dname),
                    dirs_exist_ok=True,
                )
                copied += 1
            except (PermissionError, OSError):
                pass

    return copied


def _refresh_session_files(profile_dir: Path) -> None:
    """Re-copy auth files from the user's real Chrome profile.

    Updates Cookies, Login Data, Web Data, and Local Storage in the
    worker's profile so that expired sessions get refreshed without
    wiping the entire worker profile.
    """
    source = config.get_chrome_user_data()
    count = _copy_auth_files(source, profile_dir)
    if count:
        logger.info("Refreshed %d auth files in worker profile", count)


def _init_clean_profile(profile_dir: Path) -> None:
    """Create a minimal, clean Chrome profile with no real-browser data.

    Writes only the bare Preferences needed for Chrome to start without a
    restore-nag. No cookies, no login data, no sync tokens — nothing copied
    from the user's real browser.
    """
    default_dir = profile_dir / "Default"
    default_dir.mkdir(parents=True, exist_ok=True)

    minimal_prefs: dict = {}
    prefs_file = default_dir / "Preferences"
    prefs_file.write_text(json.dumps(minimal_prefs), encoding="utf-8")


def setup_worker_profile(worker_id: int, refresh_cookies: bool = False, ats_slug: str | None = None) -> Path:
    """Create an isolated Chrome profile for a worker.

    Profiles are completely clean — no data is copied from the user's real
    Chrome browser. This prevents Google sync, extension contamination, and
    unwanted password autofill triggered by real-browser sessions.

    ATS sessions saved by a previous successful apply (via save_ats_session)
    are overlaid on top so the worker can reuse prior authentication.

    Args:
        worker_id: Numeric worker identifier.
        refresh_cookies: Ignored (kept for API compatibility). Workers never
            copy from the real browser, so there is nothing to refresh.
        ats_slug: Optional ATS platform slug for session overlay.

    Returns:
        Path to the worker's Chrome user-data directory.
    """
    profile_dir = config.CHROME_WORKER_DIR / f"worker-{worker_id}"
    prefs_file = profile_dir / "Default" / "Preferences"
    if not prefs_file.exists():
        logger.info("[worker-%d] Creating clean isolated profile", worker_id)
        _init_clean_profile(profile_dir)

    # Wipe the entire in-profile Extensions directory.
    # Our extension is loaded via --load-extension (outside the user-data-dir),
    # so nothing in Extensions/ is needed.  Wiping it prevents Chrome from
    # re-loading or re-downloading legacy extensions (Momentum, Tampermonkey,
    # etc.) that may have accumulated before the clean-profile code was added.
    ext_in_profile = profile_dir / "Default" / "Extensions"
    if ext_in_profile.is_dir():
        shutil.rmtree(str(ext_in_profile), ignore_errors=True)
        ext_in_profile.mkdir(parents=True, exist_ok=True)
        logger.debug("[worker-%d] Wiped in-profile Extensions directory", worker_id)

    # Overlay ATS session if available
    if ats_slug:
        from applypilot.apply.chrome.session import get_ats_session_path

        session_dir = get_ats_session_path(ats_slug)
        if (session_dir / "Default").exists():
            overlay_count = _copy_auth_files(session_dir, profile_dir)
            logger.info("[worker-%d] Overlaid %d auth files from %s session", worker_id, overlay_count, ats_slug)

    # Deploy per-worker extension (copy source + inject per-worker config)
    # NOTE: Extension must live OUTSIDE the user-data-dir — Chrome 75+ ignores
    # --load-extension paths that are inside the user-data-dir.
    ext_src = Path(__file__).parent / "extension"
    if ext_src.exists():
        ext_dst = config.CHROME_WORKER_DIR / "extensions" / f"worker-{worker_id}"
        try:
            shutil.copytree(str(ext_src), str(ext_dst), dirs_exist_ok=True)
            server_port = 7380 + worker_id
            config_js = (
                f"// Per-worker config — generated by setup_worker_profile()\n"
                f"// DO NOT EDIT — regenerated each time the worker profile is set up\n"
                f"globalThis.WORKER_CONFIG = {{workerId: {worker_id}, serverPort: {server_port}}};\n"
            )
            # config.js: used by popup.html (loaded as a regular <script> tag)
            (ext_dst / "config.js").write_text(config_js, encoding="utf-8")
            # Prepend config inline to background.js (classic SW — no module import needed)
            bg_src = (ext_dst / "background.js").read_text(encoding="utf-8")
            (ext_dst / "background.js").write_text(config_js + bg_src, encoding="utf-8")
            logger.debug("[worker-%d] Extension deployed to %s", worker_id, ext_dst)
        except (OSError, shutil.Error) as e:
            logger.warning("[worker-%d] Extension deploy failed (non-fatal): %s", worker_id, e)

    return profile_dir


def _remove_singleton_locks(profile_dir: Path) -> None:
    """Delete Chrome singleton lock files so Chrome can start cleanly.

    When a Chrome profile is copied from another machine (or left over from
    a crashed session), it contains SingletonLock/SingletonSocket/SingletonCookie
    files. Chrome sees these and refuses to open the debug port, showing a
    "profile in use by another computer" dialog instead.
    """
    _LOCKS = ("SingletonLock", "SingletonSocket", "SingletonCookie")
    for fname in _LOCKS:
        lock = profile_dir / fname
        if lock.exists():
            try:
                lock.unlink()
                logger.debug("Removed stale lock: %s", lock)
            except OSError:
                pass


def _suppress_restore_nag(profile_dir: Path, worker_id: int | None = None) -> None:
    """Patch Chrome Preferences before launch.

    Handles three things:
    1. Suppress the 'restore pages?' nag (Chrome marks exit_type=Crashed on kill).
    2. Disable Google account sync and sign-in so the worker profile stays isolated —
       without this, Chrome re-authenticates from copied session cookies and starts
       pulling in synced extensions, passwords, and settings from the user's real profile.
    3. Disable all password saving and autofill so Chrome doesn't interfere with
       the agent's form-filling (wrong saved credentials, unwanted popups).
    """
    prefs_file = profile_dir / "Default" / "Preferences"
    if not prefs_file.exists():
        return

    try:
        prefs = json.loads(prefs_file.read_text(encoding="utf-8"))

        # --- 1. Suppress restore nag ---
        prefs.setdefault("profile", {})["exit_type"] = "Normal"
        if worker_id is not None:
            # Open the worker's status homepage on startup (served by the always-on
            # HTTP server at port 7380+worker_id, which starts before Chrome launches).
            server_port = 7380 + worker_id
            prefs.setdefault("session", {})["restore_on_startup"] = 4  # 4 = open specific URLs
            prefs.setdefault("session", {})["startup_urls"] = [f"http://localhost:{server_port}/"]
        else:
            prefs.setdefault("session", {})["restore_on_startup"] = 5  # 5 = New Tab page
            prefs.setdefault("session", {}).pop("startup_urls", None)

        # --- 2. Disable Google sync and sign-in ---
        # Prevents Chrome from re-syncing extensions/passwords from the user's
        # real Google profile when the copied session cookies re-authenticate.
        prefs.setdefault("sync", {}).update(
            {
                "requested": False,
                "has_setup_completed": False,
                "suppress_start": True,
                "keep_everything_synced": False,
            }
        )
        prefs.setdefault("signin", {}).update(
            {
                "allowed": False,
                "allowed_on_next_startup": False,
            }
        )

        # --- 3. Disable password manager and all autofill ---
        prefs["credentials_enable_service"] = False
        prefs["credentials_enable_autosign"] = False
        prefs.setdefault("profile", {})["password_manager_enabled"] = False
        prefs.setdefault("password_manager", {}).update(
            {
                "saving_enabled": False,
                "autosignin_enabled": False,
            }
        )
        prefs.setdefault("autofill", {}).update(
            {
                "enabled": False,
                "credit_card_enabled": False,
                "profile_enabled": False,
            }
        )

        # --- 4. Extension registration and pinning ---
        # APPLYPILOT_EXT_ID is derived from the RSA key in manifest.json ("key" field).
        # sha256(base64decode(key))[:16bytes] mapped nibble→a-p.
        # Verified: base64.b64decode(key) | sha256 | first 32 hex nibbles → a-p
        APPLYPILOT_EXT_ID = "almfihgbaclbghnagbfecfpppmjfmlnp"

        ext_dir = prefs.setdefault("extensions", {})

        # Enable developer mode so --load-extension works
        ext_dir.setdefault("ui", {})["developer_mode"] = True

        # Whitelist-clean extensions.settings: keep only Chrome built-ins,
        # our known ApplyPilot extension ID, and worker-specific ext dirs.
        # Removes: web-store extensions, stale source-dir entries, relative paths.
        ext_settings = ext_dir.setdefault("settings", {})
        worker_ext_prefix = str(config.CHROME_WORKER_DIR / "extensions")
        keep_prefixes = ("/opt/google/chrome/", worker_ext_prefix)
        stale = [
            k
            for k, v in ext_settings.items()
            if k != APPLYPILOT_EXT_ID and not any(str(v.get("path", "")).startswith(p) for p in keep_prefixes)
        ]
        for k in stale:
            del ext_settings[k]

        # Inject/update the ApplyPilot extension entry so Chrome loads it from
        # the correct worker-specific dir with developer-mode trust.
        if worker_id is not None:
            worker_ext_path = str(config.CHROME_WORKER_DIR / "extensions" / f"worker-{worker_id}")
            entry = ext_settings.get(APPLYPILOT_EXT_ID, {})
            entry.update(
                {
                    "path": worker_ext_path,
                    "location": 4,  # COMMAND_LINE — trusted, no update check
                    "from_webstore": False,
                    "disable_reasons": 0,  # no disable reasons → extension stays enabled
                    # active_permissions must be present or Chrome treats the extension as
                    # "not yet installed" and blocks access to its resources (ERR_BLOCKED_BY_CLIENT).
                    # These values must exactly match manifest.json permissions/host_permissions.
                    "active_permissions": {
                        "api": ["activeTab", "alarms", "storage"],
                        "explicit_host": ["http://localhost/*"],
                        "manifest_permissions": [],
                        "scriptable_host": [],
                    },
                }
            )
            ext_settings[APPLYPILOT_EXT_ID] = entry

        # Ensure ApplyPilot is pinned (visible in toolbar, not hidden behind puzzle icon).
        # Also forcibly delete known bad IDs from ext_settings — these IDs can point to the
        # same directory as the correct ID, causing Chrome to refuse to load the correct one
        # (Chrome won't load the same extension directory twice under different IDs).
        _EXTRA_STALE = {
            "eloakdpcfbnnadhnohionnmicpmedapk",  # old path-derived source-dir ID (no key)
            "lafmhibgcablhganbgeffcppmpfjlmpn",  # previously computed wrong key-derived ID
        }
        # Delete from settings unconditionally (the keep_prefix check would have spared these
        # since they pointed to the worker ext dir, causing the duplicate-dir loading bug).
        for bad_id in _EXTRA_STALE:
            ext_settings.pop(bad_id, None)
        remove_from_pinned = set(stale) | _EXTRA_STALE
        pinned = [p for p in ext_dir.get("pinned_extensions", []) if p not in remove_from_pinned]
        if APPLYPILOT_EXT_ID not in pinned:
            pinned.append(APPLYPILOT_EXT_ID)
        ext_dir["pinned_extensions"] = pinned

        prefs_file.write_text(json.dumps(prefs), encoding="utf-8")
    except Exception:
        logger.debug("Could not patch Chrome preferences", exc_info=True)


def _get_real_user_agent() -> str:
    """Build a realistic Chrome user agent string for macOS.

    Reads the actual Chrome version to stay current. Falls back to a
    reasonable default if detection fails.
    """
    try:
        chrome_exe = config.get_chrome_path()
        result = subprocess.run(
            [chrome_exe, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        # "Google Chrome 145.0.7632.76" -> "145.0.7632.76"
        version = result.stdout.strip().split()[-1]
    except Exception:
        version = "133.0.6943.141"

    system = platform.system()
    if system == "Darwin":
        os_part = "Macintosh; Intel Mac OS X 10_15_7"
    elif system == "Windows":
        os_part = "Windows NT 10.0; Win64; x64"
    else:
        os_part = "X11; Linux x86_64"

    return f"Mozilla/5.0 ({os_part}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version} Safari/537.36"
