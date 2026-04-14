"""LLM provider — generates and validates adjacency edges for skills.

Two-pass approach:
  1. Generate raw edges from user's known skills
  2. Validate + audit edges to remove false positives and add missing intermediates
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

_GENERATE_PROMPT = """\
For each skill below, list 5-6 related professional skills with a confidence \
score (0.5-0.95) and relationship type.

Include:
- Frameworks/tools commonly used WITH this skill
- Alternative/competing skills that serve the same purpose
- Cross-language or cross-domain equivalents (transferable skill bridges)
- Ecosystem skills (what else does someone with this skill typically know?)

Skills: {skills}

Return a JSON object:
{{
  "skill_name": [
    {{"target": "related_skill", "confidence": 0.8, "relation": "framework"}},
    {{"target": "alternative", "confidence": 0.6, "relation": "alternative"}},
    {{"target": "ecosystem_tool", "confidence": 0.7, "relation": "ecosystem"}}
  ]
}}

Rules:
- All keys and targets lowercase with underscores
- Cover ALL {count} input skills
- Include transferable skill bridges across languages/domains
- Return ONLY valid JSON, no markdown"""

_VALIDATE_PROMPT = """\
You are a skill graph auditor. Given a candidate's KNOWN skills and a set of \
INFERRED skill edges, validate each edge for precision.

KNOWN SKILLS (ground truth — candidate actually has these):
{known_skills}

INFERRED EDGES TO VALIDATE:
{edges_json}

For each edge, evaluate:
1. Is this a HARD_DEPENDENCY, STRONG_PREREQUISITE, RELATED, TOOLING_OVERLAP, or COINCIDENTAL?
2. Is the confidence score realistic? Adjust if needed.
3. Should intermediate skills exist? (e.g. Spring Boot requires Java + backend concepts)

Strict rules:
- Do NOT assume transitivity blindly
- Do NOT equate exposure with proficiency
- Knowing Python does NOT imply Pandas or ML unless supported by evidence
- A base language does NOT imply advanced frameworks at high confidence
- Prefer precision over recall — remove false positives
- If candidate knows Flask but NOT Spring Boot, the edge flask→spring_boot \
should be "RELATED" at 0.5-0.6, NOT "framework" at 0.8

Return JSON:
{{
  "validated_edges": [
    {{"from": "skill_a", "to": "skill_b", "confidence": 0.7, "relation": "STRONG_PREREQUISITE"}},
    ...
  ],
  "removed": [
    {{"from": "skill_a", "to": "skill_b", "reason": "overgeneralized — no evidence of usage"}}
  ],
  "missing_intermediates": [
    {{"from": "skill_a", "to": "skill_b", "intermediate": "skill_c", "reason": "..."}}
  ]
}}

