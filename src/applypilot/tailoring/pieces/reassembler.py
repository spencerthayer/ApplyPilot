"""Resume reassembler — pieces + overlays → assembled resume text.

Pattern: Builder — constructs final resume from pieces + overlays at render time.
Page budget controls section allocation.
"""

from __future__ import annotations

from applypilot.db.dto import PieceDTO
from applypilot.db.interfaces.overlay_repository import OverlayRepository
from applypilot.db.interfaces.piece_repository import PieceRepository

# Default page budget: max items per section type
DEFAULT_BUDGET = {
    "header": 1,
    "summary": 1,
    "experience_entry": 10,
    "bullet": 50,
    "skill_group": 8,
    "education": 5,
    "project": 5,
}

# Section rendering order
SECTION_ORDER = ["header", "summary", "skill_group", "experience_entry", "education", "project"]


def reassemble(
        track_id: str,
        job_url: str,
        page_budget: dict | None,
        piece_repo: PieceRepository,
        overlay_repo: OverlayRepository,
) -> str:
    """Reassemble a resume from pieces + overlays for a specific job."""
    pieces = piece_repo.get_track_pieces(track_id)
    overlays = overlay_repo.get_for_job(job_url, track_id)
    return assemble_from_pieces(pieces, {o.piece_id: o.content_delta for o in overlays}, page_budget)


def assemble_from_pieces(
        pieces: list[PieceDTO],
        overlay_map: dict[str, str] | None = None,
        page_budget: dict | None = None,
) -> str:
    """Assemble plain-text resume from pieces, applying overlays where present."""
    overlay_map = overlay_map or {}
    budget = {**DEFAULT_BUDGET, **(page_budget or {})}

    # Resolve content: overlay wins over base piece
    resolved: list[tuple[PieceDTO, str]] = []
    for p in pieces:
        content = overlay_map.get(p.id, p.content)
        resolved.append((p, content))

    # Group by type
    by_type: dict[str, list[tuple[PieceDTO, str]]] = {}
    for p, content in resolved:
        by_type.setdefault(p.piece_type, []).append((p, content))

    # Build parent→children map for bullets
    bullet_map: dict[str, list[tuple[PieceDTO, str]]] = {}
    for p, content in by_type.get("bullet", []):
        parent = p.parent_piece_id or ""
        bullet_map.setdefault(parent, []).append((p, content))

    lines: list[str] = []

    for section_type in SECTION_ORDER:
        items = by_type.get(section_type, [])
        if not items:
            continue
        max_items = budget.get(section_type, 999)
        items = items[:max_items]

        if section_type == "header":
            for _, content in items:
                lines.append(content)
            lines.append("")

        elif section_type == "summary":
            lines.append("SUMMARY")
            for _, content in items:
                lines.append(content)
            lines.append("")

        elif section_type == "skill_group":
            lines.append("TECHNICAL SKILLS")
            for _, content in items:
                lines.append(content)
            lines.append("")

        elif section_type == "experience_entry":
            lines.append("EXPERIENCE")
            max_bullets = budget.get("bullet", 50)
            for p, content in items:
                lines.append(content)
                bullets = bullet_map.get(p.id, [])[:max_bullets]
                for _, bullet_content in bullets:
                    lines.append(f"- {bullet_content}")
                lines.append("")

        elif section_type == "education":
            lines.append("EDUCATION")
            for _, content in items:
                lines.append(content)
            lines.append("")

        elif section_type == "project":
            lines.append("PROJECTS")
            for _, content in items:
                lines.append(content)
            lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"
