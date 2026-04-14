"""ESCO API client — European Skills/Competences/Occupations taxonomy.

Free API, no key needed. Covers broad occupational skills (healthcare,
finance, trades, management) but weak on specific tech tools.
Best for: non-tech domains, soft skills, certifications.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from typing import Any

log = logging.getLogger(__name__)

_BASE = "https://ec.europa.eu/esco/api"
_TIMEOUT = 10


def _get(path: str, params: dict | None = None) -> Any:
    url = f"{_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read())


def search_skill(term: str, limit: int = 3) -> list[dict]:
    """Search ESCO for a skill. Returns [{uri, title}]."""
    try:
        data = _get("/search", {"text": term, "type": "skill", "language": "en", "limit": limit})
        return [
            {"uri": r.get("uri", ""), "title": r.get("title", "")} for r in data.get("_embedded", {}).get("results", [])
        ]
    except Exception as e:
        log.debug("ESCO search failed for '%s': %s", term, e)
        return []


def find_adjacencies(skill_name: str) -> list[tuple[str, float, str]]:
    """Find adjacent skills via ESCO. Returns [(target, confidence, relation)]."""
    results = search_skill(skill_name, limit=1)
    if not results:
        return []
    try:
        data = _get("/resource/skill", {"uri": results[0]["uri"], "language": "en"})
        adjacencies = []
        for rel_type, conf in [("broaderSkill", 0.70), ("narrowerSkill", 0.75), ("relatedSkill", 0.80)]:
            for item in data.get("_links", {}).get(rel_type, []):
                title = item.get("title", "").lower().replace(" ", "_")
                if title:
                    adjacencies.append((title, conf, rel_type.replace("Skill", "")))
        return adjacencies
    except Exception as e:
        log.debug("ESCO related skills failed: %s", e)
        return []
