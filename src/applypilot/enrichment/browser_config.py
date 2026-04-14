"""Browser stealth configuration — shared across enrichment and discovery.

Extracted from enrichment/detail.py to break the dependency on the old monolith.
"""

from __future__ import annotations

_PROXY_CONFIG: dict | None = None


def set_proxy(proxy_str: str | None) -> None:
    """Configure proxy for browser-based scraping."""
    global _PROXY_CONFIG
    if not proxy_str:
        _PROXY_CONFIG = None
        return
    parts = proxy_str.split(":")
    if len(parts) == 4:
        host, port, user, pwd = parts
        _PROXY_CONFIG = {
            "playwright": {"server": f"http://{host}:{port}", "username": user, "password": pwd},
        }
    elif len(parts) == 2:
        _PROXY_CONFIG = {"playwright": {"server": f"http://{proxy_str}"}}


def get_ua() -> str:
    """Build a realistic UA from the actual installed Chrome version."""
    try:
        from applypilot.apply.chrome import _get_real_user_agent

        return _get_real_user_agent()
    except Exception:
        return "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"


UA = get_ua()

STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {
  get: () => [
    {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer'},
    {name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai'},
    {name: 'Native Client', filename: 'internal-nacl-plugin'},
  ],
});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
window.chrome = window.chrome || {};
window.chrome.runtime = window.chrome.runtime || {};
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) =>
  parameters.name === 'notifications'
    ? Promise.resolve({state: Notification.permission})
    : originalQuery(parameters);
"""

# Backward compat aliases
_STEALTH_INIT_SCRIPT = STEALTH_INIT_SCRIPT
_PROXY_CONFIG = _PROXY_CONFIG
