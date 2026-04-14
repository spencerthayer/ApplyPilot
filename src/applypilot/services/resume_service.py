"""ResumeService — wraps tailoring, cover letters, and piece-based resume ops."""

from __future__ import annotations

import logging

from applypilot.db.interfaces.overlay_repository import OverlayRepository
from applypilot.db.interfaces.piece_repository import PieceRepository
from applypilot.db.interfaces.track_repository import TrackRepository
from applypilot.services.base import ServiceResult

log = logging.getLogger(__name__)


class ResumeService:
    def __init__(
            self,
            piece_repo: PieceRepository,
            overlay_repo: OverlayRepository,
            track_repo: TrackRepository,
    ):
        self._piece_repo = piece_repo
        self._overlay_repo = overlay_repo
        self._track_repo = track_repo

    # ── Piece-based operations ──────────────────────────────────────────

    def decompose(self, resume_data: dict) -> ServiceResult:
        """Decompose resume.json into atomic content-addressed pieces."""
        try:
            from applypilot.tailoring.pieces.decomposer import decompose_to_pieces

            pieces = decompose_to_pieces(resume_data, self._piece_repo)
            return ServiceResult(
                data={
                    "pieces": len(pieces),
                    "bullets": sum(1 for p in pieces if p.piece_type == "bullet"),
                }
            )
        except Exception as e:
            log.exception("Decompose failed: %s", e)
            return ServiceResult(success=False, error=str(e))

    def get_pieces(self, piece_type: str | None = None) -> ServiceResult:
        """List resume pieces, optionally filtered by type."""
        try:
            if piece_type:
                pieces = self._piece_repo.get_by_type(piece_type)
            else:
                pieces = (
                        self._piece_repo.get_by_type("header")
                        + self._piece_repo.get_by_type("summary")
                        + self._piece_repo.get_by_type("experience_entry")
                        + self._piece_repo.get_by_type("bullet")
                        + self._piece_repo.get_by_type("skill_group")
                        + self._piece_repo.get_by_type("education")
                        + self._piece_repo.get_by_type("project")
                )
            return ServiceResult(data={"pieces": pieces, "count": len(pieces)})
        except Exception as e:
            return ServiceResult(success=False, error=str(e))

    def create_overlays(self, job_url: str, jd_keywords: list[str], track_id: str | None = None) -> ServiceResult:
        """Generate per-job overlays for all pieces."""
        try:
            from applypilot.tailoring.pieces.overlay_engine import generate_overlays_for_job

            pieces_result = self.get_pieces()
            if not pieces_result.success:
                return pieces_result
            pieces = pieces_result.data["pieces"]
            overlays = generate_overlays_for_job(
                pieces,
                jd_keywords,
                job_url,
                track_id,
                self._overlay_repo,
            )
            return ServiceResult(data={"overlays": len(overlays)})
        except Exception as e:
            log.exception("Overlay generation failed: %s", e)
            return ServiceResult(success=False, error=str(e))

    def render_from_pieces(
            self, job_url: str, track_id: str = "default", page_budget: dict | None = None
    ) -> ServiceResult:
        """Assemble pieces + overlays into final resume text."""
        try:
            from applypilot.tailoring.pieces.reassembler import reassemble

            text = reassemble(track_id, job_url, page_budget, self._piece_repo, self._overlay_repo)
            return ServiceResult(data={"text": text, "length": len(text)})
        except Exception as e:
            log.exception("Render failed: %s", e)
            return ServiceResult(success=False, error=str(e))

    # ── Legacy pipeline operations ──────────────────────────────────────

    def run_tailoring(
            self,
            *,
            min_score: int = 5,  # lowered from 7 — TL1 light tailoring for score 5-6
            limit: int = 0,
            validation_mode: str = "normal",
            target_url: str | None = None,
            force: bool = False,
    ) -> ServiceResult:
        """Delegate to existing run_tailoring."""
        try:
            from applypilot.scoring.tailor import run_tailoring

            result = run_tailoring(
                min_score=min_score,
                limit=limit,
                validation_mode=validation_mode,
                target_url=target_url,
                force=force,
            )
            return ServiceResult(data=result if isinstance(result, dict) else {"action": "tailoring_complete"})
        except Exception as e:
            log.exception("Tailoring failed: %s", e)
            return ServiceResult(success=False, error=str(e))

    def run_cover_letters(
            self,
            *,
            min_score: int = 7,
            limit: int = 0,
            validation_mode: str = "normal",
            job_url: str | None = None,
    ) -> ServiceResult:
        """Delegate to existing run_cover_letters."""
        try:
            from applypilot.scoring.cover_letter import run_cover_letters

            run_cover_letters(
                min_score=min_score,
                limit=limit,
                validation_mode=validation_mode,
                job_url=job_url,
            )
            return ServiceResult(data={"action": "cover_letters_complete"})
        except Exception as e:
            log.exception("Cover letter generation failed: %s", e)
            return ServiceResult(success=False, error=str(e))

    def run_pdf_conversion(self) -> ServiceResult:
        """Delegate to existing batch_convert."""
        try:
            from applypilot.scoring.pdf import batch_convert

            batch_convert()
            return ServiceResult(data={"action": "pdf_conversion_complete"})
        except Exception as e:
            log.exception("PDF conversion failed: %s", e)
            return ServiceResult(success=False, error=str(e))
