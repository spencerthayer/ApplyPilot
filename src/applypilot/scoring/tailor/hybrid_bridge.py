"""Hybrid bridge — connects two-stage pipeline to piece-based storage.

1. ensure_decomposed(): decompose resume.json → pieces DB (once)
2. store_overlays(): after tailoring, diff output vs pieces → overlays DB
3. try_cache(): check if overlays exist for similar JD → reassemble without LLM
"""

from __future__ import annotations

import hashlib
import logging

from applypilot.db.dto import OverlayDTO
from applypilot.db.interfaces.overlay_repository import OverlayRepository
from applypilot.db.interfaces.piece_repository import PieceRepository
from applypilot.tailoring.pieces.decomposer import decompose_to_pieces

log = logging.getLogger(__name__)


def _jd_hash(jd_text: str) -> str:
    """Stable hash of JD keywords for cache lookup."""
    words = sorted(set(w for w in jd_text.lower().split() if len(w) > 3))[:50]
    return hashlib.sha256(" ".join(words).encode()).hexdigest()[:16]


def _jd_keyword_set(jd_text: str) -> set[str]:
    """Extract meaningful keywords from JD for similarity check."""
    stop = {
        "with",
        "that",
        "this",
        "from",
        "have",
        "will",
        "your",
        "they",
        "been",
        "more",
        "about",
        "which",
        "their",
        "would",
        "make",
        "like",
        "into",
        "than",
        "other",
        "some",
    }
    return {w.lower() for w in jd_text.split() if len(w) > 3 and w.lower() not in stop}


def ensure_decomposed(resume: dict, piece_repo: PieceRepository) -> int:
    """Decompose resume into pieces if not already done. Returns piece count."""
    existing = piece_repo.get_by_type("header")
    if existing:
        return len(piece_repo.get_by_type("bullet")) + len(existing)
    pieces = decompose_to_pieces(resume, piece_repo)
    log.info("Decomposed resume into %d pieces", len(pieces))
    return len(pieces)


def map_track(
        track_id: str,
        track_skills: list[str],
        piece_repo: PieceRepository,
        conn,
) -> int:
    """Map pieces to a track based on skill overlap. Returns mapping count."""
    pieces = []
    for ptype in ("header", "summary", "experience_entry", "bullet", "skill_group", "education"):
        pieces.extend(piece_repo.get_by_type(ptype))

    if not pieces:
        return 0

    skills_lower = {s.lower() for s in track_skills}
    count = 0

    for p in pieces:
        content_lower = p.content.lower()
        tags = []
        try:
            import json

            tags = [t.lower() for t in json.loads(p.tags)] if p.tags else []
        except Exception:
            pass

        # Always include header, summary, education
        if p.piece_type in ("header", "summary", "education"):
            emphasis = 1.0
            include = 1
        else:
            # Score relevance by keyword overlap
            matched = sum(1 for s in skills_lower if s in content_lower or s in tags)
            emphasis = min(matched / max(len(skills_lower), 1) * 3, 1.0)
            include = 1 if emphasis >= 0.1 or p.piece_type == "experience_entry" else 0

        conn.execute(
            "INSERT OR REPLACE INTO track_piece_mappings (track_id, piece_id, emphasis, include) VALUES (?, ?, ?, ?)",
            (track_id, p.id, round(emphasis, 2), include),
        )
        count += 1

    conn.commit()
    log.info("Mapped %d pieces to track %s", count, track_id)
    return count


def store_overlays(
        tailored_json: dict,
        job_url: str,
        track_id: str | None,
        piece_repo: PieceRepository,
        overlay_repo: OverlayRepository,
) -> int:
    """Diff tailored output against base pieces, store as overlays. Returns overlay count."""
    bullets = piece_repo.get_by_type("bullet")
    if not bullets:
        return 0

    # Build lookup: content_hash → piece
    base_map = {p.content.lower().strip(): p for p in bullets}

    count = 0
    for exp in tailored_json.get("experience", []):
        for b in exp.get("bullets", []):
            text = b.get("text", b) if isinstance(b, dict) else str(b)
            text_lower = text.lower().strip()

            # Find the closest matching base piece
            matched_piece = base_map.get(text_lower)
            if matched_piece:
                continue  # No change, no overlay needed

            # Find partial match (same first 30 chars = same bullet, rewritten)
            for base_text, piece in base_map.items():
                if base_text[:30] == text_lower[:30] or _similarity(base_text, text_lower) > 0.5:
                    overlay = OverlayDTO(
                        id=hashlib.sha256(f"{piece.id}:{job_url}".encode()).hexdigest()[:12],
                        piece_id=piece.id,
                        job_url=job_url,
                        track_id=track_id or "",
                        overlay_type="content_rewrite",
                        content_delta=text,
                    )
                    overlay_repo.save(overlay)
                    count += 1
                    break

    log.info("Stored %d overlays for %s", count, job_url[:60])
    return count


def _all_pieces(piece_repo: PieceRepository) -> list:
    pieces = []
    for ptype in ("header", "summary", "skill_group", "experience_entry", "bullet", "education"):
        pieces.extend(piece_repo.get_by_type(ptype))
    return pieces


