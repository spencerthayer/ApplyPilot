"""Frozen DTO dataclasses — single source of truth for all DB table schemas.

Each persistent DTO has __table_name__ and __table_config__ class vars
that drive automatic schema creation via schema_from_dto().

Transient DTOs (ScoreResultDTO, TailorResultDTO, ApplyResultDTO) are used
for passing structured results between services and repos — they have no
table backing.
"""

from __future__ import annotations

from dataclasses import dataclass


# ── Persistent DTOs (backed by tables) ──────────────────────────────────


@dataclass(frozen=True)
class JobDTO:
    __table_name__ = "jobs"
    __table_config__ = {
        "primary_key": "url",
        "indexes": ["fit_score", "apply_status", "site", "pipeline_status"],
    }

    url: str
    title: str | None = None
    company: str | None = None
    location: str | None = None
    site: str | None = None
    salary: str | None = None
    description: str | None = None
    strategy: str | None = None
    discovered_at: str | None = None
    pipeline_status: str | None = None  # PipelineStatus value — explicit state machine
    full_description: str | None = None
    application_url: str | None = None
    detail_scraped_at: str | None = None
    detail_error: str | None = None
    detail_error_category: str | None = None
    detail_retry_count: int = 0
    detail_next_retry_at: str | None = None
    fit_score: int | None = None
    score_reasoning: str | None = None
    scored_at: str | None = None
    score_error: str | None = None
    score_retry_count: int = 0
    score_next_retry_at: str | None = None
    exclusion_reason_code: str | None = None
    exclusion_rule_id: str | None = None
    excluded_at: str | None = None
    tailored_resume_path: str | None = None
    tailored_at: str | None = None
    tailor_attempts: int = 0
    cover_letter_path: str | None = None
    cover_letter_at: str | None = None
    cover_attempts: int = 0
    applied_at: str | None = None
    apply_status: str | None = None
    apply_error: str | None = None
    apply_attempts: int = 0
    agent_id: str | None = None
    last_attempted_at: str | None = None
    apply_duration_ms: int | None = None
    apply_task_id: str | None = None
    verification_confidence: str | None = None
    apply_tier: str | None = None
    tier_weight: float | None = None
    redirect_chain: str | None = None
    classified_at: str | None = None
    apply_category: str | None = None
    tracking_status: str | None = None
    tracking_updated_at: str | None = None
    tracking_doc_path: str | None = None
    last_email_at: str | None = None
    next_action: str | None = None
    next_action_due: str | None = None
    needs_human_reason: str | None = None
    needs_human_url: str | None = None
    needs_human_instructions: str | None = None
    best_track_id: str | None = None
    tailoring_pipeline: str | None = None  # "two_stage" | "cache_hit" | "single_stage_fallback"
    overlay_count: int = 0  # number of overlays stored for this job


@dataclass(frozen=True)
class PieceDTO:
    __table_name__ = "pieces"
    __table_config__ = {
        "primary_key": "id",
        "indexes": ["piece_type", "content_hash", "parent_piece_id"],
    }

    id: str
    content_hash: str
    piece_type: str
    content: str
    parent_piece_id: str | None = None
    tags: str = "[]"
    metadata: str = "{}"
    sort_order: int = 0
    created_at: str | None = None


@dataclass(frozen=True)
class OverlayDTO:
    __table_name__ = "overlays"
    __table_config__ = {
        "primary_key": "id",
        "indexes": ["piece_id", "job_url"],
        "unique": [("piece_id", "job_url", "track_id")],
    }

    id: str
    piece_id: str
    job_url: str
    track_id: str | None = None
    overlay_type: str = ""
    content_delta: str = ""
    metadata_delta: str = "{}"
    created_at: str | None = None


@dataclass(frozen=True)
class TrackMappingDTO:
    __table_name__ = "track_piece_mappings"
    __table_config__ = {
        "primary_key": ("track_id", "piece_id"),
        "indexes": ["track_id"],
    }

    track_id: str
    piece_id: str
    emphasis: float = 1.0
    include: int = 1
    sort_override: int | None = None


@dataclass(frozen=True)
class TrackDTO:
    __table_name__ = "tracks"
    __table_config__ = {"primary_key": "track_id"}

    track_id: str
    name: str = ""
    skills: str = "[]"
    active: int = 1
    base_resume_path: str | None = None
    framing: str | None = None  # "what employers buy" — adaptive proof point prioritization
    created_at: str | None = None


@dataclass(frozen=True)
class CoverLetterPieceDTO:
    __table_name__ = "cover_letter_pieces"
    __table_config__ = {
        "primary_key": "id",
        "indexes": ["piece_type", "job_url"],
    }

    id: str
    piece_type: str
    content: str
    skill_tags: str = "[]"
    job_url: str | None = None
    created_at: str | None = None


@dataclass(frozen=True)
class AnalyticsEventDTO:
    __table_name__ = "analytics_events"
    __table_config__ = {
        "primary_key": "event_id",
        "indexes": ["event_type", ("stage", "timestamp")],
    }

    event_id: str
    timestamp: str
    stage: str
    event_type: str
    payload: str = "{}"
    processed_at: str | None = None


@dataclass(frozen=True)
class RedirectChainDTO:
    __table_name__ = "redirect_chains"
    __table_config__ = {"primary_key": "job_id"}

    job_id: str
    original_url: str
    final_url: str
    hop_count: int = 0
    total_time_ms: int = 0
    chain_log: str = "[]"
    final_tier: str | None = None
    classified_at: str | None = None


