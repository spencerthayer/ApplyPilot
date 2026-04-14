"""Helpers."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

import httpx
import typer
import yaml
from rich.console import Console

console = Console()

# API endpoint templates
API_BASE = "https://boards-api.greenhouse.io/v1/boards"
API_TEMPLATE = f"{API_BASE}/{{slug}}/jobs"

# Known slug fixes
KNOWN_FIXES = {"notion": "notionhq"}

app = typer.Typer(
    name="greenhouse",
    help="Manage Greenhouse ATS employers and verify configurations.",
    no_args_is_help=True,
)


def _load_config(config_path: Optional[Path] = None) -> dict:
    """Load greenhouse.yaml configuration."""
    if config_path is None:
        # Try user config first, then package config
        from applypilot.config import APP_DIR, CONFIG_DIR

        user_path = APP_DIR / "greenhouse.yaml"
        if user_path.exists():
            config_path = user_path
        else:
            config_path = CONFIG_DIR / "greenhouse.yaml"

    if not config_path.exists():
        console.print(f"[red]Config not found:[/red] {config_path}")
        raise typer.Exit(code=1)

    with config_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    return data.get("employers", {})


def _check_slug(slug: str) -> Tuple[bool, Optional[int], Optional[str]]:
    """Check if a slug is valid via Greenhouse API."""
    url = API_TEMPLATE.format(slug=slug)

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, headers={"Accept": "application/json"})
    except httpx.RequestError as e:
        return False, None, f"Request error: {e}"

    if resp.status_code == 200:
        try:
            data = resp.json()
            jobs = data.get("jobs", [])
            total = len(jobs)
            return True, total, None
        except ValueError:
            return True, None, "Invalid JSON"
    elif resp.status_code == 404:
        return False, None, "Not found"
    elif resp.status_code == 429:
        return False, None, "Rate limited"
    else:
        return False, None, f"HTTP {resp.status_code}"


def _generate_variations(name: str) -> List[str]:
    """Generate slug variations for a company name."""
    name = name.lower().strip()
    variations = [name]

    # No spaces
    no_spaces = name.replace(" ", "")
    if no_spaces != name:
        variations.append(no_spaces)

    # Dashes
    dash = name.replace(" ", "-")
    if dash != name:
        variations.append(dash)

    # Underscores
    underscore = name.replace(" ", "_")
    if underscore != name:
        variations.append(underscore)

    # First word only
    first_word = name.split()[0] if name else ""
    if first_word and first_word not in variations:
        variations.append(first_word)

    # Suffixes
    for suffix in ["careers", "jobs"]:
        plain = f"{name}{suffix}"
        dash_suf = f"{name}-{suffix}"
        if plain not in variations:
            variations.append(plain)
        if dash_suf not in variations:
            variations.append(dash_suf)

    # Deduplicate while preserving order
    seen = set()
    return [v for v in variations if not (v in seen or seen.add(v))]
