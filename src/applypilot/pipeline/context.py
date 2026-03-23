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
    dry_run: bool = False

    # Scope: None = batch (all pending jobs), set = single job
    job_url: str | None = None

    @property
    def is_single(self) -> bool:
        return self.job_url is not None
