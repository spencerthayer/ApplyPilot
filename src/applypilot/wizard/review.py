"""Per-section resume review — accept, edit, or reject each section (INIT-10)."""

from __future__ import annotations

import logging

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

log = logging.getLogger(__name__)
console = Console()

_SECTION_LABELS = {
    "basics": "Contact & Summary",
    "work": "Work Experience",
    "education": "Education",
    "skills": "Skills",
    "projects": "Projects",
    "certificates": "Certifications",
    "volunteer": "Volunteer",
    "awards": "Awards",
    "publications": "Publications",
}


def _format_section_preview(key: str, data) -> str:
    """Format a resume section for display."""
    if key == "basics":
        name = data.get("name", "")
        label = data.get("label", "")
        loc = data.get("location", {}).get("city", "")
        summary = (data.get("summary") or "")[:120]
        return f"{name} | {label} | {loc}\n{summary}{'...' if len(data.get('summary', '')) > 120 else ''}"
    if key == "work":
        lines = []
        for job in (data or [])[:5]:
            name = job.get("name", job.get("company", ""))
            pos = job.get("position", "")
            bullets = len(job.get("highlights", []))
            lines.append(f"  • {pos} @ {name} ({bullets} bullets)")
        if len(data or []) > 5:
            lines.append(f"  ... and {len(data) - 5} more")
        return "\n".join(lines)
    if key == "education":
        return "\n".join(
            f"  • {e.get('studyType', '')} {e.get('area', '')} — {e.get('institution', '')}" for e in (data or [])[:5]
        )
    if key == "skills":
        return "\n".join(f"  • {s.get('name', '')}: {', '.join(s.get('keywords', [])[:6])}" for s in (data or [])[:8])
    if key == "projects":
        return "\n".join(f"  • {p.get('name', '')}" for p in (data or [])[:5])
    if isinstance(data, list):
        return f"  {len(data)} item(s)"
    return str(data)[:200]


def review_resume_sections(resume_data: dict) -> dict:
    """Interactive per-section review. Returns modified resume data."""
    console.print(
        Panel(
            "[bold]Resume Review[/bold]\n\n"
            "Review each section. You can [green]accept[/green], [red]remove[/red], "
            "or [yellow]edit[/yellow] individual sections.",
            border_style="cyan",
        )
    )

    if not Confirm.ask("Review sections now?", default=True):
        return resume_data

    result = dict(resume_data)

    for key, label in _SECTION_LABELS.items():
        section_data = resume_data.get(key)
        if not section_data:
            continue
        if key == "basics" and not any(section_data.get(f) for f in ("name", "label", "summary")):
            continue

        console.print(f"\n[bold cyan]── {label} ──[/bold cyan]")
        console.print(_format_section_preview(key, section_data))

        choice = Prompt.ask(
            "  [a]ccept / [r]emove / [e]dit",
            choices=["a", "r", "e"],
            default="a",
        )

        if choice == "r":
            if key == "basics":
                console.print("  [yellow]Cannot remove basics section.[/yellow]")
            else:
                del result[key]
                console.print(f"  [red]Removed {label}[/red]")
        elif choice == "e":
            result[key] = _edit_section(key, section_data)
            console.print(f"  [green]Updated {label}[/green]")
        else:
            console.print("  [dim]Accepted[/dim]")

    # Preserve meta and other non-reviewed sections
    for key in resume_data:
        if key not in result and key not in _SECTION_LABELS:
            result[key] = resume_data[key]

    return result


def _edit_section(key: str, data):
    """Simple inline editor for a section."""
    if key == "basics":
        for field in ("name", "label", "email", "phone"):
            current = data.get(field, "")
            new_val = Prompt.ask(f"  {field}", default=current)
            if new_val != current:
                data[field] = new_val
        summary = data.get("summary", "")
        new_summary = Prompt.ask("  summary (enter to keep)", default=summary)
        if new_summary != summary:
            data["summary"] = new_summary
        return data

    if key == "work" and isinstance(data, list):
        for i, job in enumerate(data):
            name = job.get("name", job.get("company", ""))
            pos = job.get("position", "")
            keep = Confirm.ask(f"  Keep '{pos} @ {name}'?", default=True)
            if not keep:
                data[i] = None
        return [j for j in data if j is not None]

    if key == "skills" and isinstance(data, list):
        for i, skill in enumerate(data):
            name = skill.get("name", "")
            kws = ", ".join(skill.get("keywords", []))
            new_kws = Prompt.ask(f"  {name}", default=kws)
            data[i]["keywords"] = [k.strip() for k in new_kws.split(",") if k.strip()]
        return data

    # For other sections, just allow removing items
    if isinstance(data, list):
        kept = []
        for item in data:
            label = item.get("name", item.get("institution", item.get("title", str(item)[:50])))
            if Confirm.ask(f"  Keep '{label}'?", default=True):
                kept.append(item)
        return kept

    return data
