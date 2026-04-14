"""Apply result categorization — semantic bucketing of status + error.

Extracted from database.py. Pure function, no DB access.
"""

from __future__ import annotations

_AUTH_ERRORS = frozenset(
    {
        "workday_login_required",
        "login_issue",
        "login_required",
        "email_verification",
        "account_required",
        "sso_required",
        "account_creation_broken",
    }
)

_INELIGIBLE_ERRORS = frozenset(
    {
        "not_eligible_location",
        "not_eligible_salary",
        "contract_only",
    }
)

_EXPIRED_ERRORS = frozenset({"expired", "already_applied"})

_PLATFORM_ERRORS = frozenset(
    {
        "not_a_job_application",
        "unsafe_permissions",
        "unsafe_verification",
        "site_blocked",
        "cloudflare_blocked",
        "blocked_by_cloudflare",
    }
)

_NO_URL_ERRORS = frozenset({"no_external_url"})


def categorize_apply_result(apply_status: str | None, apply_error: str | None) -> str:
    """Derive a semantic apply category from status + error."""
    match apply_status:
        case None:
            return "pending"
        case "applied":
            return "applied"
        case "in_progress":
            return "in_progress"
        case "needs_human":
            return "needs_human"
        case "manual":
            error = apply_error or "unknown"
            if error in _AUTH_ERRORS:
                return "blocked_auth"
            if error in _NO_URL_ERRORS:
                return "archived_no_url"
            return "manual_only"
        case _:
            error = apply_error or "unknown"
            if error in _AUTH_ERRORS:
                return "blocked_auth"
            if error in _INELIGIBLE_ERRORS:
                return "archived_ineligible"
            if error in _EXPIRED_ERRORS:
                return "archived_expired"
            if error in _PLATFORM_ERRORS:
                return "archived_platform"
            if error in _NO_URL_ERRORS:
                return "archived_no_url"
            return "blocked_technical"
