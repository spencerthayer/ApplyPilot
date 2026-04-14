"""Semantic guardrail — factual claim extraction + profile verification (LLD §7).

For LLM calls that generate new content (cover letters, profile enrichment).
Extracts factual claims from output and verifies each against user's profile data.
Ungrounded claims flagged as "AI-suggested." Fabrication rate > threshold → reject.
"""

from __future__ import annotations

import logging
import re

from applypilot.guardrails.statistical_guard import GuardrailResult

log = logging.getLogger(__name__)

# Patterns that indicate factual claims (numbers, company names, tool names)
_METRIC_PATTERN = re.compile(
    r"\b\d+[%xX+]?\b"  # numbers: 50%, 3x, 10+
    r"|\$[\d,.]+[KkMmBb]?"  # dollar amounts
    r"|\b\d+[,.]?\d*\s*(?:users?|customers?|clients?|employees?|TPS|QPS|RPS)\b"
    r"|\b(?:reduced|increased|improved|grew|saved|cut)\b.*?\b\d+",
    re.IGNORECASE,
)

_TOOL_PATTERN = re.compile(
    r"\b(?:Python|Java|TypeScript|JavaScript|React|Angular|Vue|Node\.?js|"
    r"AWS|GCP|Azure|Docker|Kubernetes|K8s|Terraform|"
    r"PostgreSQL|MySQL|MongoDB|Redis|Kafka|"
    r"TensorFlow|PyTorch|Spark|Airflow|"
    r"Git|Jenkins|CircleCI|GitHub Actions)\b",
    re.IGNORECASE,
)


def extract_claims(text: str) -> list[str]:
    """Extract factual claims (metrics, tools, company names) from generated text."""
    claims: list[str] = []

    for match in _METRIC_PATTERN.finditer(text):
        # Get surrounding context (the sentence containing the metric)
        start = max(0, text.rfind(".", 0, match.start()) + 1)
        end = text.find(".", match.end())
        if end == -1:
            end = min(len(text), match.end() + 100)
        claims.append(text[start:end].strip())

    for match in _TOOL_PATTERN.finditer(text):
        claims.append(match.group(0))

    return list(dict.fromkeys(claims))  # dedupe preserving order


def verify_claims(claims: list[str], profile: dict) -> tuple[list[str], list[str]]:
    """Verify claims against profile data.

    Returns (grounded_claims, ungrounded_claims).
    """
    # Build a searchable text from all profile data
    profile_text = _flatten_profile(profile).lower()

    grounded: list[str] = []
    ungrounded: list[str] = []

    for claim in claims:
        claim_lower = claim.lower().strip()
        if not claim_lower or len(claim_lower) < 3:
            continue
        # A claim is grounded if its key terms appear in the profile
        key_terms = [w for w in re.findall(r"[a-z0-9]+", claim_lower) if len(w) > 2]
        if not key_terms:
            continue
        matched = sum(1 for t in key_terms if t in profile_text)
        coverage = matched / len(key_terms) if key_terms else 0
        if coverage >= 0.5:
            grounded.append(claim)
        else:
            ungrounded.append(claim)

    return grounded, ungrounded


def check_semantic(
        output: str,
        profile: dict,
        max_fabrication_rate: float = 0.15,
) -> GuardrailResult:
    """Semantic guardrail: verify factual claims in generated content.

    Args:
        output: LLM-generated text (cover letter, enrichment, etc).
        profile: User's profile dict.
        max_fabrication_rate: Maximum allowed fraction of ungrounded claims.

    Returns:
        GuardrailResult with pass/fail and fabrication stats.
    """
    claims = extract_claims(output)
    if not claims:
        return GuardrailResult(
            passed=True,
            retention=1.0,
            threshold=max_fabrication_rate,
            detail="no factual claims detected",
        )

    grounded, ungrounded = verify_claims(claims, profile)
    total = len(grounded) + len(ungrounded)
    fabrication_rate = len(ungrounded) / total if total else 0.0

    passed = fabrication_rate <= max_fabrication_rate
    return GuardrailResult(
        passed=passed,
        retention=1.0 - fabrication_rate,
        threshold=1.0 - max_fabrication_rate,
        detail=(
                f"{len(ungrounded)}/{total} claims ungrounded ({fabrication_rate:.0%}), "
                f"threshold={max_fabrication_rate:.0%}" + (f" | ungrounded: {ungrounded[:3]}" if ungrounded else "")
        ),
    )


def _flatten_profile(profile: dict) -> str:
    """Recursively flatten profile dict into searchable text."""
    parts: list[str] = []

    def _walk(obj: object) -> None:
        match obj:
            case str():
                parts.append(obj)
            case dict():
                for v in obj.values():
                    _walk(v)
            case list():
                for item in obj:
                    _walk(item)

    _walk(profile)
    return " ".join(parts)
