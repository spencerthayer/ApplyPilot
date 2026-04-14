"""Per-job overlay generation — lightweight diffs on base pieces.

Pattern: Prototype — base piece + lightweight delta.
Overlays are the core of storage efficiency: instead of storing full
tailored resumes, we store only what changed per job.
"""

from __future__ import annotations

import re
import uuid

from applypilot.db.dto import OverlayDTO, PieceDTO
from applypilot.db.interfaces.overlay_repository import OverlayRepository


def _uid() -> str:
    return uuid.uuid4().hex[:12]


def generate_overlay(
        piece: PieceDTO,
        jd_keywords: list[str],
        job_url: str,
        track_id: str | None,
        overlay_repo: OverlayRepository,
) -> OverlayDTO | None:
    """Generate an overlay for a piece based on JD requirements.

    Returns None if no modification needed (piece already matches well).
    """
    overlay_type, content_delta = _classify_and_generate(piece, jd_keywords)
    if not overlay_type:
        return None
    overlay = OverlayDTO(
        id=_uid(),
        piece_id=piece.id,
        job_url=job_url,
        track_id=track_id,
        overlay_type=overlay_type,
        content_delta=content_delta,
    )
    overlay_repo.save(overlay)
    return overlay


def generate_overlays_for_job(
        pieces: list[PieceDTO],
        jd_keywords: list[str],
        job_url: str,
        track_id: str | None,
        overlay_repo: OverlayRepository,
) -> list[OverlayDTO]:
    """Generate overlays for all pieces that need modification for a job."""
    overlays = []
    for piece in pieces:
        overlay = generate_overlay(piece, jd_keywords, job_url, track_id, overlay_repo)
        if overlay:
            overlays.append(overlay)
    return overlays


def _classify_and_generate(piece: PieceDTO, jd_keywords: list[str]) -> tuple[str, str]:
    """Determine overlay type and content delta for a piece.

    Returns ("", "") if no overlay needed.
    """
    if not jd_keywords or piece.piece_type in ("header", "education"):
        return "", ""

    content_lower = piece.content.lower()
    matched = [kw for kw in jd_keywords if kw.lower() in content_lower]

    if piece.piece_type == "bullet":
        # Keyword injection: bold/emphasize matched keywords
        if matched:
            delta = piece.content
            for kw in matched:
                pattern = re.compile(re.escape(kw), re.IGNORECASE)
                delta = pattern.sub(kw.upper(), delta, count=1)
            if delta != piece.content:
                return "keyword_inject", delta

    elif piece.piece_type == "skill_group":
        # Add missing JD keywords to skill groups
        missing = [kw for kw in jd_keywords if kw.lower() not in content_lower]
        if missing:
            return "keyword_inject", f"{piece.content}, {', '.join(missing[:3])}"

    return "", ""
