"""Pipeline context — shared state passed through stages."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PipelineContext:
    """Immutable-ish bag of config that every stage reads. No stage mutates this."""

    min_score: int = 7
    limit: int = 20
    workers: int = 1
    validation_mode: str = "normal"
    sources: list[str] | None = None
    companies: list[str] | None = None
    urls: list[str] | None = None
    strict_title: bool = False
    force: bool = False
    dry_run: bool = False

    @property
    def is_single(self) -> bool:
        return self.urls is not None and len(self.urls) == 1

    @property
    def job_url(self) -> str | None:
        """Backward compat — stages that read ctx.job_url still work."""
        if self.urls and len(self.urls) == 1:
            return self.urls[0]
        return None
