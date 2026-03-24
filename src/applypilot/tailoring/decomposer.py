"""Resume decomposer — breaks resume.json into atomic segments.

SRP: Only decomposes. Does not persist (that's SegmentsRepo's job),
does not generate variants (that's VariantGenerator's job).
"""

from __future__ import annotations

import uuid

from applypilot.db.segments_repo import Segment


def _uid() -> str:
    return uuid.uuid4().hex[:12]


def decompose(resume: dict) -> list[Segment]:
    """Break a canonical resume.json into a flat list of typed segments.

    Returns segments forming a tree via parent_id references.
    Root segment has parent_id=None.
    """
    root_id = _uid()
    segments: list[Segment] = []
    basics = resume.get("basics", {})

    segments.append(Segment(
        id=root_id, type="root", parent_id=None,
        content=basics.get("name", "Resume"),
        metadata={"label": basics.get("label", ""), "email": basics.get("email", "")},
    ))

    # Summary
    summary = basics.get("summary", "")
    if summary:
        segments.append(Segment(
            id=_uid(), type="summary", parent_id=root_id,
            content=summary, sort_order=0,
        ))

    # Experience → bullets
    for i, job in enumerate(resume.get("work", [])):
        exp_id = _uid()
        company = job.get("name", "Unknown")
        position = job.get("position", "")
        segments.append(Segment(
            id=exp_id, type="experience", parent_id=root_id,
            content=f"{position} at {company}",
            tags=[company.lower()],
            metadata={
                "company": company, "position": position,
                "start": job.get("startDate", ""), "end": job.get("endDate", ""),
            },
            sort_order=i + 10,
        ))
        for j, bullet in enumerate(job.get("highlights", [])):
            segments.append(Segment(
                id=_uid(), type="bullet", parent_id=exp_id,
                content=bullet, tags=[company.lower()], sort_order=j,
            ))

    # Skills
    for i, skill in enumerate(resume.get("skills", [])):
        name = skill.get("name", "")
        keywords = skill.get("keywords", [])
        if name and keywords:
            segments.append(Segment(
                id=_uid(), type="skill_group", parent_id=root_id,
                content=f"{name}: {', '.join(keywords)}",
                tags=[k.lower() for k in keywords],
                metadata={"category": name},
                sort_order=i + 100,
            ))

    # Education
    for i, edu in enumerate(resume.get("education", [])):
        parts = [edu.get("institution", ""), edu.get("studyType", ""), edu.get("area", "")]
        content = " | ".join(p for p in parts if p)
        segments.append(Segment(
            id=_uid(), type="education", parent_id=root_id,
            content=content,
            metadata={
                "institution": edu.get("institution", ""),
                "area": edu.get("area", ""),
                "studyType": edu.get("studyType", ""),
                "start": edu.get("startDate", ""),
                "end": edu.get("endDate", ""),
                "score": edu.get("score", ""),
            },
            sort_order=i + 200,
        ))

    # Projects
    for i, proj in enumerate(resume.get("projects", [])):
        name = proj.get("name", "")
        desc = proj.get("description", "")
        keywords = proj.get("keywords", [])
        content = f"{name} — {desc}" if desc else name
        segments.append(Segment(
            id=_uid(), type="project", parent_id=root_id,
            content=content,
            tags=[k.lower() for k in keywords],
            metadata={"keywords": keywords},
            sort_order=i + 300,
        ))

    return segments