@dataclass(frozen=True)
class LLMCacheEntryDTO:
    __table_name__ = "llm_cache"
    __table_config__ = {"primary_key": "cache_key"}

    cache_key: str
    response: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    created_at: str | None = None
    ttl_seconds: int = 86400
    hit_count: int = 0


@dataclass(frozen=True)
class SchemaVersionDTO:
    __table_name__ = "schema_version"
    __table_config__ = {"primary_key": "version"}

    version: int


@dataclass(frozen=True)
class AccountDTO:
    __table_name__ = "accounts"
    __table_config__ = {"primary_key": "id", "indexes": ["domain"]}

    id: int | None = None  # AUTOINCREMENT
    site: str = ""
    domain: str = ""
    email: str = ""
    password: str | None = None
    created_at: str | None = None
    job_url: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class TrackingEmailDTO:
    __table_name__ = "tracking_emails"
    __table_config__ = {"primary_key": "email_id", "indexes": ["job_url"]}

    email_id: str = ""
    thread_id: str | None = None
    job_url: str = ""
    sender: str | None = None
    sender_name: str | None = None
    subject: str | None = None
    received_at: str | None = None
    snippet: str | None = None
    body_text: str | None = None
    classification: str | None = None
    extracted_data: str | None = None
    classified_at: str | None = None


@dataclass(frozen=True)
class TrackingPersonDTO:
    __table_name__ = "tracking_people"
    __table_config__ = {
        "primary_key": "id",
        "indexes": ["job_url"],
        "unique": [("job_url", "email")],
    }

    id: int | None = None  # AUTOINCREMENT
    job_url: str = ""
    name: str | None = None
    title: str | None = None
    email: str | None = None
    source_email_id: str | None = None
    first_seen_at: str | None = None


@dataclass(frozen=True)
class QAKnowledgeDTO:
    __table_name__ = "qa_knowledge"
    __table_config__ = {
        "primary_key": "id",
        "indexes": ["question_key"],
        "unique": [("question_key", "answer_text")],
    }

    id: int | None = None  # AUTOINCREMENT
    question_text: str = ""
    question_key: str = ""
    answer_text: str = ""
    answer_source: str = ""
    field_type: str | None = None
    options_json: str | None = None
    ats_slug: str | None = None
    job_url: str | None = None
    outcome: str = "unknown"
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class BulletBankDTO:
    __table_name__ = "bullet_bank"
    __table_config__ = {"primary_key": "id"}

    id: str = ""
    text: str = ""
    context: str = "{}"
    tags: str = "[]"
    metrics: str = "[]"
    created_at: str | None = None
    use_count: int = 0
    success_rate: float = 0.0


@dataclass(frozen=True)
class BulletFeedbackDTO:
    __table_name__ = "bullet_feedback"
    __table_config__ = {"primary_key": "id", "indexes": ["bullet_id"]}

    id: int | None = None
    bullet_id: str = ""
    job_title: str = ""
    outcome: str = ""
    created_at: str | None = None


# ── Transient DTOs (no table backing) ───────────────────────────────────


@dataclass(frozen=True)
class ScoreResultDTO:
    """Passes scoring results to job_repo.update_score()."""

    url: str
    fit_score: int
    score_reasoning: str
    scored_at: str


@dataclass(frozen=True)
class ExclusionResultDTO:
    """Passes exclusion results to job_repo.update_exclusion()."""

    url: str
    exclusion_reason_code: str
    exclusion_rule_id: str
    score_reasoning: str
    scored_at: str


@dataclass(frozen=True)
class ScoreFailureDTO:
    """Passes scoring failure to job_repo.update_score_failure()."""

    url: str
    score_error: str
    score_reasoning: str
    score_retry_count: int
    score_next_retry_at: str | None = None


@dataclass(frozen=True)
class TailorResultDTO:
    """Passes tailoring results to job_repo.update_tailoring()."""

    url: str
    tailored_resume_path: str
    tailored_at: str


@dataclass(frozen=True)
class ApplyResultDTO:
    """Passes apply results to job_repo.update_apply_status()."""

    url: str
    apply_status: str
    apply_error: str | None = None
    applied_at: str | None = None
    apply_duration_ms: int | None = None
    agent_id: str | None = None


@dataclass(frozen=True)
class CoverLetterResultDTO:
    """Passes cover letter results to job_repo.update_cover_letter()."""

    url: str
    cover_letter_path: str
    cover_letter_at: str


@dataclass(frozen=True)
class EnrichResultDTO:
    """Passes enrichment results to job_repo.update_enrichment()."""

    url: str
    full_description: str | None
    application_url: str | None
    detail_scraped_at: str


@dataclass(frozen=True)
class EnrichErrorDTO:
    """Passes enrichment errors to job_repo.update_enrichment_error()."""

    url: str
    detail_error: str
    detail_error_category: str
    detail_retry_count: int
    detail_next_retry_at: str | None
    detail_scraped_at: str


# ── Registry ────────────────────────────────────────────────────────────

ALL_DTOS = [
    JobDTO,
    PieceDTO,
    OverlayDTO,
    TrackMappingDTO,
    TrackDTO,
    CoverLetterPieceDTO,
    AnalyticsEventDTO,
    RedirectChainDTO,
    LLMCacheEntryDTO,
    SchemaVersionDTO,
    AccountDTO,
    TrackingEmailDTO,
    TrackingPersonDTO,
    QAKnowledgeDTO,
    BulletBankDTO,
    BulletFeedbackDTO,
]
