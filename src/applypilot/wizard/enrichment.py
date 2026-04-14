"""AI-guided resume enrichment — surfaces hidden achievements via targeted questions.

After resume import, the LLM analyzes the profile and asks follow-up questions
to surface quantified impact, ownership stories, and technical depth that
the user didn't think to include.
"""

from __future__ import annotations

import json
import logging

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

log = logging.getLogger(__name__)
console = Console()

_ANALYSIS_PROMPT = """\
You are a career coach analyzing a resume to find gaps where the candidate \
likely has achievements they haven't mentioned. The resume is in JSON Resume format.

Resume:
{resume_json}

Analyze this resume and generate exactly {num_questions} targeted follow-up questions. \
Each question should:
1. Reference a SPECIFIC role or project from their resume
2. Ask about quantifiable impact they likely had but didn't mention
3. Be simple and non-intimidating (8th grade reading level)
4. Focus on: metrics (%, $, users, time saved), ownership scope, team size, \
technical decisions, business outcomes

Return a JSON array of objects:
[
  {{
    "question": "the question to ask",
    "context": "which role/project this relates to",
    "hint": "example of what a good answer looks like"
  }}
]

Only return the JSON array, nothing else."""

_INTEGRATE_PROMPT = """\
You are updating a JSON Resume with new information from the candidate.

Current resume:
{resume_json}

The candidate provided these answers to follow-up questions:

{qa_pairs}

Rules:
1. Integrate the answers into the appropriate work/project highlights
2. Use strong action verbs calibrated to their seniority
3. Quantify impact wherever the candidate provided numbers
4. NEVER fabricate — only use information the candidate actually provided
5. Keep existing highlights that weren't addressed by the Q&A
6. Return the complete updated JSON Resume

Return ONLY the updated JSON Resume object, nothing else."""


def _ask_llm(system_prompt: str, user_content: str) -> str:
    from applypilot.llm import get_client

    client = get_client(tier="mid")
    return client.chat(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        max_output_tokens=4096,
    )


def analyze_and_ask(resume_data: dict, num_questions: int = 5) -> list[dict]:
    """Generate targeted follow-up questions based on resume analysis."""
    system_prompt = _ANALYSIS_PROMPT.replace("\n\nResume:\n{resume_json}", "").replace(
        "{num_questions}", str(num_questions)
    )
    user_content = json.dumps(resume_data, indent=2, ensure_ascii=False)
    raw = _ask_llm(system_prompt, user_content)

    # Extract JSON array from response
    try:
        start = raw.index("[")
        end = raw.rindex("]") + 1
        return json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        log.warning("Failed to parse LLM questions: %s", raw[:200])
        return []


def integrate_answers(resume_data: dict, qa_pairs: list[dict]) -> dict:
    """Integrate Q&A answers back into the resume.

    AI-enhanced content is flagged in meta.applypilot.ai_suggested (INIT-04).
    """
    formatted = "\n\n".join(f"Q: {qa['question']}\nA: {qa['answer']}" for qa in qa_pairs if qa.get("answer"))
    if not formatted:
        return resume_data

    system_prompt = _INTEGRATE_PROMPT.replace("\n\nCurrent resume:\n{resume_json}", "").replace("\n\n{qa_pairs}", "")
    user_content = (
            "Current resume:\n"
            + json.dumps(resume_data, indent=2, ensure_ascii=False)
            + "\n\nCandidate answers:\n"
            + formatted
    )
    raw = _ask_llm(system_prompt, user_content)

    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        updated = json.loads(raw[start:end])
        # Preserve meta section (applypilot settings)
        if "meta" in resume_data:
            updated["meta"] = resume_data["meta"]
        # Flag AI-suggested content (INIT-04)
        _flag_ai_suggested(resume_data, updated, qa_pairs)
        return updated
    except (ValueError, json.JSONDecodeError):
        log.warning("Failed to parse LLM integration: %s", raw[:200])
        return resume_data


def _flag_ai_suggested(original: dict, updated: dict, qa_pairs: list[dict]) -> None:
    """Mark new/changed highlights as AI-suggested in meta.applypilot (INIT-04)."""
    original_bullets = set()
    for job in original.get("work", []):
        for h in job.get("highlights", []):
            original_bullets.add(h.strip())

    ai_suggested = []
    for job in updated.get("work", []):
        company = job.get("name", "unknown")
        for h in job.get("highlights", []):
            if h.strip() not in original_bullets:
                ai_suggested.append(
                    {
                        "section": "work",
                        "company": company,
                        "text": h[:100],
                        "source": "enrichment",
                    }
                )

    if ai_suggested:
        meta = updated.setdefault("meta", {})
        ap = meta.setdefault("applypilot", {})
        existing = ap.get("ai_suggested", [])
        ap["ai_suggested"] = existing + ai_suggested


def run_enrichment_interview(resume_data: dict) -> dict:
    """Interactive enrichment: LLM asks questions, user answers, resume updated.

    Returns the enriched resume_data.
    """
    console.print(
        Panel(
            "[bold]AI Resume Enrichment[/bold]\n\n"
            "I'll analyze your resume and ask targeted questions to surface\n"
            "achievements you may not have thought to include.\n"
            "Press Enter to skip any question.",
            border_style="cyan",
        )
    )

    if not Confirm.ask("Ready to start?", default=True):
        return resume_data

    console.print("\n[dim]Analyzing your resume...[/dim]")
    questions = analyze_and_ask(resume_data)

    if not questions:
        console.print("[yellow]Couldn't generate questions. Skipping enrichment.[/yellow]")
        return resume_data

    console.print(f"\n[cyan]I have {len(questions)} questions to strengthen your resume:[/cyan]\n")

    qa_pairs = []
    for i, q in enumerate(questions, 1):
        context = q.get("context", "")
        hint = q.get("hint", "")
        question = q.get("question", "")

        if context:
            console.print(f"[dim]  Re: {context}[/dim]")
        console.print(f"[bold cyan]Q{i}:[/bold cyan] {question}")
        if hint:
            console.print(f"[dim]  Example: {hint}[/dim]")

        answer = Prompt.ask("  Your answer", default="")
        if answer.strip():
            qa_pairs.append({"question": question, "answer": answer.strip()})
            console.print("  [green]✓ Captured[/green]")
        else:
            console.print("  [dim]Skipped[/dim]")
        console.print()

    if not qa_pairs:
        console.print("[yellow]No answers provided. Resume unchanged.[/yellow]")
        return resume_data

    console.print(f"[dim]Integrating {len(qa_pairs)} answer(s) into your resume...[/dim]")
    enriched = integrate_answers(resume_data, qa_pairs)

    # Show what changed
    old_bullets = sum(len(j.get("highlights", [])) for j in resume_data.get("work", []))
    new_bullets = sum(len(j.get("highlights", [])) for j in enriched.get("work", []))
    delta = new_bullets - old_bullets

    console.print(f"[green]✓ Resume enriched:[/green] {new_bullets} bullets ({'+' if delta >= 0 else ''}{delta})")
    return enriched
