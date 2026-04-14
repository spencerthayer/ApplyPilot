"""Track archetype framing — what employers buy per track.

Maps track names to adaptive framing that drives proof point prioritization
in tailoring and interview prep.
"""

from __future__ import annotations

# Default framings — applied when tracks are discovered
TRACK_FRAMINGS: dict[str, str] = {
    "backend": "Someone who independently designs and ships production backend systems with measurable reliability",
    "android": "Someone who builds performant, maintainable Android apps with clean architecture and cross-platform thinking",
    "mobile": "Someone who delivers cross-platform mobile experiences with SDK design and platform-agnostic patterns",
    "devops": "Someone who automates infrastructure, CI/CD, and cloud deployments for faster, safer releases",
    "cloud": "Someone who architects scalable cloud-native systems with cost optimization and operational excellence",
    "frontend": "Someone who builds responsive, accessible UIs with modern frameworks and design system thinking",
    "fullstack": "Someone who owns features end-to-end from database to UI with production-grade quality",
    "ml": "Someone who puts ML models into production with evaluation, monitoring, and iterative improvement",
    "data": "Someone who builds reliable data pipelines and turns raw data into actionable insights",
    "platform": "Someone who builds internal tools and platforms that multiply engineering team productivity",
    "security": "Someone who identifies and mitigates security risks across the stack with defense-in-depth",
}


def get_framing(track_name: str) -> str:
    """Get framing for a track name. Fuzzy matches against known patterns."""
    name_lower = track_name.lower()
    for key, framing in TRACK_FRAMINGS.items():
        if key in name_lower:
            return framing
    return f"Someone who delivers production-quality {track_name} solutions with measurable impact"
