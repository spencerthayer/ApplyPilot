"""PDF/TXT resume ingestion via LLM → JSON Resume conversion."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from applypilot.discovery.smartextract import extract_json
from applypilot.resume.validation import validate_resume_json

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a resume parser. You receive raw text extracted from a resume document.
Return ONLY a valid JSON object following the JSON Resume v1.0.0 schema (https://jsonresume.org/schema).

Required top-level keys: basics, work, education, skills.
Optional: projects, certificates, publications, volunteer, languages, awards, references, meta.

Rules:
- Extract ALL information present in the text. Do not fabricate or infer missing data.
- Use ISO date fragments for dates: "2024-04" not "April 2024".
- Use empty string "" for missing optional string fields, not null.
- work[].highlights must be an array of bullet-point strings.
- skills[] entries must have "name" (category) and "keywords" (array of strings).
- Return raw JSON only. No markdown fences, no commentary.
"""


def extract_text(path: Path) -> str:
    """Extract text from a PDF or TXT file."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            import pdfplumber
        except ImportError:
            raise RuntimeError("pdfplumber is required for PDF import. Install with: pip install pdfplumber")
        parts: list[str] = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    parts.append(page_text)
        return "\n".join(parts)
    if suffix in (".txt", ".md"):
        return path.read_text(encoding="utf-8")
    raise ValueError(f"Unsupported file type: {suffix}. Use .pdf or .txt")


def parse_resume_via_llm(text: str) -> dict:
    """Send extracted resume text to the LLM and return parsed JSON Resume dict."""
    from applypilot.llm import get_client

    client = get_client()
    response = client.chat(
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        temperature=0.1,
        max_tokens=8192,
    )

    data = extract_json(response)
    validate_resume_json(data)
    return data


def merge_resume_jsons(documents: list[dict]) -> dict:
    """Merge multiple JSON Resume dicts into one, preferring the first for basics."""
    if len(documents) == 1:
        return documents[0]

    merged = json.loads(json.dumps(documents[0]))

    _ARRAY_DEDUP_KEYS = {
        "work": "name",
        "education": "institution",
        "skills": "name",
        "projects": "name",
        "certificates": "name",
        "publications": "name",
        "volunteer": "organization",
        "languages": "language",
    }

    for doc in documents[1:]:
        # basics: fill blanks only
        for key, val in (doc.get("basics") or {}).items():
            base_basics = merged.setdefault("basics", {})
            if key == "profiles":
                _merge_list_by_key(base_basics.setdefault("profiles", []), val or [], "network")
            elif key == "location":
                base_loc = base_basics.setdefault("location", {})
                for lk, lv in (val or {}).items():
                    if lv and not base_loc.get(lk):
                        base_loc[lk] = lv
            elif not base_basics.get(key) and val:
                base_basics[key] = val

        for section, dedup_key in _ARRAY_DEDUP_KEYS.items():
            if section in doc and isinstance(doc[section], list):
                _merge_list_by_key(merged.setdefault(section, []), doc[section], dedup_key)

    return merged


def _merge_list_by_key(base: list, incoming: list, key: str) -> None:
    """Append items from incoming to base, skipping duplicates by key."""
    seen = {item.get(key) for item in base if isinstance(item, dict) and item.get(key)}
    for item in incoming:
        val = item.get(key) if isinstance(item, dict) else None
        if val and val in seen:
            continue
        if val:
            seen.add(val)
        base.append(item)


def ingest_resumes(paths: list[Path]) -> dict:
    """Extract text from files → LLM parse each → merge → validate.

    Args:
        paths: List of PDF/TXT resume file paths.

    Returns:
        Merged and validated JSON Resume dict.
    """
    parsed: list[dict] = []

    for path in paths:
        path = Path(path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        log.info("Extracting text from %s", path.name)
        text = extract_text(path)
        if not text.strip():
            log.warning("No text extracted from %s, skipping", path.name)
            continue

        log.info("Parsing %s via LLM...", path.name)
        parsed.append(parse_resume_via_llm(text))

    if not parsed:
        raise ValueError("No resume content could be extracted from the provided files")

    result = merge_resume_jsons(parsed) if len(parsed) > 1 else parsed[0]
    validate_resume_json(result)
    return result
