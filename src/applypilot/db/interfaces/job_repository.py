"""JobRepository ABC — contract for job persistence."""

from abc import ABC, abstractmethod

from applypilot.db.dto import (
    ApplyResultDTO,
    CoverLetterResultDTO,
    EnrichErrorDTO,
    EnrichResultDTO,
    ExclusionResultDTO,
    JobDTO,
    ScoreFailureDTO,
    ScoreResultDTO,
    TailorResultDTO,
)


class JobRepository(ABC):
    @abstractmethod
    def upsert(self, job: JobDTO) -> None: ...

    @abstractmethod
    def get_by_url(self, url: str) -> JobDTO | None: ...

    @abstractmethod
    def get_by_stage(self, stage: str, limit: int = 0) -> list[JobDTO]: ...

    @abstractmethod
    def update_enrichment(self, result: EnrichResultDTO) -> None: ...

    @abstractmethod
    def update_enrichment_error(self, result: EnrichErrorDTO) -> None: ...

    @abstractmethod
    def update_score(self, result: ScoreResultDTO) -> None: ...

    @abstractmethod
    def update_exclusion(self, result: ExclusionResultDTO) -> None: ...

    @abstractmethod
    def update_score_failure(self, result: ScoreFailureDTO) -> None: ...

    @abstractmethod
    def update_tailoring(self, result: TailorResultDTO) -> None: ...

    @abstractmethod
    def update_cover_letter(self, result: CoverLetterResultDTO) -> None: ...

    @abstractmethod
    def update_apply_status(self, result: ApplyResultDTO) -> None: ...

    @abstractmethod
    def acquire_next(self, min_score: int, max_attempts: int, agent_id: str) -> JobDTO | None: ...

    @abstractmethod
    def get_priority_queue(self, limit: int = 50) -> list[JobDTO]: ...

    @abstractmethod
    def count_by_status(self) -> dict[str, int]: ...

    @abstractmethod
    def reset_stale_in_progress(self, timeout_minutes: int = 5) -> int: ...

    @abstractmethod
    def get_score_distribution(self) -> list[tuple[int, int]]: ...

    @abstractmethod
    def autoheal_legacy_llm_failures(self, error_pattern: str) -> int: ...

    @abstractmethod
    def commit(self) -> None: ...

    @abstractmethod
    def find_by_url_fuzzy(self, url: str) -> JobDTO | None: ...

    @abstractmethod
    def get_all_urls_and_sites(self) -> list[tuple[str, str]]: ...

    @abstractmethod
    def get_relative_application_urls(self) -> list[tuple[str, str, str]]: ...

    @abstractmethod
    def get_wttj_jobs(self) -> list[tuple[str, str]]: ...

    @abstractmethod
    def get_pending_enrichment(self, skip_sites: list[str], job_url: str | None = None) -> list[JobDTO]: ...

    @abstractmethod
    def park_for_human_review(self, url: str, reason: str, apply_url: str, instructions: str) -> None: ...

    @abstractmethod
    def mark_permanent_failure(self, url: str) -> None: ...

    @abstractmethod
    def acquire_next_filtered(
            self,
            min_score: int,
            max_attempts: int,
            agent_id: str,
            blocked_sites: list[str] | None = None,
            blocked_patterns: list[str] | None = None,
    ) -> JobDTO | None: ...

    @abstractmethod
    def get_target_job(self, url: str) -> JobDTO | None: ...

    @abstractmethod
    def lock_for_apply(self, url: str, agent_id: str) -> None: ...

    @abstractmethod
    def get_by_rowid(self, rowid: int) -> JobDTO | None: ...

    @abstractmethod
    def get_dashboard_data(self) -> dict: ...

    @abstractmethod
    def get_by_pipeline_status(self, status: str, limit: int = 0) -> list[JobDTO]: ...

    @abstractmethod
    def set_pipeline_status(self, url: str, status: str) -> None: ...

    @abstractmethod
    def backfill_pipeline_status(self) -> int: ...

    @abstractmethod
    def get_jobs_by_stage_dict(
            self, stage: str, *, min_score: int | None = None, limit: int = 100, job_url: str | None = None
    ) -> list[JobDTO]: ...

    @abstractmethod
    def find_by_tailored_path(self, path: str) -> list[JobDTO]: ...

    @abstractmethod
    def clear_tailoring(self, url: str) -> None: ...

    @abstractmethod
    def search_fts(self, query: str, limit: int = 50) -> list[JobDTO]: ...

    @abstractmethod
    def update_job_fields_generic(self, url: str, fields: dict) -> None: ...
