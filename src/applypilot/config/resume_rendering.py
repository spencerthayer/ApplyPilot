"""Resume configuration system (INIT-19).

User-controlled resume settings with smart defaults + full override.
Presets auto-matched to job attributes.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ResumeConfig:
    """Resume rendering configuration."""

    max_pages: str = "auto"  # auto, 1, 2, 3, unlimited
    bullet_format: str = "star"  # star, plain, narrative
    section_order: list[str] = field(
        default_factory=lambda: [
            "summary",
            "experience",
            "skills",
            "projects",
            "education",
            "certificates",
        ]
    )
    section_visibility: dict[str, str] = field(
        default_factory=lambda: {
            "summary": "auto",
            "skills": "auto",
            "experience": "auto",
            "projects": "auto",
            "education": "auto",
            "certificates": "auto",
        }
    )


# Presets matched to job attributes
PRESETS: dict[str, ResumeConfig] = {
    "enterprise": ResumeConfig(max_pages="2", bullet_format="star"),
    "startup": ResumeConfig(max_pages="1", bullet_format="star", section_visibility={"certificates": "false"}),
    "eu_market": ResumeConfig(max_pages="2", bullet_format="star"),
    "academic": ResumeConfig(
        max_pages="unlimited",
        bullet_format="narrative",
        section_order=[
            "education",
            "experience",
            "projects",
            "skills",
            "certificates",
        ],
    ),
}


def resolve_config(job: dict | None = None, override: ResumeConfig | None = None) -> ResumeConfig:
    """Resolve resume config: user override > job-matched preset > default."""
    if override:
        return override

    if job:
        title = (job.get("title") or "").lower()
        company = (job.get("company") or "").lower()
        if any(w in title for w in ("professor", "researcher", "phd", "postdoc")):
            return PRESETS["academic"]
        if any(w in company for w in ("startup", "seed", "series a")):
            return PRESETS["startup"]

    return ResumeConfig()
