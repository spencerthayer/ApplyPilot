"""CLI command: company — manage the company registry."""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import typer

import applypilot.cli as _cli

console = _cli.console
log = logging.getLogger(__name__)

__all__ = ["company_add", "company_list"]


def company_add(
        name: str = typer.Argument(..., help="Company name (e.g. 'Apple', 'Groww')."),
        url: str = typer.Argument(..., help="Career page URL (e.g. 'https://jobs.apple.com')."),
        search_url: str | None = typer.Option(
            None, "--search-url",
            help="Search URL with {query_encoded} placeholder. Auto-detected if not provided.",
        ),
) -> None:
    """Add a company and its career site to the registry for discovery."""
    _cli._bootstrap()

    from applypilot.discovery.company_registry import get_registry, CompanyRecord
    from applypilot.config.paths import APP_DIR
    import yaml

    # Derive key from name
    key = name.lower().replace(" ", "_").replace("-", "_")

    # Check if already exists
    registry = get_registry()
    existing = registry.resolve(key)
    if existing:
        console.print(f"[yellow]'{name}' already in registry as '{existing.key}' → {existing.career_url}[/yellow]")
        return

    # Parse domain from URL
    parsed = urlparse(url if url.startswith("http") else f"https://{url}")
    domain = parsed.hostname or ""
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    # Build search URL if not provided — append ?search={query_encoded} as best guess
    if not search_url:
        search_url = f"{base_url}/search?search={{query_encoded}}&location={{location_encoded}}"

    # Site name for sites.yaml
    site_name = f"{name} Careers"

    # 1. Save to ~/.applypilot/companies.yaml
    record = CompanyRecord(
        key=key, name=name, aliases=[], domain=domain,
        career_url=url, runners={"smartextract": site_name},
        ats="custom", source="user",
    )
    registry.save_user_entry(record)
    console.print(f"[green]✓[/green] Added '{name}' to ~/.applypilot/companies.yaml")

    # 2. Add site to ~/.applypilot/sites.yaml
    sites_path = APP_DIR / "sites.yaml"
    sites_data: dict = {}
    if sites_path.exists():
        sites_data = yaml.safe_load(sites_path.read_text(encoding="utf-8")) or {}
    sites_list = sites_data.setdefault("sites", [])

    # Check if site already exists
    if not any(s.get("name") == site_name for s in sites_list):
        sites_list.append({"name": site_name, "url": search_url, "type": "search"})
        sites_path.write_text(
            yaml.dump(sites_data, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        console.print(f"[green]✓[/green] Added '{site_name}' to ~/.applypilot/sites.yaml")
    else:
        console.print(f"[dim]Site '{site_name}' already in sites.yaml[/dim]")

    console.print(f"\n[bold]Next:[/bold] applypilot run discover --company {key}")


def company_list() -> None:
    """List all companies in the registry."""
    _cli._bootstrap()

    from applypilot.discovery.company_registry import get_registry
    from rich.table import Table

    registry = get_registry()
    table = Table(title=f"Company Registry ({len(registry._companies)} companies)")
    table.add_column("Key", style="bold")
    table.add_column("Name")
    table.add_column("Runners")
    table.add_column("ATS")
    table.add_column("Source")

    for key in sorted(registry._companies):
        rec = registry._companies[key]
        runners = ", ".join(f"{r}:{k}" for r, k in rec.runners.items()) or "—"
        table.add_row(key, rec.name, runners, rec.ats, rec.source)

    console.print(table)
