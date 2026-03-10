"""Quality gates for resume validation during tailoring."""

import re
from abc import ABC, abstractmethod
from typing import Any, Dict

from applypilot.llm import get_client
from applypilot.tailoring.models import GateResult, Resume


class QualityGate(ABC):
    """Abstract base class for quality gates."""

    @abstractmethod
    def check(self, resume: Resume, context: Dict[str, Any]) -> GateResult:
        """Evaluate the resume against this gate's criteria."""


class MetricsGate(QualityGate):
    """Checks that resume bullets contain quantifiable metrics."""

    METRIC_PATTERN = re.compile(r"\d+%?|\$[\d,.]+[KkMmBb]?|\d+\s*(million|billion|x|users|clients|projects)")

    def _has_metric(self, bullet: str) -> bool:
        return bool(self.METRIC_PATTERN.search(bullet))

    def check(self, resume: Resume, context: Dict[str, Any]) -> GateResult:
        """Check that >= 70% of experience bullets contain metrics."""
        bullets = self._extract_bullets(resume)

        if not bullets:
            return GateResult(
                passed=False,
                score=0.0,
                feedback="No experience bullets found in resume",
                retry_prompt="Add experience bullets with quantifiable metrics",
            )

        without_metrics = [b for b in bullets if not self._has_metric(b)]
        ratio = 1.0 - (len(without_metrics) / len(bullets))

        return GateResult(
            passed=ratio >= 0.7,
            score=ratio,
            feedback=f"{len(without_metrics)}/{len(bullets)} bullets lack metrics",
            retry_prompt="Add quantifiable metrics (%, $, numbers) to bullets" if ratio < 0.7 else None,
        )

    def _extract_bullets(self, resume: Resume) -> list:
        """Extract bullet points from resume EXPERIENCE section only."""
        bullets = []
        exp = resume.sections.get("EXPERIENCE")
        if isinstance(exp, dict) and "bullets" in exp:
            bullets.extend(exp["bullets"])
        elif isinstance(exp, list):
            bullets.extend(exp)
        return bullets


class RelevanceGate(QualityGate):
    """Checks resume relevance against job requirements using LLM."""

    def __init__(self) -> None:
        self.client = get_client()

    def check(self, resume: Resume, context: Dict[str, Any]) -> GateResult:
        """Score resume relevance to the target job."""
        job_intel = context.get("job_intelligence")

        if not job_intel:
            return GateResult(
                passed=True,
                score=1.0,
                feedback="No job intelligence available; skipping relevance check",
            )

        prompt = (
            f"Score the relevance of this resume to the job on a scale of 1-10.\n"
            f"Reply with ONLY a number between 1 and 10.\n\n"
            f"Job Title: {job_intel.title}\n"
            f"Job Company: {job_intel.company}\n\n"
            f"Resume (first 2000 chars):\n{resume.text[:2000]}"
        )

        try:
            response = self.client.ask(prompt, temperature=0.1)
            # Extract numeric score from response
            score_match = re.search(r"(\d+(?:\.\d+)?)", response.strip())
            raw_score = float(score_match.group(1)) if score_match else 5.0
            score = min(max(raw_score / 10.0, 0.0), 1.0)
        except Exception:
            # If LLM fails, pass with neutral score
            score = 0.5

        return GateResult(
            passed=score >= 0.7,
            score=score,
            feedback=f"Relevance score: {score:.1f}/1.0",
            retry_prompt="Improve alignment with job requirements" if score < 0.7 else None,
        )
