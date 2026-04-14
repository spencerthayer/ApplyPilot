"""Site configuration loading, target building, and location filtering.

Single responsibility: reads sites.yaml + searches.yaml and produces
the list of (name, url, options) targets for the pipeline to process.
"""

import logging
from urllib.parse import quote_plus

import yaml

from applypilot import config
from applypilot.config import CONFIG_DIR

log = logging.getLogger(__name__)


def load_sites() -> list[dict]:
    """Load scraping target sites from config/sites.yaml + user overrides."""
    from applypilot.config.paths import APP_DIR

    sites = []
    # Package defaults
    path = CONFIG_DIR / "sites.yaml"
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        sites.extend(data.get("sites", []))
    # User overrides (~/.applypilot/sites.yaml)
    user_path = APP_DIR / "sites.yaml"
    if user_path.exists():
        data = yaml.safe_load(user_path.read_text(encoding="utf-8")) or {}
        user_sites = data.get("sites", [])
        # Merge — user sites with same name override package ones
        existing_names = {s.get("name") for s in sites}
        for s in user_sites:
            if s.get("name") not in existing_names:
                sites.append(s)
    if not sites:
        log.warning("No sites configured in config/sites.yaml or ~/.applypilot/sites.yaml")
    return sites


def load_location_filter(search_cfg: dict | None = None) -> tuple[list[str], list[str]]:
    """Load location accept/reject lists from search config."""
    if search_cfg is None:
        search_cfg = config.load_search_config()
    accept = search_cfg.get("location_accept", [])
    reject = search_cfg.get("location_reject_non_remote", [])
    return accept, reject


def location_ok(location: str | None, accept: list[str], reject: list[str]) -> bool:
    """Check if a job location passes the user's location filter."""
    if not location:
        return True
    loc = location.lower()
    # Remote-like locations always pass
    if any(r in loc for r in ("remote", "anywhere", "work from home", "wfh", "distributed")):
        return True
    for r in reject:
        if r.lower() in loc:
            return False
    for a in accept:
        if a.lower() in loc:
            return True
    return False


def build_scrape_targets(
    sites: list[dict] | None = None,
    search_cfg: dict | None = None,
) -> list[dict]:
    """Build the full list of (name, url, options) targets from sites + search config.

    - "search" sites get expanded: 1 URL per query from search config
    - "static" sites get scraped once as-is

    Placeholders: {query_encoded}, {location_encoded}, {query},
                  {distance}, {distance_encoded}
    """
    if sites is None:
        sites = load_sites()
    if search_cfg is None:
        search_cfg = config.load_search_config()

    queries_cfg = search_cfg.get("queries", [])
    queries = [q["query"] for q in queries_cfg]
    locs = search_cfg.get("locations", [])
    default_location = locs[0]["location"] if locs else ""
    distance_cfg = search_cfg.get("defaults", {}).get("distance", 0)
    try:
        default_distance = max(0, int(distance_cfg))
    except (TypeError, ValueError):
        default_distance = 0
    default_distance_str = str(default_distance)

    targets: list[dict] = []

    for site in sites:
        site_url = site.get("url", "")
        site_name = site.get("name", "Unknown")
        site_type = site.get("type", "static")
        no_headful = site.get("no_headful", False)
        # NEW: force_headful skips headless entirely (for SPA sites like Naukri)
        force_headful = site.get("force_headful", False)

        def _expand(url: str, query: str | None = None) -> str:
            expanded = url
            if query:
                expanded = expanded.replace("{query_encoded}", quote_plus(query))
                expanded = expanded.replace("{query}", quote_plus(query))
            expanded = expanded.replace("{location_encoded}", quote_plus(default_location))
            expanded = expanded.replace("{distance}", default_distance_str)
            expanded = expanded.replace("{distance_encoded}", quote_plus(default_distance_str))
            return expanded

        if site_type == "search" and queries:
            for query in queries:
                targets.append(
                    {
                        "name": site_name,
                        "url": _expand(site_url, query),
                        "query": query,
                        "no_headful": no_headful,
                        "force_headful": force_headful,
                    }
                )
        else:
            targets.append(
                {
                    "name": site_name,
                    "url": _expand(site_url),
                    "query": None,
                    "no_headful": no_headful,
                    "force_headful": force_headful,
                }
            )

    return targets
