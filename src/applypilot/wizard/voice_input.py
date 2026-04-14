"""Voice/text input → structured resume content.

Two modes:
  1. --paste: User pastes a paragraph of free-form text about their experience
  2. --voice: Records audio via system mic, transcribes via Whisper, then processes

Both feed into the LLM to extract structured achievements and integrate into resume.json.
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

log = logging.getLogger(__name__)
console = Console()

_EXTRACT_PROMPT = """\
You are extracting professional achievements from a raw text dump. \
The person is describing their work experience in their own words — \
possibly rambling, unstructured, or in a conversational tone.

Raw input:
---
{raw_text}
---

Current resume context (for dedup — don't repeat what's already there):
{existing_summary}

Extract structured achievements and return a JSON object:
{{
  "achievements": [
    {{
      "company": "company name if mentioned",
      "role": "role/position if mentioned",
      "bullet": "Action-verb achievement bullet with quantified impact",
      "confidence": "high/medium/low — how clearly the person stated this"
    }}
  ],
  "skills_mentioned": ["skill1", "skill2"],
  "suggested_follow_ups": ["question to ask for more detail"]
}}

Rules:
- Use strong action verbs calibrated to seniority
- Quantify impact where numbers were given
- If they said "about 50%" use "~50%", don't fabricate exact numbers
- Mark confidence "low" if you're inferring rather than extracting
- Only return the JSON, nothing else."""


def _ask_llm(prompt: str) -> str:
    from applypilot.llm import get_client

    return get_client(tier="mid").chat(
        [{"role": "user", "content": prompt}],
        max_output_tokens=4096,
    )


def _existing_summary(resume_data: dict) -> str:
    """Brief summary of existing resume for dedup context."""
    bullets = []
    for job in resume_data.get("work", []):
        for h in job.get("highlights", [])[:2]:
            bullets.append(h[:80])
    return "; ".join(bullets[:10]) if bullets else "Empty resume"


def extract_from_text(raw_text: str, resume_data: dict) -> dict:
    """Extract structured achievements from free-form text."""
    prompt = _EXTRACT_PROMPT.format(
        raw_text=raw_text,
        existing_summary=_existing_summary(resume_data),
    )
    raw = _ask_llm(prompt)
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        return json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        log.warning("Failed to parse extraction: %s", raw[:200])
        return {"achievements": [], "skills_mentioned": [], "suggested_follow_ups": []}


def record_audio(duration: int = 30) -> str | None:
    """Record audio from mic and transcribe. Returns text or None."""
    try:
        import subprocess

        console.print(f"[cyan]🎤 Recording for {duration}s... (speak now, Ctrl+C to stop early)[/cyan]")

        # Record using macOS sox/rec or ffmpeg
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()

        try:
            subprocess.run(
                ["rec", "-q", tmp.name, "trim", "0", str(duration)],
                timeout=duration + 5,
            )
        except FileNotFoundError:
            # Fallback to ffmpeg
            try:
                subprocess.run(
                    [
                        "ffmpeg",
                        "-y",
                        "-f",
                        "avfoundation",
                        "-i",
                        ":0",
                        "-t",
                        str(duration),
                        "-ar",
                        "16000",
                        "-ac",
                        "1",
                        tmp.name,
                    ],
                    timeout=duration + 5,
                    capture_output=True,
                )
            except FileNotFoundError:
                console.print("[red]No audio recorder found.[/red] Install sox (`brew install sox`) or ffmpeg.")
                return None
        except KeyboardInterrupt:
            console.print("[dim]Recording stopped.[/dim]")

        audio_path = Path(tmp.name)
        if not audio_path.exists() or audio_path.stat().st_size < 1000:
            console.print("[yellow]Recording too short or empty.[/yellow]")
            return None

        console.print("[dim]Transcribing...[/dim]")
        return _transcribe(audio_path)

    except Exception as e:
        log.warning("Audio recording failed: %s", e)
        console.print(f"[red]Recording failed:[/red] {e}")
        return None


def _transcribe(audio_path: Path) -> str | None:
    """Transcribe audio file using local Whisper or API."""
    try:
        # Try local whisper first
        import whisper

        model = whisper.load_model("base")
        result = model.transcribe(str(audio_path))
        return result.get("text", "").strip()
    except ImportError:
        pass

    # Fallback: OpenAI Whisper API
    try:
        import os

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            console.print("[yellow]No whisper model or OPENAI_API_KEY for transcription.[/yellow]")
            console.print("[dim]Install: pip install openai-whisper[/dim]")
            return None

        import httpx

        with open(audio_path, "rb") as f:
            resp = httpx.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": ("audio.wav", f, "audio/wav")},
                data={"model": "whisper-1"},
                timeout=60,
            )
        resp.raise_for_status()
        return resp.json().get("text", "").strip()
    except Exception as e:
        log.warning("Transcription failed: %s", e)
        return None


def integrate_achievements(resume_data: dict, extracted: dict) -> dict:
    """Integrate extracted achievements into resume.json."""
    achievements = extracted.get("achievements", [])
    if not achievements:
        return resume_data

    work = resume_data.get("work", [])
    added = 0

    for ach in achievements:
        if ach.get("confidence") == "low":
            continue  # Skip low-confidence inferences

        bullet = ach.get("bullet", "").strip()
        if not bullet:
            continue

        company = (ach.get("company") or "").lower()

        # Find matching work entry
        matched = False
        for job in work:
            job_company = (job.get("name") or "").lower()
            if company and (company in job_company or job_company in company):
                job.setdefault("highlights", []).append(bullet)
                matched = True
                added += 1
                break

        # If no match, add to most recent role
        if not matched and work:
            work[0].setdefault("highlights", []).append(bullet)
            added += 1

    # Add new skills
    new_skills = extracted.get("skills_mentioned", [])
    if new_skills:
        skills = resume_data.setdefault("skills", [])
        existing_keywords = set()
        for s in skills:
            existing_keywords.update(k.lower() for k in s.get("keywords", []))

        for skill in new_skills:
            if skill.lower() not in existing_keywords:
                # Add to first skill group or create "Other"
                if skills:
                    skills[0].setdefault("keywords", []).append(skill)
                else:
                    skills.append({"name": "Skills", "keywords": [skill]})

    if added:
        console.print(f"[green]Added {added} achievement(s) to resume[/green]")

    return resume_data


def run_text_input(resume_data: dict) -> dict:
    """Paste mode — user dumps text, LLM extracts achievements."""
    console.print(
        Panel(
            "[bold]Paste Mode[/bold]\n\n"
            "Paste a paragraph about your work experience.\n"
            "Describe what you did, the impact, technologies used.\n"
            "The more detail, the better the resume bullets.\n\n"
            "[dim]Type 'done' on a new line when finished.[/dim]",
            border_style="cyan",
        )
    )

    lines = []
    while True:
        line = Prompt.ask("", default="")
        if line.strip().lower() == "done":
            break
        lines.append(line)

    raw_text = "\n".join(lines).strip()
    if not raw_text:
        console.print("[yellow]No text provided.[/yellow]")
        return resume_data

    console.print(f"\n[dim]Processing {len(raw_text)} characters...[/dim]")
    extracted = extract_from_text(raw_text, resume_data)

    achievements = extracted.get("achievements", [])
    if not achievements:
        console.print("[yellow]No achievements extracted.[/yellow]")
        return resume_data

    console.print(f"\n[cyan]Extracted {len(achievements)} achievement(s):[/cyan]")
    for i, ach in enumerate(achievements, 1):
        conf = ach.get("confidence", "?")
        color = "green" if conf == "high" else ("yellow" if conf == "medium" else "red")
        console.print(f"  [{color}]{conf}[/{color}] {ach.get('bullet', '?')}")

    if not Confirm.ask("\nIntegrate these into your resume?", default=True):
        return resume_data

    return integrate_achievements(resume_data, extracted)
