"""LLM score calibration — parse response, apply bounded delta."""

from __future__ import annotations

import json
import re

try:
    from applypilot.scoring.deterministic.baseline_scorer import HARD_MISMATCH_TERMS
except ImportError:  # baseline_scorer not yet extracted
    HARD_MISMATCH_TERMS = (
        "clearance",
        "active license",
        "board certification",
        "bar admission",
        "registered nurse",
        "rn license",
        "medical doctor",
        "cpa required",
        "citizenship required",
    )

__all__ = [
    "ScoreResponseParseError",
    "extract_json_object",
    "parse_score_response",
    "has_hard_mismatch_evidence",
    "apply_score_calibration",
]

_SHORT_REASON_WORD_RE = re.compile(r"[A-Za-z0-9+#./'-]+")


# ── Exception ─────────────────────────────────────────────────────────────


class ScoreResponseParseError(ValueError):
    """Raised when LLM score response does not satisfy the scoring JSON schema."""

    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


# ── Private helpers ───────────────────────────────────────────────────────


def _coerce_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _coerce_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value in (None, ""):
        return []
    return [str(value).strip()]


def _normalize_short_reason(text: str) -> str:
    words = _SHORT_REASON_WORD_RE.findall((text or "").strip())
    if len(words) < 3:
        return ""
    if len(words) > 9:
        words = words[:9]
    return " ".join(words)


def _derive_short_reason(reasoning: str) -> str:
    text = re.sub(r"\s+", " ", (reasoning or "")).strip()
    if not text:
        return "Mixed fit with notable gaps"
    first_sentence = re.split(r"[.!?]\s+", text, maxsplit=1)[0].strip()
    candidate = first_sentence or text
    normalized = _normalize_short_reason(candidate)
    if normalized:
        return normalized

    lowered = text.lower()
    if any(token in lowered for token in ("strong fit", "excellent", "high fit", "good fit")):
        return "Strong fit with clear overlap"
    if any(token in lowered for token in ("poor fit", "weak fit", "mismatch", "not a fit")):
        return "Weak fit with major gaps"
    if any(token in lowered for token in ("moderate fit", "mixed fit", "partial fit")):
        return "Moderate fit with notable gaps"
    return "Mixed fit with notable gaps"


# ── Public API ────────────────────────────────────────────────────────────


def extract_json_object(text: str) -> dict:
    """Extract and parse a JSON object from raw LLM text."""
    payload = (text or "").strip()
    if not payload:
        raise ScoreResponseParseError("empty_response", "LLM returned an empty response.")

    # Strip reasoning model thinking tags (Qwen3, DeepSeek R1, etc.)
    payload = re.sub(r"<think>.*?</think>", "", payload, flags=re.DOTALL).strip()

    fenced_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", payload, re.DOTALL | re.IGNORECASE)
    if fenced_match:
        payload = fenced_match.group(1).strip()

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        object_match = re.search(r"\{.*\}", payload, re.DOTALL)
        if not object_match:
            raise ScoreResponseParseError("missing_json_object", "No JSON object found in model response.")
        try:
            parsed = json.loads(object_match.group(0))
        except json.JSONDecodeError as exc:
            raise ScoreResponseParseError(
                "invalid_json",
                f"Could not parse JSON object: {exc.msg} at line {exc.lineno}, column {exc.colno}",
            ) from exc

    if not isinstance(parsed, dict):
        raise ScoreResponseParseError("invalid_shape", "JSON response must be an object.")
    return parsed


def parse_score_response(response: str) -> dict:
    """Parse and validate the strict scoring JSON schema."""
    data = extract_json_object(response)
    if "score" not in data:
        raise ScoreResponseParseError("missing_score", "Response JSON did not include a 'score' field.")

    try:
        score = int(round(float(data["score"])))
    except (TypeError, ValueError) as exc:
        raise ScoreResponseParseError("invalid_score_type", "Score must be numeric.") from exc
    score = max(1, min(10, score))

    confidence_raw = data.get("confidence", 0.5)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    matched_skills = _coerce_list(data.get("matched_skills"))
    missing_requirements = _coerce_list(data.get("missing_requirements"))
    reasoning = _coerce_text(data.get("reasoning")) or "No reasoning provided by model."
    why_short_raw = _coerce_text(data.get("why_short") or data.get("reasoning_short") or data.get("summary_short"))
    if why_short_raw:
        why_short = _normalize_short_reason(why_short_raw) or _derive_short_reason(reasoning)
    else:
        why_short = _derive_short_reason(reasoning)

    return {
        "score": score,
        "confidence": confidence,
        "why_short": why_short,
        "matched_skills": matched_skills[:12],
        "missing_requirements": missing_requirements[:12],
        "reasoning": reasoning,
    }


def has_hard_mismatch_evidence(baseline: dict, missing_requirements: list[str], job_text: str) -> bool:
    """Check whether baseline signals or missing requirements indicate a hard mismatch."""
    if float(baseline.get("domain_mismatch_penalty") or 0.0) >= 3.0:
        return True
    evidence_blob = " ".join(missing_requirements + [job_text]).lower()
    return any(term in evidence_blob for term in HARD_MISMATCH_TERMS)


def apply_score_calibration(
        baseline: dict,
        llm_score: int,
        confidence: float,
        matched_skills: list[str],
        missing_requirements: list[str],
        job_context: str,
) -> tuple[int, int]:
    """Apply bounded LLM delta to deterministic baseline. Returns (calibrated, delta)."""
    baseline_score = int(baseline.get("score", 0))
    bounded_llm_score = max(1, min(10, int(llm_score)))
    bounded_confidence = max(0.0, min(1.0, float(confidence)))
    matched_count = int(baseline.get("matched_skill_count") or len(matched_skills))
    missing_count = max(int(baseline.get("missing_requirement_count") or len(missing_requirements)), 0)
    skill_overlap = max(0.0, min(1.0, float(baseline.get("skill_overlap") or 0.0)))
    title_similarity = max(0.0, min(1.0, float(baseline.get("title_similarity") or 0.0)))
    if matched_count + missing_count > 0:
        requirement_coverage = matched_count / (matched_count + missing_count)
    else:
        requirement_coverage = skill_overlap

    max_delta = 2
    if bounded_confidence >= 0.85 and (len(matched_skills) >= 5 or len(missing_requirements) >= 4):
        max_delta = 3

    delta = bounded_llm_score - baseline_score
    delta = max(-max_delta, min(max_delta, delta))
    calibrated = max(1, min(10, baseline_score + delta))

    hard_mismatch = has_hard_mismatch_evidence(baseline, missing_requirements, job_context)
    if hard_mismatch and bounded_confidence >= 0.8 and bounded_llm_score <= 2:
        calibrated = min(calibrated, 2)

    # Generic floor based on measurable overlap; no role-specific hardcoded buckets.
    evidence_score = max(
        max(0.0, min(1.0, float(baseline.get("evidence_strength") or 0.0))),
        max(
            0.0,
            min(
                1.0,
                0.45 * skill_overlap
                + 0.30 * min(1.0, matched_count / 6.0)
                + 0.20 * requirement_coverage
                + 0.05 * title_similarity,
            ),
        ),
    )
    if not hard_mismatch and evidence_score >= 0.35:
        dynamic_floor = max(3, min(5, int(round(1.0 + 5.0 * evidence_score))))
        calibrated = max(calibrated, dynamic_floor)

    return calibrated, calibrated - baseline_score
