"""Renderer — assembled resume data → txt/html/pdf output."""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def render_txt(assembled_text: str, output_path: Path) -> Path:
    """Write assembled resume text to a .txt file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(assembled_text, encoding="utf-8")
    return output_path


def render_html(assembled_text: str, output_path: Path) -> Path:
    """Convert assembled text to professional HTML resume."""
    from applypilot.scoring.pdf.html_renderer import build_html
    from applypilot.scoring.pdf.parser import parse_resume

    resume = parse_resume(assembled_text)
    html = build_html(resume)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path


def render_pdf(assembled_text: str, output_path: Path) -> Path:
    """Convert assembled text to PDF via HTML intermediate.

    Falls back to txt if PDF rendering deps unavailable.
    """
    html_path = output_path.with_suffix(".html")
    render_html(assembled_text, html_path)

    try:
        from applypilot.scoring.pdf import html_to_pdf

        html_to_pdf(str(html_path), str(output_path))
        return output_path
    except (ImportError, Exception) as e:
        log.warning("PDF render unavailable (%s), falling back to txt", e)
        return render_txt(assembled_text, output_path.with_suffix(".txt"))
