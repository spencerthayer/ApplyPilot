"""Pipeline status state machine (LLD §13.2).

Explicit status tracking replaces NULL-column inference.
Each job transitions through these states as it moves through the pipeline.
"""

from __future__ import annotations

from enum import StrEnum


class PipelineStatus(StrEnum):
    """Job lifecycle states — durable in SQLite, crash-resumable."""

    DISCOVERED = "discovered"
    ENRICHED = "enriched"
    ENRICHMENT_FAILED = "enrichment_failed"
    SCORED = "scored"
    EXCLUDED = "excluded"
    TAILORED = "tailored"
    SKIPPED = "skipped"  # TL0: score too low for tailoring
    COVER_DONE = "cover_done"
    READY_TO_APPLY = "ready"
    IN_PROGRESS = "in_progress"
    APPLIED = "applied"
    FAILED = "failed"
    NEEDS_HUMAN = "needs_human"

    @staticmethod
    def is_terminal(status: str) -> bool:
        return status in {
            PipelineStatus.APPLIED,
            PipelineStatus.EXCLUDED,
            PipelineStatus.SKIPPED,
        }

    @staticmethod
    def is_retryable(status: str) -> bool:
        return status in {
            PipelineStatus.FAILED,
            PipelineStatus.ENRICHMENT_FAILED,
        }


# Valid transitions — enforced at the repo layer
VALID_TRANSITIONS: dict[PipelineStatus, set[PipelineStatus]] = {
    PipelineStatus.DISCOVERED: {PipelineStatus.ENRICHED, PipelineStatus.ENRICHMENT_FAILED},
    PipelineStatus.ENRICHED: {PipelineStatus.SCORED, PipelineStatus.EXCLUDED},
    PipelineStatus.ENRICHMENT_FAILED: {PipelineStatus.DISCOVERED},  # retry
    PipelineStatus.SCORED: {PipelineStatus.TAILORED, PipelineStatus.SKIPPED},
    PipelineStatus.EXCLUDED: set(),  # terminal
    PipelineStatus.TAILORED: {PipelineStatus.COVER_DONE, PipelineStatus.READY_TO_APPLY},
    PipelineStatus.SKIPPED: set(),  # terminal
    PipelineStatus.COVER_DONE: {PipelineStatus.READY_TO_APPLY},
    PipelineStatus.READY_TO_APPLY: {PipelineStatus.IN_PROGRESS},
    PipelineStatus.IN_PROGRESS: {PipelineStatus.APPLIED, PipelineStatus.FAILED, PipelineStatus.NEEDS_HUMAN},
    PipelineStatus.APPLIED: set(),  # terminal
    PipelineStatus.FAILED: {PipelineStatus.READY_TO_APPLY, PipelineStatus.IN_PROGRESS},  # retry
    PipelineStatus.NEEDS_HUMAN: {PipelineStatus.IN_PROGRESS, PipelineStatus.READY_TO_APPLY},
}
