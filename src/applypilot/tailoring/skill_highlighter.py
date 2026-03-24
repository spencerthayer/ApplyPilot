"""Skill highlighter — bolds LLM-specified skill keywords in bullet text.

Purpose: The LLM returns each bullet as {text, skills} — it knows which words
are real skills from context. We wrap those exact words in <strong> tags so the
PDF renderer bolds them. This proves skills are used in real work (not just listed),
letting the skills section be condensed to free page space for more bullets.

SRP: Only wraps specified words in HTML tags. Does not decide what to bold,
does not render, does not call LLMs.
"""

from __future__ import annotations

import re


def highlight(text: str, skills: list[str], tag: str = "strong") -> str:
    """Wrap LLM-specified skill words in HTML tags within bullet text.

    Args:
        text: Bullet point plain text.
        skills: Exact skill words the LLM identified in this bullet.
        tag: HTML tag to wrap with. Default "strong" for bold.

    Returns:
        Text with skills wrapped in <tag>...</tag>.
    """
    if not skills or not text:
        return text

    # Sort longest-first so "Kotlin Coroutines" matches before "Kotlin"
    sorted_skills = sorted(skills, key=len, reverse=True)
    pattern = "|".join(re.escape(s) for s in sorted_skills)
    regex = re.compile(rf"\b({pattern})\b", re.IGNORECASE)

    return regex.sub(lambda m: f"<{tag}>{m.group(0)}</{tag}>", text)


def parse_bullet(bullet) -> tuple[str, list[str]]:
    """Normalize a bullet into (text, skills) regardless of LLM output format.

    Handles both formats:
      - str: "Built SDK using CameraX" → (text, [])
      - dict: {"text": "Built SDK using CameraX", "skills": ["CameraX"]} → (text, skills)

    Purpose: Backward compat — old LLM responses return plain strings,
    new ones return {text, skills}. Callers don't need to care.

    Args:
        bullet: Raw bullet from LLM JSON output.

    Returns:
        (plain_text, skill_list) tuple.
    """
    if isinstance(bullet, dict):
        text = bullet.get("text", "")
        skills = bullet.get("skills", [])
        return text, skills
    return str(bullet).strip(), []
