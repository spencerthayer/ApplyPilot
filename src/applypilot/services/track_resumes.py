"""Track base resume generation — shared by wizard and CLI."""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path

from rich.console import Console

from applypilot.config import APP_DIR
from applypilot.resume_json import build_resume_text_from_json

log = logging.getLogger(__name__)
console = Console()


def generate_track_base_resumes(resume_data: dict, tracks) -> list[Path]:
    """Generate a base resume per active track. Returns list of paths created."""
    track_dir = APP_DIR / "tracks"
    track_dir.mkdir(parents=True, exist_ok=True)
    paths = []

    for track in tracks:
        if not getattr(track, "active", True):
            continue

        track_skills = {s.lower() for s in getattr(track, "skills", [])}
        track_resume = copy.deepcopy(resume_data)

        # Reorder bullets: track-relevant first
        for job in track_resume.get("work", []):
            highlights = job.get("highlights", [])
            scored = []
            for h in highlights:
                h_lower = h.lower()
                relevance = sum(1 for s in track_skills if s in h_lower)
                scored.append((relevance, h))
            job["highlights"] = [h for _, h in sorted(scored, key=lambda x: -x[0])]

        # Filter skills to track-relevant groups
        track_resume["skills"] = [
                                     group
                                     for group in track_resume.get("skills", [])
                                     if any(kw.lower() in track_skills for kw in group.get("keywords", []))
                                 ] or track_resume.get("skills", [])  # keep all if no match

        name = getattr(track, "name", "unknown")
        tid = getattr(track, "track_id", "x")
        safe_name = name.lower().replace(" ", "_").replace("/", "_").replace("(", "").replace(")", "").strip("_")

        text = build_resume_text_from_json(track_resume)
        txt_path = track_dir / f"{tid}_{safe_name}.txt"
        txt_path.write_text(text, encoding="utf-8")

        json_path = txt_path.with_suffix(".json")
        json_path.write_text(json.dumps(track_resume, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        console.print(f"  [green]✓[/green] {name}: {txt_path.name}")
        paths.append(txt_path)

        # Update DB with base resume path
        try:
            from applypilot.bootstrap import get_app

            get_app().container.track_repo.update_base_resume_path(tid, str(txt_path))
        except Exception:
            pass

    return paths