def try_cache(
        job_url: str,
        track_id: str | None,
        piece_repo: PieceRepository,
        overlay_repo: OverlayRepository,
        jd_text: str | None = None,
) -> str | None:
    """Check if overlays exist for this job or a similar JD. Reassemble without LLM."""
    from applypilot.tailoring.pieces.reassembler import assemble_from_pieces

    # 1. Exact match
    overlays = overlay_repo.get_for_job(job_url, track_id or "")
    if overlays:
        pieces = _all_pieces(piece_repo)
        text = assemble_from_pieces(pieces, {o.piece_id: o.content_delta for o in overlays})
        log.info("Cache hit (exact): %d pieces + %d overlays", len(pieces), len(overlays))
        return text

    # 2. Similar JD (>80% keyword overlap)
    if not jd_text:
        return None
    jd_kws = _jd_keyword_set(jd_text)
    if not jd_kws:
        return None
    try:
        from applypilot.db.connection import get_connection

        conn = get_connection()
        rows = conn.execute("SELECT DISTINCT job_url FROM overlays").fetchall()
        for row in rows:
            other_url = row[0]
            if other_url == job_url:
                continue
            job_row = conn.execute("SELECT full_description FROM jobs WHERE url = ?", (other_url,)).fetchone()
            if not job_row or not job_row[0]:
                continue
            overlap = len(jd_kws & _jd_keyword_set(job_row[0])) / max(len(jd_kws | _jd_keyword_set(job_row[0])), 1)
            if overlap >= 0.80:
                overlays = overlay_repo.get_for_job(other_url, track_id or "")
                if overlays:
                    pieces = _all_pieces(piece_repo)
                    text = assemble_from_pieces(pieces, {o.piece_id: o.content_delta for o in overlays})
                    log.info("Cache hit (similar %.0f%%): reusing from %s", overlap * 100, other_url[:50])
                    return text
    except Exception as e:
        log.debug("Variant cache failed: %s", e)
    return None


def _similarity(a: str, b: str) -> float:
    """Quick word-overlap similarity."""
    wa, wb = set(a.split()), set(b.split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / max(len(wa), len(wb))


def record_apply_feedback(
        job_url: str,
        outcome: str,
        job_title: str,
        overlay_repo: OverlayRepository,
        piece_repo: PieceRepository,
) -> int:
    """After apply outcome, record feedback for each bullet used in that job's overlays."""
    overlays = overlay_repo.get_for_job(job_url, "")
    if not overlays:
        # Try without track filter
        from applypilot.db.connection import get_connection

        conn = get_connection()
        rows = conn.execute("SELECT * FROM overlays WHERE job_url = ?", (job_url,)).fetchall()
        if not rows:
            return 0

    count = 0
    from applypilot.tailoring.bullet_bank import BulletBank

    try:
        from applypilot.bootstrap import get_app

        bank = BulletBank(get_app().container.bullet_bank_repo)
    except Exception:
        return 0

    for o in overlays:
        piece = piece_repo.get_by_id(o.piece_id)
        if piece and piece.piece_type == "bullet":
            # Ensure bullet exists in bank
            existing = bank.get_bullet(piece.id)
            if not existing:
                bank.add_bullet(piece.content, tags=[piece.piece_type])
            bank.record_feedback(piece.id, job_title, outcome)
            count += 1

    log.info("Recorded %s feedback for %d bullets (%s)", outcome, count, job_url[:50])
    return count


def refresh_pieces(resume: dict, piece_repo: PieceRepository, conn) -> dict:
    """Re-decompose resume and cascade changes through the piece system.

    Returns summary of what changed.
    """
    from applypilot.tailoring.pieces.decomposer import decompose_to_pieces

    # 1. Get existing pieces
    old_pieces = {}
    for ptype in ("header", "summary", "experience_entry", "bullet", "skill_group", "education", "project"):
        for p in piece_repo.get_by_type(ptype):
            old_pieces[p.content_hash] = p

    # 2. Decompose fresh (force — delete old first)
    conn.execute("DELETE FROM pieces")
    conn.commit()
    new_pieces = decompose_to_pieces(resume, piece_repo)
    new_hashes = {p.content_hash for p in new_pieces}
    old_hashes = set(old_pieces.keys())

    added = new_hashes - old_hashes
    removed = old_hashes - new_hashes
    unchanged = new_hashes & old_hashes

    # 3. Invalidate overlays for removed/changed pieces
    if removed:
        removed_ids = [old_pieces[h].id for h in removed if h in old_pieces]
        for pid in removed_ids:
            conn.execute("DELETE FROM overlays WHERE piece_id = ?", (pid,))
        conn.commit()

    # 4. Re-map tracks
    try:
        from applypilot.bootstrap import get_app

        tracks = get_app().container.track_repo.get_all_tracks()
        conn.execute("DELETE FROM track_piece_mappings")
        conn.commit()
        for t in tracks:
            skills = t["skills"] if isinstance(t.get("skills"), list) else []
            if skills:
                map_track(t["track_id"], skills, piece_repo, conn)
    except Exception as e:
        log.debug("Track re-mapping skipped: %s", e)

    # 5. Clear stale tailored resumes
    stale_count = 0
    if removed:
        try:
            conn.execute(
                "UPDATE jobs SET tailored_resume_path = NULL, tailored_at = NULL WHERE tailored_resume_path IS NOT NULL"
            )
            stale_count = conn.execute("SELECT changes()").fetchone()[0]
            conn.commit()
        except Exception:
            pass

    summary = {
        "total_pieces": len(new_pieces),
        "added": len(added),
        "removed": len(removed),
        "unchanged": len(unchanged),
        "overlays_invalidated": len(removed),
        "tailored_cleared": stale_count,
    }
    log.info(
        "Piece refresh: %d pieces (%d added, %d removed, %d unchanged), %d overlays invalidated, %d tailored cleared",
        *[
            summary[k]
            for k in ("total_pieces", "added", "removed", "unchanged", "overlays_invalidated", "tailored_cleared")
        ],
    )
    return summary
