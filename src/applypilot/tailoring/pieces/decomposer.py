"""Resume decomposer — breaks resume.json into atomic PieceDTOs.

Walks the JSON Resume tree, emitting typed pieces with content-hash dedup.
Reuses existing decomposition logic from tailoring/decomposer.py but
outputs PieceDTOs and persists via PieceRepository.
"""

from __future__ import annotations

import hashlib
import json
import uuid

from applypilot.db.dto import PieceDTO
from applypilot.db.interfaces.piece_repository import PieceRepository


def _uid() -> str:
    return uuid.uuid4().hex[:12]


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def decompose_to_pieces(resume: dict, piece_repo: PieceRepository) -> list[PieceDTO]:
    """Decompose resume.json → atomic PieceDTOs, dedup by content_hash."""
    pieces: list[PieceDTO] = []
    basics = resume.get("basics", {})

    # Header
    header_content = f"{basics.get('name', '')}\n{basics.get('label', '')}"
    pieces.append(
        PieceDTO(
            id=_uid(),
            content_hash=_hash(header_content),
            piece_type="header",
            content=header_content,
            metadata=json.dumps({"email": basics.get("email"), "phone": basics.get("phone")}),
            sort_order=0,
        )
    )

    # Summary
    summary = basics.get("summary", "")
    if summary:
        pieces.append(
            PieceDTO(
                id=_uid(),
                content_hash=_hash(summary),
                piece_type="summary",
                content=summary,
                sort_order=1,
            )
        )

    # Experience entries + bullets
    for i, job in enumerate(resume.get("work", [])):
        company = job.get("name", "Unknown")
        position = job.get("position", "")
        exp_content = f"{position} at {company}"
        exp_id = _uid()
        pieces.append(
            PieceDTO(
                id=exp_id,
                content_hash=_hash(exp_content),
                piece_type="experience_entry",
                content=exp_content,
                tags=json.dumps([company.lower()]),
                sort_order=10 + i,
                metadata=json.dumps(
                    {
                        "company": company,
                        "position": position,
                        "start": job.get("startDate", ""),
                        "end": job.get("endDate", ""),
                    }
                ),
            )
        )
        for j, bullet in enumerate(job.get("highlights", [])):
            pieces.append(
                PieceDTO(
                    id=_uid(),
                    content_hash=_hash(bullet),
                    piece_type="bullet",
                    content=bullet,
                    parent_piece_id=exp_id,
                    sort_order=j,
                )
            )

    # Skills
    for i, skill in enumerate(resume.get("skills", [])):
        name = skill.get("name", "")
        keywords = skill.get("keywords", [])
        if name and keywords:
            content = f"{name}: {', '.join(keywords)}"
            pieces.append(
                PieceDTO(
                    id=_uid(),
                    content_hash=_hash(content),
                    piece_type="skill_group",
                    content=content,
                    tags=json.dumps([k.lower() for k in keywords]),
                    metadata=json.dumps({"category": name}),
                    sort_order=100 + i,
                )
            )

    # Education
    for i, edu in enumerate(resume.get("education", [])):
        parts = [edu.get("institution", ""), edu.get("studyType", ""), edu.get("area", "")]
        content = " | ".join(p for p in parts if p)
        pieces.append(
            PieceDTO(
                id=_uid(),
                content_hash=_hash(content),
                piece_type="education",
                content=content,
                metadata=json.dumps(
                    {
                        "institution": edu.get("institution", ""),
                        "area": edu.get("area", ""),
                        "start": edu.get("startDate", ""),
                        "end": edu.get("endDate", ""),
                    }
                ),
                sort_order=200 + i,
            )
        )

    # Projects
    for i, proj in enumerate(resume.get("projects", [])):
        name = proj.get("name", "")
        desc = proj.get("description", "")
        content = f"{name} — {desc}" if desc else name
        keywords = proj.get("keywords", [])
        pieces.append(
            PieceDTO(
                id=_uid(),
                content_hash=_hash(content),
                piece_type="project",
                content=content,
                tags=json.dumps([k.lower() for k in keywords]),
                sort_order=300 + i,
            )
        )

    # Dedup: check existing by hash, save new
    saved = []
    for p in pieces:
        existing = piece_repo.get_by_hash(p.content_hash)
        if existing:
            saved.append(existing)
        else:
            piece_repo.save(p)
            saved.append(p)
    return saved
