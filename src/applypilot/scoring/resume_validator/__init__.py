"""Resume validation framework — deterministic checks for tailored resumes.

Decomposed into:
  - models.py:  ValidationResult, ValidationConfig
  - checks.py:  8 individual check functions (pure functions)
  - __init__.py: ResumeValidator class + convenience entry points
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from applypilot.scoring.resume_validator.models import ValidationConfig, ValidationResult
from applypilot.scoring.resume_validator.checks import (
    check_role_completeness,
    check_project_completeness,
    check_bullet_counts,
    check_total_bullets,
    check_summary_quality,
    check_bullet_metrics,
    check_weak_verbs,
    check_education_completeness,
)

# Re-export for backward compat
__all__ = [
    "ResumeValidator",
    "ValidationResult",
    "ValidationConfig",
    "validate_resume",
    "validate_resume_with_retry",
    "check_role_completeness",
    "check_project_completeness",
    "check_bullet_counts",
    "check_total_bullets",
    "check_summary_quality",
    "check_bullet_metrics",
    "check_weak_verbs",
    "check_education_completeness",
]

log = logging.getLogger(__name__)


class ResumeValidator:
    """Runs validation checks against resume data, generates retry instructions."""

    DEFAULT_CHECKS: list[Callable] = [
        check_role_completeness,
        check_project_completeness,
        check_bullet_counts,
        check_total_bullets,
        check_summary_quality,
        check_bullet_metrics,
        check_weak_verbs,
        check_education_completeness,
    ]

    def __init__(self, profile: dict, config: dict):
        self.profile = profile
        self.config = config
        self.validation_config = ValidationConfig.from_config(config)

    def validate(self, resume_data: dict, selected_checks: Optional[list[Callable]] = None) -> dict[str, Any]:
        if not self.validation_config.enabled:
            return {
                "passed": True,
                "results": [],
                "all_errors": [],
                "all_warnings": [],
                "retry_prompt": "",
                "failed_checks": [],
                "check_metadata": {},
            }

        checks = selected_checks or self.DEFAULT_CHECKS
        results = []
        for check_func in checks:
            try:
                results.append(check_func(resume_data, self.profile, self.validation_config))
            except Exception as e:
                log.exception("Validation check %s failed", check_func.__name__)
                results.append(
                    ValidationResult(
                        passed=False,
                        check_name=check_func.__name__,
                        errors=[f"Check failed: {e}"],
                        retry_instructions=["Review resume structure"],
                    )
                )

        all_passed = all(r.passed for r in results)
        failed = [r for r in results if not r.passed]
        retry_sections = [r.to_retry_prompt() for r in failed if r.retry_instructions]

        return {
            "passed": all_passed,
            "results": results,
            "all_errors": [e for r in results for e in r.errors],
            "all_warnings": [w for r in results for w in r.warnings],
            "retry_prompt": "\n\n---\n\n".join(retry_sections),
            "failed_checks": [r.check_name for r in failed],
            "check_metadata": {r.check_name: r.metadata for r in results if r.metadata},
        }

    def validate_with_retry(
            self, resume_data: dict, tailoring_func: Callable[[dict, str], dict], max_retries: Optional[int] = None
    ) -> dict[str, Any]:
        max_retries = max_retries or self.validation_config.max_retries
        attempts = []
        current_data = resume_data
        validation = None

        for attempt in range(max_retries + 1):
            validation = self.validate(current_data)
            attempts.append(
                {
                    "attempt": attempt,
                    "passed": validation["passed"],
                    "error_count": len(validation["all_errors"]),
                    "failed_checks": validation["failed_checks"],
                }
            )
            if validation["passed"]:
                return {
                    "success": True,
                    "resume_data": current_data,
                    "attempts": attempts,
                    "final_validation": validation,
                    "exhausted": False,
                }
            if attempt >= max_retries or not validation["retry_prompt"]:
                break
            try:
                current_data = tailoring_func(current_data, validation["retry_prompt"])
            except Exception:
                log.exception("Tailoring retry failed on attempt %d", attempt + 1)
                break

        return {
            "success": False,
            "resume_data": current_data,
            "attempts": attempts,
            "final_validation": validation,
            "exhausted": True,
        }


def validate_resume(resume_data: dict, profile: dict, config: Optional[dict] = None) -> dict[str, Any]:
    config = config or profile.get("tailoring_config", {})
    return ResumeValidator(profile, config).validate(resume_data)


def validate_resume_with_retry(
        resume_data: dict,
        profile: dict,
        tailoring_func: Callable[[dict, str], dict],
        config: Optional[dict] = None,
        max_retries: Optional[int] = None,
) -> dict[str, Any]:
    config = config or profile.get("tailoring_config", {})
    return ResumeValidator(profile, config).validate_with_retry(resume_data, tailoring_func, max_retries)
