"""Negotiation scripts — salary negotiation frameworks per offer.

Complements PPP salary intelligence with actionable talking points.
"""

from __future__ import annotations


def generate_scripts(target_salary: str, job_title: str, company: str, location: str = "") -> dict:
    """Generate negotiation scripts for a specific offer."""
    return {
        "salary_expectation": (
            f"Based on market data for {job_title} roles, I'm targeting {target_salary}. "
            "I'm flexible on structure — what matters is the total package and the opportunity."
        ),
        "geographic_pushback": (
            "The roles I'm competitive for are output-based, not location-based. "
            "My track record doesn't change based on postal code."
        ),
        "below_target": (
            f"I'm comparing with opportunities in a higher range. "
            f"I'm drawn to {company} because of the technical challenges. "
            f"Can we explore {target_salary}?"
        ),
        "competing_offer": (
            f"I have a competing offer at [X]. I'd prefer to join {company} — can we close the gap on compensation?"
        ),
        "downlevel_counter": (
            "I understand the level mapping. Can we agree on a 6-month review "
            "with clear criteria for promotion? I'd also like the scope to match "
            "the responsibilities we discussed."
        ),
    }
