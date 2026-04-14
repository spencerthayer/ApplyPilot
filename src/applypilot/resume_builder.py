"""Resume builder pattern — single renderer, multiple data sources.

Two data shapes feed the same builder:
  - JSON Resume (canonical resume.json) via `from_json_resume()`
  - LLM tailored output via `from_tailored_output()`

The builder produces a section map, the renderer outputs text.
Empty sections are never rendered.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field


@dataclass
class ResumeBuilder:
    """Accumulates resume sections. Only non-empty sections render."""

    header_lines: list[str] = field(default_factory=list)
    sections: OrderedDict[str, str] = field(default_factory=OrderedDict)

    def set_header(self, *lines: str) -> "ResumeBuilder":
        self.header_lines = [l for l in lines if l]
        return self

    def add_section(self, name: str, content: str) -> "ResumeBuilder":
        if content and content.strip():
            self.sections[name] = content.strip()
        return self

    def render_text(self) -> str:
        lines = list(self.header_lines) + [""]
        for name, content in self.sections.items():
            lines.append(name)
            lines.append(content)
            lines.append("")
        return "\n".join(lines).strip()

    def render_html(self, skill_annotations: dict | None = None) -> str:
        """Render professional HTML directly from structured sections."""
        from applypilot.scoring.pdf.html_renderer import build_html_from_builder

        return build_html_from_builder(self, skill_annotations=skill_annotations)


def _coerce_str(val) -> str:
    if val is None:
        return ""
    return str(val).strip()


def _coerce_list(val) -> list[str]:
    if not isinstance(val, list):
        return []
    return [_coerce_str(v) for v in val if _coerce_str(v)]


def _sort_work_entries(work: list[dict]) -> list[dict]:
    """Sort work entries: current roles first, then by startDate descending."""
    entries = [e for e in work if isinstance(e, dict)]

    def _is_current(e):
        end = _coerce_str(e.get("endDate"))
        return not end or end.lower() == "present"

    current = [e for e in entries if _is_current(e)]
    past = sorted(
        [e for e in entries if not _is_current(e)],
        key=lambda e: _coerce_str(e.get("startDate")) or "0000",
        reverse=True,
    )
    return current + past


def from_json_resume(data: dict) -> ResumeBuilder:
    """Build from canonical JSON Resume format (resume.json)."""
    b = ResumeBuilder()
    basics = data.get("basics", {}) if isinstance(data.get("basics"), dict) else {}
    location = basics.get("location", {}) if isinstance(basics.get("location"), dict) else {}

    # Header
    name = _coerce_str(basics.get("name"))
    label = _coerce_str(basics.get("label"))
    loc_parts = [_coerce_str(location.get(k)) for k in ("city", "region", "countryCode")]
    loc_line = ", ".join(p for p in loc_parts if p)
    contact = []
    for k in ("email", "phone", "url"):
        v = _coerce_str(basics.get(k))
        if v:
            contact.append(v)
    for p in basics.get("profiles", []) if isinstance(basics.get("profiles"), list) else []:
        if isinstance(p, dict) and _coerce_str(p.get("url")):
            contact.append(_coerce_str(p["url"]))
    contact_line = " | ".join(dict.fromkeys(contact))
    b.set_header(name, label, loc_line, contact_line)

    # Summary
    b.add_section("SUMMARY", _coerce_str(basics.get("summary")))

    # Experience (before skills — HR reads bullets first)
    work = data.get("work", []) if isinstance(data.get("work"), list) else []
    exp_lines = []
    for entry in _sort_work_entries(work):
        dates = " - ".join(
            p for p in [_coerce_str(entry.get("startDate")), _coerce_str(entry.get("endDate")) or "Present"] if p
        )
        header = " | ".join(p for p in [_coerce_str(entry.get("position")), _coerce_str(entry.get("name")), dates] if p)
        exp_lines.append(header or "Untitled role")
        subtitle = " | ".join(p for p in [_coerce_str(entry.get("location")), _coerce_str(entry.get("url"))] if p)
        if subtitle:
            exp_lines.append(subtitle)
        if _coerce_str(entry.get("summary")):
            exp_lines.append(f"- {_coerce_str(entry['summary'])}")
        for h in _coerce_list(entry.get("highlights", [])):
            exp_lines.append(f"- {h}")
        exp_lines.append("")
    b.add_section("EXPERIENCE", "\n".join(exp_lines))

    # Skills (after experience — ATS keyword scan, not primary reading)
    skills = data.get("skills", []) if isinstance(data.get("skills"), list) else []
    skill_lines = []
    for entry in skills:
        if not isinstance(entry, dict):
            continue
        cat = _coerce_str(entry.get("name")) or "Skills"
        kws = ", ".join(_coerce_list(entry.get("keywords", [])))
        if kws:
            skill_lines.append(f"{cat}: {kws}")
    b.add_section("TECHNICAL SKILLS", "\n".join(skill_lines))

    # Projects
    projects = data.get("projects", []) if isinstance(data.get("projects"), list) else []
    proj_lines = []
    for entry in projects:
        if not isinstance(entry, dict):
            continue
        name_ = _coerce_str(entry.get("name")) or "Untitled project"
        bullets = []
        if _coerce_str(entry.get("description")):
            bullets.append(_coerce_str(entry["description"]))
        bullets.extend(_coerce_list(entry.get("highlights", [])))
        if not bullets:
            continue
        proj_lines.append(name_)
        dates = " - ".join(p for p in [_coerce_str(entry.get("startDate")), _coerce_str(entry.get("endDate"))] if p)
        subtitle = " | ".join(p for p in [dates, _coerce_str(entry.get("url"))] if p)
        if subtitle:
            proj_lines.append(subtitle)
        for bl in bullets:
            proj_lines.append(f"- {bl}")
        proj_lines.append("")
    b.add_section("PROJECTS", "\n".join(proj_lines))

    # Education
    education = data.get("education", []) if isinstance(data.get("education"), list) else []
    edu_lines = []
    for entry in education:
        if not isinstance(entry, dict):
            continue
        line = " | ".join(
            p
            for p in [
                _coerce_str(entry.get("institution")),
                _coerce_str(entry.get("studyType")),
                _coerce_str(entry.get("area")),
                _coerce_str(entry.get("endDate")),
            ]
            if p
        )
        if line:
            edu_lines.append(line)
    b.add_section("EDUCATION", "\n".join(edu_lines))

    # Certificates
    certs = data.get("certificates", []) if isinstance(data.get("certificates"), list) else []
    cert_lines = []
    for entry in certs:
        if not isinstance(entry, dict):
            continue
        line = " | ".join(
            p
            for p in [_coerce_str(entry.get("name")), _coerce_str(entry.get("issuer")), _coerce_str(entry.get("date"))]
            if p
        )
        if line:
            cert_lines.append(line)
    b.add_section("CERTIFICATES", "\n".join(cert_lines))

    # Publications
    pubs = data.get("publications", []) if isinstance(data.get("publications"), list) else []
    pub_lines = []
    for entry in pubs:
        if not isinstance(entry, dict):
            continue
        line = " | ".join(
            p
            for p in [
                _coerce_str(entry.get("name")),
                _coerce_str(entry.get("publisher")),
                _coerce_str(entry.get("releaseDate")),
            ]
            if p
        )
        if line:
            pub_lines.append(line)
        s = _coerce_str(entry.get("summary"))
        if s:
            pub_lines.append(f"- {s}")
    b.add_section("PUBLICATIONS", "\n".join(pub_lines))

    return b


def from_tailored_output(data: dict, profile: dict) -> ResumeBuilder:
    """Build from LLM tailored output format."""
    from applypilot.scoring.tailor.response_assembler import sanitize_text, normalize_bullet

    b = ResumeBuilder()
    personal = profile.get("personal", {})
    basics = profile.get("basics", {})
    location = basics.get("location", {}) if isinstance(basics.get("location"), dict) else {}

    # Header — always from profile, never LLM
    name = personal.get("full_name") or basics.get("name", "")
    title = sanitize_text(data.get("title", ""))
    loc = ", ".join(p for p in [location.get("city", ""), location.get("country", "")] if p)
    contact = []
    for k in ("email", "phone"):
        v = personal.get(k) or basics.get(k, "")
        if v:
            contact.append(v)
    for k in ("github_url", "linkedin_url", "portfolio_url"):
        v = personal.get(k, "")
        if v:
            contact.append(v)
    if not contact:
        for p in basics.get("profiles", []):
            if isinstance(p, dict) and p.get("url"):
                contact.append(p["url"])
    b.set_header(name, title, loc, " | ".join(contact) if contact else "")

    # Summary
    b.add_section("SUMMARY", sanitize_text(data.get("summary", "")))

    # Experience (before skills — HR reads bullets first)
    exp_lines = []
    for entry in data.get("experience", []):
        exp_lines.append(sanitize_text(entry.get("header", "")))
        if entry.get("subtitle"):
            exp_lines.append(sanitize_text(entry["subtitle"]))
        for bl in entry.get("bullets", []):
            text = normalize_bullet(bl)
            if text:
                exp_lines.append(f"- {sanitize_text(text)}")
        exp_lines.append("")
    b.add_section("EXPERIENCE", "\n".join(exp_lines))

    # Skills (after experience — ATS keyword scan)
    skill_lines = []
    if isinstance(data.get("skills"), dict):
        for cat, val in data["skills"].items():
            cleaned = sanitize_text(str(val))
            if cleaned:
                skill_lines.append(f"{cat}: {cleaned}")
    b.add_section("TECHNICAL SKILLS", "\n".join(skill_lines))

    # Projects
    proj_lines = []
    for entry in data.get("projects", []):
        header = sanitize_text(entry.get("header", ""))
        bullets = [normalize_bullet(bl) for bl in entry.get("bullets", []) if normalize_bullet(bl)]
        if header and bullets:
            proj_lines.append(header)
            if entry.get("subtitle"):
                proj_lines.append(sanitize_text(entry["subtitle"]))
            for text in bullets:
                proj_lines.append(f"- {sanitize_text(text)}")
            proj_lines.append("")
    b.add_section("PROJECTS", "\n".join(proj_lines))

    # Education
    b.add_section("EDUCATION", sanitize_text(str(data.get("education", ""))))

    return b


def from_pieces(
    piece_repo,
    overlay_repo=None,
    track_id: str | None = None,
    job_url: str | None = None,
) -> ResumeBuilder:
    """Build resume from DB pieces + optional overlays.

    This is the DB-first path:
    - No track_id: all pieces (full resume)
    - With track_id: only pieces mapped to that track
    - With job_url: apply overlays (tailored version)
    """

    b = ResumeBuilder()

    # Load pieces — track-scoped or all
    if track_id:
        pieces = piece_repo.get_track_pieces(track_id)
    else:
        pieces = []
        for ptype in ("header", "summary", "skill_group", "experience_entry", "bullet", "education", "project"):
            pieces.extend(piece_repo.get_by_type(ptype))

    if not pieces:
        return b  # Empty — no decomposition done yet

    # Load overlays if job-specific
    overlay_map: dict[str, str] = {}
    if overlay_repo and job_url:
        overlays = overlay_repo.get_for_job(job_url, track_id or "")
        overlay_map = {o.piece_id: o.content_delta for o in overlays}

    # Resolve content: overlay wins over base
    def _content(p):
        return overlay_map.get(p.id, p.content)

    # Group by type
    by_type: dict[str, list] = {}
    for p in pieces:
        by_type.setdefault(p.piece_type, []).append(p)

    # Header
    headers = by_type.get("header", [])
    if headers:
        b.set_header(*_content(headers[0]).split("\n"))

    # Summary
    summaries = by_type.get("summary", [])
    if summaries:
        b.add_section("SUMMARY", _content(summaries[0]))

    # Skills
    skill_lines = [_content(p) for p in by_type.get("skill_group", [])]
    b.add_section("TECHNICAL SKILLS", "\n".join(skill_lines))

    # Experience — with child bullets
    bullet_map: dict[str, list] = {}
    for p in by_type.get("bullet", []):
        bullet_map.setdefault(p.parent_piece_id or "", []).append(p)

    exp_lines = []
    for p in by_type.get("experience_entry", []):
        exp_lines.append(_content(p))
        for bp in sorted(bullet_map.get(p.id, []), key=lambda x: x.sort_order):
            exp_lines.append(f"- {_content(bp)}")
        exp_lines.append("")
    b.add_section("EXPERIENCE", "\n".join(exp_lines))

    # Education
    edu_lines = [_content(p) for p in by_type.get("education", [])]
    b.add_section("EDUCATION", "\n".join(edu_lines))

    # Projects
    proj_lines = [_content(p) for p in by_type.get("project", [])]
    b.add_section("PROJECTS", "\n".join(proj_lines))

    return b
