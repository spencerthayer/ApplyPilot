"""Text-to-PDF conversion — re-exports from decomposed modules."""

from applypilot.config import TAILORED_DIR  # noqa: F401
from applypilot.scoring.pdf.parser import parse_resume, parse_skills, parse_entries  # noqa: F401
from applypilot.scoring.pdf.html_renderer import build_html, _load_skill_annotations, _apply_highlights  # noqa: F401
from applypilot.scoring.pdf.pdf_renderer import render_pdf, convert_to_pdf, batch_convert  # noqa: F401
