"""Shared JSON utilities for LLM response parsing and JSON path resolution.

Used by smartextract, enrichment/detail.py, and resume_ingest.py.
Extracted from the monolith to avoid circular imports and enable reuse.
"""

import json
import re


def extract_json(text: str) -> dict:
    """Extract JSON from LLM response, handling think tags and code fences."""
    # Strip <think>...</think> blocks (reasoning models like DeepSeek)
    if "<think>" in text:
        after = text.split("</think>")[-1].strip()
        if after:
            text = after
    # Strip markdown code fences
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    text = text.strip()
    # Fix invalid escape sequences from LLMs
    text = re.sub(r'\\([^"\\\/bfnrtu])', r"\1", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Progressively trim trailing chars for truncated JSON
    while text.endswith("}") or text.endswith("]"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            text = text[:-1].rstrip()
    raise json.JSONDecodeError("Could not parse JSON", text, 0)


def resolve_json_path_raw(data, path: str):
    """Navigate a JSON path and return whatever is there (including lists/dicts)."""
    if not path or not data:
        return None
    try:
        current = data
        for part in path.replace("[", ".[").split("."):
            if not part:
                continue
            if part.startswith("[") and part.endswith("]"):
                idx = int(part[1:-1])
                current = current[idx]
            else:
                current = current[part]
        return current
    except (KeyError, IndexError, TypeError):
        return None


def resolve_json_path(data, path: str):
    """JSON path resolver with type coercion for display strings."""
    if not path or not data:
        return None
    try:
        current = data
        for part in path.replace("[", ".[").split("."):
            if not part:
                continue
            if part.startswith("[") and part.endswith("]"):
                idx = int(part[1:-1])
                current = current[idx]
            else:
                current = current[part]
        if isinstance(current, (str, int, float)):
            return str(current) if not isinstance(current, str) else current
        elif isinstance(current, dict):
            return current.get("name", current.get("text", str(current)[:100]))
        elif isinstance(current, list):
            if current and isinstance(current[0], dict):
                return ", ".join(str(item.get("name", item.get("text", ""))) for item in current[:3])
            return ", ".join(str(x) for x in current[:3])
        return str(current) if current else None
    except (KeyError, IndexError, TypeError):
        return None
