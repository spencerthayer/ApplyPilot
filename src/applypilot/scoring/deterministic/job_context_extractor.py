"""JD context extraction and prioritization."""

import re

__all__ = [
    "JOB_CONTEXT_PRIORITIES",
    "extract_requirement_focused_text",
]

JOB_CONTEXT_PRIORITIES: list[tuple[int, tuple[str, ...]]] = [
    (4, ("requirements", "minimum qualifications", "must have", "qualifications")),
    (3, ("preferred qualifications", "nice to have", "preferred", "bonus points")),
    (3, ("responsibilities", "what you'll do", "what you will do", "day to day")),
    (2, ("about the role", "role overview", "about this role")),
]


def extract_requirement_focused_text(description: str, max_chars: int = 6000) -> str:
    """Prefer requirements/qualifications/responsibilities when truncating long JDs."""

    cleaned = (description or "").strip()
    if len(cleaned) <= max_chars:
        return cleaned

    blocks = [block.strip() for block in re.split(r"\n\s*\n", cleaned) if block.strip()]
    if not blocks:
        return cleaned[:max_chars]

    scored: list[tuple[int, int, str]] = []
    for index, block in enumerate(blocks):
        lowered = block.lower()
        score = 1 if index == 0 else 0
        for weight, terms in JOB_CONTEXT_PRIORITIES:
            if any(term in lowered for term in terms):
                score += weight
        if re.search(r"\b(required|must|minimum|experience|skills?)\b", lowered):
            score += 1
        if block.count("\n-") + block.count("\n*") > 2:
            score += 1
        scored.append((score, index, block))

    selected_indexes: list[int] = []
    total = 0
    for _, index, block in sorted(scored, key=lambda item: (-item[0], item[1])):
        projected = total + len(block) + 2
        if projected > max_chars and selected_indexes:
            continue
        selected_indexes.append(index)
        total = projected
        if total >= max_chars:
            break

    selected_indexes = sorted(set(selected_indexes))
    sections = [blocks[index] for index in selected_indexes]
    focused = "\n\n".join(sections).strip()
    if len(focused) <= max_chars:
        return focused
    return focused[:max_chars]
