"""Api."""

import json
import logging
import re
import urllib.error
import urllib.request

log = logging.getLogger(__name__)
_QUARANTINE_HTTP_STATUSES = {401, 404, 422}
_COMMON_SITE_ID_CANDIDATES = ["careers", "jobs", "recruiting", "myworkday"]
_opener = None  # initialized by setup_proxy(), None = use default urllib


def _workday_search_request(employer: dict, search_text: str, limit: int, offset: int, site_id: str) -> dict:
    url = f"{employer['base_url']}/wday/cxs/{employer['tenant']}/{site_id}/jobs"
    payload = json.dumps(
        {
            "appliedFacets": {},
            "limit": limit,
            "offset": offset,
            "searchText": search_text,
        }
    ).encode()
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    with _urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _try_discover_site_id(employer: dict, search_text: str, limit: int) -> str | None:
    candidates = _candidate_site_ids(employer)
    for candidate in candidates:
        if candidate == employer.get("site_id"):
            continue
        if not _site_path_is_live(employer["base_url"], candidate):
            continue
        try:
            _workday_search_request(employer, search_text, limit, 0, candidate)
            return candidate
        except urllib.error.HTTPError:
            continue
        except Exception:
            continue
    return None


def workday_search(employer: dict, search_text: str, limit: int = 20, offset: int = 0) -> dict:
    """Search jobs via Workday CXS API. Returns JSON with total + jobPostings."""
    try:
        return _workday_search_request(employer, search_text, limit, offset, employer["site_id"])
    except urllib.error.HTTPError as e:
        if offset == 0 and e.code in _QUARANTINE_HTTP_STATUSES:
            discovered_site_id = _try_discover_site_id(employer, search_text, limit)
            if discovered_site_id and discovered_site_id != employer.get("site_id"):
                employer["site_id"] = discovered_site_id
                return _workday_search_request(employer, search_text, limit, offset, employer["site_id"])
        raise


def workday_detail(employer: dict, external_path: str) -> dict:
    """Fetch full job detail via Workday CXS API."""
    url = f"{employer['base_url']}/wday/cxs/{employer['tenant']}/{employer['site_id']}{external_path}"

    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

    with _urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _candidate_site_ids(employer: dict) -> list[str]:
    site_id = (employer.get("site_id") or "").strip()
    name = (employer.get("name") or "").strip()
    normalized_name = re.sub(r"[^A-Za-z0-9]+", "", name)
    lower_name = normalized_name.lower()
    base_candidates = [site_id, normalized_name, lower_name, *_COMMON_SITE_ID_CANDIDATES]
    seen: set[str] = set()
    candidates: list[str] = []
    for candidate in base_candidates:
        if not candidate:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        candidates.append(candidate)
    return candidates


def _site_path_is_live(base_url: str, site_id: str) -> bool:
    for path in (f"/{site_id}", f"/en-US/{site_id}"):
        req = urllib.request.Request(
            f"{base_url}{path}",
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
        )
        try:
            with _urlopen(req, timeout=8):
                return True
        except urllib.error.HTTPError:
            continue
        except Exception:
            continue
    return False


def setup_proxy(proxy_str: str | None) -> None:
    """Configure a global urllib opener with proxy support."""
    global _opener
    if not proxy_str:
        _opener = urllib.request.build_opener()
        return

    parts = proxy_str.split(":")
    if len(parts) == 4:
        host, port, user, passwd = parts
        proxy_url = f"http://{user}:{passwd}@{host}:{port}"
    elif len(parts) == 2:
        proxy_url = f"http://{parts[0]}:{parts[1]}"
    else:
        log.warning("Proxy format not recognized; expected host:port or host:port:user:pass")
        _opener = urllib.request.build_opener()
        return

    proxy_handler = urllib.request.ProxyHandler(
        {
            "http": proxy_url,
            "https": proxy_url,
        }
    )
    _opener = urllib.request.build_opener(proxy_handler)
    log.info("Proxy configured")


def _urlopen(req, timeout=30):
    """Open a URL using the configured opener (with or without proxy)."""
    if _opener:
        return _opener.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)
