"""Data access package.

Architecture: DTOs → Repository interfaces → SQLite implementations → DI Container.
"""

from applypilot.db.container import Container
from applypilot.db.dto import (
    ALL_DTOS,
    AnalyticsEventDTO,
    ApplyResultDTO,
    CoverLetterPieceDTO,
    CoverLetterResultDTO,
    ExclusionResultDTO,
    JobDTO,
    LLMCacheEntryDTO,
    OverlayDTO,
    PieceDTO,
    RedirectChainDTO,
    ScoreFailureDTO,
    ScoreResultDTO,
    SchemaVersionDTO,
    TailorResultDTO,
    TrackMappingDTO,
)
from applypilot.db.schema import init_db, migrate_from_dto, schema_from_dto

# Connection re-exports (used by legacy code outside db/)
from applypilot.db.connection import close_connection, get_connection

__all__ = [
    "Container",
    "JobDTO",
    "PieceDTO",
    "OverlayDTO",
    "TrackMappingDTO",
    "CoverLetterPieceDTO",
    "CoverLetterResultDTO",
    "ExclusionResultDTO",
    "ScoreFailureDTO",
    "AnalyticsEventDTO",
    "RedirectChainDTO",
    "LLMCacheEntryDTO",
    "SchemaVersionDTO",
    "ScoreResultDTO",
    "TailorResultDTO",
    "ApplyResultDTO",
    "ALL_DTOS",
    "init_db",
    "schema_from_dto",
    "migrate_from_dto",
    "get_connection",
    "close_connection",
]
