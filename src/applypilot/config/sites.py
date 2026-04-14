"""Sites configuration — manual ATS, blocked sites, SSO, base URLs."""

from __future__ import annotations

from applypilot.config.paths import CONFIG_DIR


def load_sites_config() -> dict:
    """Load sites.yaml configuration (sites list, manual_ats, blocked, etc.)."""
    import yaml

    path = CONFIG_DIR / "sites.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def is_manual_ats(url: str | None) -> bool:
    """Check if a URL routes through an ATS that requires manual application."""
    if not url:
        return False
    domains = load_sites_config().get("manual_ats", [])
    url_lower = url.lower()
    return any(domain in url_lower for domain in domains)


def load_blocked_sites() -> tuple[set[str], list[str]]:
    """Load blocked sites and URL patterns from sites.yaml."""
    blocked = load_sites_config().get("blocked", {})
    return set(blocked.get("sites", [])), blocked.get("url_patterns", [])


def load_blocked_sso() -> list[str]:
    """Load blocked SSO domains from sites.yaml."""
    return load_sites_config().get("blocked_sso", [])


def load_no_signup_domains() -> list[str]:
    """Load no-signup domains from sites.yaml."""
    return load_sites_config().get("no_signup_domains", [])


def load_base_urls() -> dict[str, str | None]:
    """Load site base URLs for URL resolution from sites.yaml."""
    return load_sites_config().get("base_urls", {})
