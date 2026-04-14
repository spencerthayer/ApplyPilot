"""Metrics provenance validation module.

Loads verified metrics from the canonical ApplyPilot profile contract and validates
text against them to prevent metric fabrication.
"""

import json
import os
import re
from dataclasses import dataclass
from typing import List, Set

from applypilot.config import load_profile
from applypilot.resume.extraction import get_profile_verified_metrics
from applypilot.resume_json import normalize_profile_data


@dataclass
class ValidationResult:
    """Result of validating text against verified metrics."""

    valid_metrics: List[str]
    invalid_metrics: List[str]


class MetricsRegistry:
    """Registry of verified metrics loaded from user profile.

    Parses work[].key_metrics[] from the normalized profile contract to build
    a searchable registry of verified metrics.
    """

    # Regex patterns for extracting metrics from text
    PERCENT_PATTERN = re.compile(r"\d+%")
    DOLLAR_PATTERN = re.compile(r"\$[\d,]+(?:/month)?")
    MULTIPLIER_PATTERN = re.compile(r"\d+x")
    PLUS_PATTERN = re.compile(r"\d+\+")

    def __init__(self, profile_path: str | None = None) -> None:
        """Initialize registry by loading metrics from profile.

        Args:
            profile_path: Optional path to a profile.json or resume.json file.
        """
        self._verified_metrics: Set[str] = set()
        self._load_profile(profile_path)

    def _load_profile(self, path: str | None) -> None:
        """Load and parse metrics from canonical or legacy profile storage.

        Extracts metrics from normalized work[].key_metrics[] entries.
        """
        profile = {}
        if path:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    profile = normalize_profile_data(json.load(f))
            except (FileNotFoundError, json.JSONDecodeError):
                return
        else:
            try:
                profile = load_profile()
            except FileNotFoundError:
                legacy_path = os.path.expanduser("~/.applypilot/profile.json")
                try:
                    with open(legacy_path, "r", encoding="utf-8") as f:
                        profile = normalize_profile_data(json.load(f))
                except (FileNotFoundError, json.JSONDecodeError):
                    return

        for metric in get_profile_verified_metrics(profile):
            normalized = self._normalize_metric(metric)
            if normalized:
                self._verified_metrics.add(normalized)

    def _normalize_metric(self, metric: str) -> str:
        """Normalize a metric string for comparison.

        Converts to lowercase and removes extra whitespace.
        """
        return " ".join(metric.lower().split())

    def _extract_metric_signatures(self, text: str) -> List[str]:
        """Extract metric signatures from text.

        Finds percentages, dollar amounts, multipliers, and counts.
        Returns normalized signatures for matching.
        """
        signatures = []
        text_lower = text.lower()

        # Extract percentages (e.g., "400%", "125% YoY")
        for match in self.PERCENT_PATTERN.finditer(text_lower):
            # Get surrounding context (20 chars before and after)
            start = max(0, match.start() - 20)
            end = min(len(text_lower), match.end() + 20)
            context = text_lower[start:end]
            signatures.append(self._normalize_metric(context))

        # Extract dollar amounts (e.g., "$35,000", "$35,000/month")
        for match in self.DOLLAR_PATTERN.finditer(text_lower):
            start = max(0, match.start() - 20)
            end = min(len(text_lower), match.end() + 20)
            context = text_lower[start:end]
            signatures.append(self._normalize_metric(context))

        # Extract multipliers (e.g., "10x")
        for match in self.MULTIPLIER_PATTERN.finditer(text_lower):
            start = max(0, match.start() - 15)
            end = min(len(text_lower), match.end() + 15)
            context = text_lower[start:end]
            signatures.append(self._normalize_metric(context))

        # Extract counts with plus (e.g., "50+")
        for match in self.PLUS_PATTERN.finditer(text_lower):
            start = max(0, match.start() - 15)
            end = min(len(text_lower), match.end() + 15)
            context = text_lower[start:end]
            signatures.append(self._normalize_metric(context))

        return signatures

    def _is_metric_verified(self, signature: str) -> bool:
        """Check if a metric signature matches any verified metric.

        Uses substring matching to be lenient on formatting while
        remaining strict on values.
        """
        for verified in self._verified_metrics:
            # Check if signature is contained in verified or vice versa
            if signature in verified or verified in signature:
                return True
            # Check for overlapping numeric values
            if self._has_matching_number(signature, verified):
                return True
        return False

    def _has_matching_number(self, sig1: str, sig2: str) -> bool:
        """Check if two signatures share the same numeric value.

        Extracts numbers with their units (%, $, x) and compares.
        """
        # Extract numeric patterns
        nums1 = set(re.findall(r"\d+%|\$[\d,]+|\d+x|\d+\+", sig1))
        nums2 = set(re.findall(r"\d+%|\$[\d,]+|\d+x|\d+\+", sig2))

        # Check for any overlap
        return bool(nums1 & nums2)

    def validate_text(self, text: str) -> ValidationResult:
        """Validate text against verified metrics.

        Extracts all metrics from text and checks each against the registry.

        Args:
            text: Text to validate (e.g., resume bullet points)

        Returns:
            ValidationResult with valid_metrics and invalid_metrics lists
        """
        if not text:
            return ValidationResult(valid_metrics=[], invalid_metrics=[])

        signatures = self._extract_metric_signatures(text)

        valid = []
        invalid = []

        for sig in signatures:
            if self._is_metric_verified(sig):
                valid.append(sig)
            else:
                invalid.append(sig)

        return ValidationResult(valid_metrics=valid, invalid_metrics=invalid)

    def get_verified_metrics(self) -> List[str]:
        """Return all verified metrics as a list.

        Returns:
            List of all verified metric strings from the profile
        """
        return sorted(list(self._verified_metrics))

    def flag_missing_metrics(self, text: str) -> List[str]:
        """Identify metrics in text that are not in the verified registry.

        This helps flag potential fabrications for user review.

        Args:
            text: Text to analyze

        Returns:
            List of unverified metric signatures found in text
        """
        result = self.validate_text(text)
        return result.invalid_metrics
