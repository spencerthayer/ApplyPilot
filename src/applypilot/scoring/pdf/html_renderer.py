"""HTML template builder for resume PDF rendering."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from applypilot.scoring.pdf.parser import parse_entries, parse_skills

if TYPE_CHECKING:
    from applypilot.resume_builder import ResumeBuilder

log = logging.getLogger(__name__)


def _load_skill_annotations(text_path: Path) -> dict:
    """Load bullet annotations from _DATA.json sidecar."""
    data_path = text_path.with_name(text_path.stem + "_DATA.json")
    if not data_path.exists():
        return {}
    try:
        data = json.loads(data_path.read_text(encoding="utf-8"))
        annotations: dict[str, list[str]] = {}
        for section in ("experience", "projects"):
            for entry in data.get(section, []):
                for bullet in entry.get("bullets", []):
                    if isinstance(bullet, dict):
                        text = bullet.get("text", "")
                        skills = bullet.get("skills", [])
                        if text and skills:
                            annotations[text[:50].lower().strip()] = skills
        return annotations
    except Exception:
        return {}


def _apply_highlights(bullet_text: str, annotations: dict) -> str:
    """Bold skill keywords in a bullet if annotations exist."""
    if not annotations:
        return bullet_text
    from applypilot.tailoring.skill_highlighter import highlight

    key = bullet_text[:50].lower().strip()
    skills = annotations.get(key, [])
    return highlight(bullet_text, skills) if skills else bullet_text


# ── Shared CSS ────────────────────────────────────────────────────────────

_RESUME_CSS = """\
@page { size: letter; margin: 0.35in 0.5in; }
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'Calibri', 'Segoe UI', Arial, sans-serif; font-size: 10pt; line-height: 1.35; color: #1a1a1a; }
.header { text-align: center; margin-bottom: 4px; padding-bottom: 4px; border-bottom: 1.5px solid #2a7ab5; }
.name { font-size: 18pt; font-weight: 700; color: #1a3a5c; letter-spacing: 0.5px; }
.title { font-size: 10.5pt; color: #3a6b8c; margin: 1px 0; }
.location { font-size: 9pt; color: #555; }
.contact { font-size: 9pt; color: #444; margin-top: 1px; }
.section { margin-top: 5px; }
.section-title { font-size: 10pt; font-weight: 700; color: #1a3a5c; text-transform: uppercase; \
letter-spacing: 0.8px; border-bottom: 1.5px solid #2a7ab5; padding-bottom: 1px; margin-bottom: 3px; }
.summary { font-size: 9.5pt; color: #333; line-height: 1.4; }
.skill-row { font-size: 9.5pt; margin: 0; line-height: 1.35; }
.skill-cat { font-weight: 600; color: #1a3a5c; }
.entry { margin-bottom: 4px; break-inside: avoid; }
.entry-title { font-weight: 600; font-size: 10pt; color: #1a3a5c; }
.entry-subtitle { font-size: 9pt; color: #4a7a9b; font-style: italic; margin-bottom: 1px; }
ul { margin-left: 14px; padding: 0; }
li { font-size: 9.5pt; margin-bottom: 1px; line-height: 1.35; }
.edu { font-size: 10pt; }"""


def _render_section_html(name: str, content: str, annotations: dict) -> str:
    """Render a single section to HTML from its text content."""
    upper = name.upper().strip()

    if upper == "SUMMARY":
        return (
            f'<div class="section"><div class="section-title">Summary</div>'
            f'<div class="summary">{content.strip()}</div></div>'
        )

    if upper == "TECHNICAL SKILLS":
        skills = parse_skills(content)
        rows = "".join(f'<div class="skill-row"><span class="skill-cat">{c}:</span> {v}</div>\n' for c, v in skills)
        return f'<div class="section"><div class="section-title">Technical Skills</div>{rows}</div>'

    if upper in ("EXPERIENCE", "PROJECTS"):
        label = "Experience" if upper == "EXPERIENCE" else "Projects"
        entries = parse_entries(content)
        items = ""
        for e in entries:
            bullets = "".join(f"<li>{_apply_highlights(b, annotations)}</li>" for b in e["bullets"])
            sub = f'<div class="entry-subtitle">{e["subtitle"]}</div>' if e["subtitle"] else ""
            items += f'<div class="entry"><div class="entry-title">{e["title"]}</div>{sub}<ul>{bullets}</ul></div>'
        return f'<div class="section"><div class="section-title">{label}</div>{items}</div>'

    if upper == "EDUCATION":
        return (
            f'<div class="section"><div class="section-title">Education</div>'
            f'<div class="edu">{content.strip()}</div></div>'
        )

    # Generic fallback (CERTIFICATES, PUBLICATIONS, etc.)
    return (
        f'<div class="section"><div class="section-title">{name.strip().title()}</div>'
        f'<div class="edu">{content.strip()}</div></div>'
    )


def _wrap_html(name: str, title: str, location: str, contact_html: str, body: str) -> str:
    loc = f'<div class="location">{location}</div>' if location else ""
    return (
        f'<!DOCTYPE html>\n<html><head><meta charset="utf-8">\n'
        f"<style>\n{_RESUME_CSS}\n</style></head><body>\n"
        f'<div class="header"><div class="name">{name}</div>'
        f'<div class="title">{title}</div>{loc}'
        f'<div class="contact">{contact_html}</div></div>\n'
        f"{body}\n</body></html>"
    )


def _contact_str(raw: str) -> str:
    parts = [p.strip() for p in raw.split("|")] if raw else []
    return " &nbsp;|&nbsp; ".join(parts)


# ── Primary path: from ResumeBuilder (no text round-trip) ─────────────────


def build_html_from_builder(builder: "ResumeBuilder", skill_annotations: dict | None = None) -> str:
    """Build professional HTML directly from ResumeBuilder structured sections."""
    annotations = skill_annotations or {}
    h = builder.header_lines
    name = h[0] if len(h) > 0 else ""
    title = h[1] if len(h) > 1 else ""
    location = h[2] if len(h) > 2 else ""
    contact = h[3] if len(h) > 3 else ""
    if len(h) == 3 and ("@" in h[2] or "|" in h[2]):
        location, contact = "", h[2]

    body = "".join(_render_section_html(n, c, annotations) for n, c in builder.sections.items())
    return _wrap_html(name, title, location, _contact_str(contact), body)


# ── Legacy path: from parse_resume() dict (backward compat) ──────────────


def build_html(resume: dict, skill_annotations: dict | None = None) -> str:
    """Build professional resume HTML from parsed data (legacy text→dict path)."""
    annotations = skill_annotations or {}
    body = "".join(_render_section_html(n, c, annotations) for n, c in resume.get("sections", {}).items())
    return _wrap_html(
        resume.get("name", ""),
        resume.get("title", ""),
        resume.get("location", ""),
        _contact_str(resume.get("contact", "")),
        body,
    )