Return ONLY valid JSON."""


def generate_adjacencies(skills: list[str]) -> dict[str, list[tuple[str, float, str]]]:
    """Generate + validate adjacency edges. Two LLM passes for precision."""
    if not skills:
        return {}

    # Pass 1: Generate raw edges
    raw_result: dict[str, list[tuple[str, float, str]]] = {}
    for i in range(0, len(skills), 15):
        batch = skills[i: i + 15]
        raw_result.update(_generate_batch(batch))

    if not raw_result:
        return raw_result

    # Pass 2: Validate edges against known skills
    validated = _validate_edges(skills, raw_result)
    return validated if validated else raw_result


def _generate_batch(skills: list[str]) -> dict[str, list[tuple[str, float, str]]]:
    try:
        from applypilot.llm import get_client

        prompt = _GENERATE_PROMPT.format(skills=", ".join(skills), count=len(skills))
        # Retry once on parse failure — cheap models sometimes produce malformed JSON
        for attempt in range(2):
            raw = get_client(tier="mid").chat(
                [
                    {
                        "role": "system",
                        "content": "You are a professional skill taxonomy expert. Return ONLY valid JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                max_output_tokens=3000,
            )
            result = _parse_edges_response(raw)
            if result:
                return result
            log.warning("Adjacency batch attempt %d failed to parse, retrying...", attempt + 1)
        return {}
    except Exception as e:
        log.warning("LLM adjacency generation failed: %s", e)
        return {}


def _validate_edges(
        known_skills: list[str],
        raw_edges: dict[str, list[tuple[str, float, str]]],
) -> dict[str, list[tuple[str, float, str]]] | None:
    """Validate raw edges against known skills. Returns corrected edges."""
    try:
        from applypilot.llm import get_client

        # Flatten edges for validation
        flat = []
        for src, edges in raw_edges.items():
            for target, conf, rel in edges:
                flat.append({"from": src, "to": target, "confidence": conf, "relation": rel})

        # Batch validate (max ~50 edges per call)
        all_validated: list[dict] = []
        all_removed: list[dict] = []
        for i in range(0, len(flat), 50):
            batch = flat[i: i + 50]
            prompt = _VALIDATE_PROMPT.format(
                known_skills=", ".join(known_skills),
                edges_json=json.dumps(batch, indent=1),
            )
            raw = get_client(tier="mid").chat(
                [
                    {
                        "role": "system",
                        "content": "You are a skill graph auditor. Be conservative — precision over recall.",
                    },
                    {"role": "user", "content": prompt},
                ],
                max_output_tokens=3000,
            )
            parsed = _parse_json(raw)
            if parsed:
                all_validated.extend(parsed.get("validated_edges", []))
                all_removed.extend(parsed.get("removed", []))

                # Add intermediate skills as new edges
                for m in parsed.get("missing_intermediates", []):
                    inter = m.get("intermediate", "")
                    if inter:
                        all_validated.append(
                            {
                                "from": m["from"],
                                "to": inter,
                                "confidence": 0.8,
                                "relation": "STRONG_PREREQUISITE",
                            }
                        )
                        all_validated.append(
                            {
                                "from": inter,
                                "to": m["to"],
                                "confidence": 0.7,
                                "relation": "STRONG_PREREQUISITE",
                            }
                        )

        if all_removed:
            log.info("Adjacency audit: removed %d false positives", len(all_removed))
        if all_validated:
            log.info("Adjacency audit: validated %d edges", len(all_validated))

        # Rebuild result dict
        result: dict[str, list[tuple[str, float, str]]] = {}
        for e in all_validated:
            src = str(e.get("from", "")).lower().replace(" ", "_")
            tgt = str(e.get("to", "")).lower().replace(" ", "_")
            conf = float(e.get("confidence", 0.5))
            rel = str(e.get("relation", "related")).lower()
            if src and tgt:
                result.setdefault(src, []).append((tgt, conf, rel))
        return result if result else None

    except Exception as e:
        log.warning("Adjacency validation failed (using raw): %s", e)
        return None


def _parse_edges_response(raw: str) -> dict[str, list[tuple[str, float, str]]]:
    """Parse LLM response into edges dict."""
    data = _parse_json(raw)
    if not data:
        return {}

    # Handle both flat and nested formats
    if "validated_edges" in data:
        data = {e["from"]: [] for e in data["validated_edges"]}
        for e in data.get("validated_edges", []):
            data.setdefault(e["from"], []).append(e)

    result: dict[str, list[tuple[str, float, str]]] = {}
    for skill, edges in data.items():
        if not isinstance(edges, list):
            continue
        result[skill.lower().replace(" ", "_")] = [
            (
                str(e.get("target", "")).lower().replace(" ", "_"),
                float(e.get("confidence", 0.7)),
                str(e.get("relation", "related")),
            )
            for e in edges
            if isinstance(e, dict) and e.get("target")
        ]
    return result


def _parse_json(raw: str) -> dict | None:
    """Extract JSON from LLM response — handles markdown fences, thinking tags, preamble."""
    import re

    text = raw.strip()
    # Strip thinking tags (Qwen3, DeepSeek R1)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    # Strip markdown fences
    if "```" in text:
        parts = text.split("```")
        for part in parts[1::2]:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            try:
                return json.loads(part)
            except json.JSONDecodeError:
                continue
    # Find outermost { ... }
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        return json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError) as e:
        log.warning("JSON parse failed: %s — response: %s", e, text[:200])
        return None
