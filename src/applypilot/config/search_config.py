"""Search and sites configuration loading."""

from __future__ import annotations

from applypilot.config.paths import CONFIG_DIR, SEARCH_CONFIG_PATH


def load_search_config() -> dict:
    """Load search configuration from ~/.applypilot/searches.yaml."""
    import yaml

    if not SEARCH_CONFIG_PATH.exists():
        example = CONFIG_DIR / "searches.example.yaml"
        if example.exists():
            return yaml.safe_load(example.read_text(encoding="utf-8")) or {}
        return {}
    return yaml.safe_load(SEARCH_CONFIG_PATH.read_text(encoding="utf-8")) or {}
