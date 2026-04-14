"""DI Container — creates concrete repos, injects into services.

Swap SQLite→Postgres: change THIS file only. Zero service changes.
"""

from __future__ import annotations

import sqlite3

from applypilot.db.interfaces.account_repository import AccountRepository
from applypilot.db.interfaces.analytics_repository import AnalyticsRepository
from applypilot.db.interfaces.bullet_bank_repository import BulletBankRepository
from applypilot.db.interfaces.job_repository import JobRepository
from applypilot.db.interfaces.llm_cache_repository import LLMCacheRepository
from applypilot.db.interfaces.overlay_repository import OverlayRepository
from applypilot.db.interfaces.piece_repository import PieceRepository
from applypilot.db.interfaces.qa_repository import QARepository
from applypilot.db.interfaces.track_repository import TrackRepository
from applypilot.db.interfaces.tracking_repository import TrackingRepository
from applypilot.db.schema import init_db
from applypilot.db.sqlite.account_repo import SqliteAccountRepository
from applypilot.db.sqlite.analytics_repo import SqliteAnalyticsRepository
from applypilot.db.sqlite.bullet_bank_repo import SqliteBulletBankRepository
from applypilot.db.sqlite.connection import get_connection
from applypilot.db.sqlite.job_repo import SqliteJobRepository
from applypilot.db.sqlite.llm_cache_repo import SqliteLLMCacheRepository
from applypilot.db.sqlite.overlay_repo import SqliteOverlayRepository
from applypilot.db.sqlite.piece_repo import SqlitePieceRepository
from applypilot.db.sqlite.qa_repo import SqliteQARepository
from applypilot.db.sqlite.track_repo import SqliteTrackRepository
from applypilot.db.sqlite.tracking_repo import SqliteTrackingRepository


class Container:
    """DI container. Creates concrete repos from a single connection."""

    def __init__(self, conn: sqlite3.Connection | None = None, *, auto_init: bool = True):
        self._conn = conn or get_connection()
        self._graph = None  # lazy singleton
        if auto_init:
            init_db(get_connection())

    @property
    def skill_graph(self):
        """Singleton SkillAdjacencyGraph — ESCO + LLM hybrid, cached."""
        if self._graph is None:
            all_skills = []
            try:
                from applypilot.config import RESUME_JSON_PATH
                import json

                if RESUME_JSON_PATH.exists():
                    data = json.loads(RESUME_JSON_PATH.read_text(encoding="utf-8"))
                    for group in data.get("skills", []):
                        all_skills.extend(group.get("keywords", []))
            except Exception:
                pass

            from applypilot.intelligence.adjacency_graph.builder import build_graph

            self._graph = build_graph(all_skills)
        return self._graph

    @property
    def job_repo(self) -> JobRepository:
        return SqliteJobRepository(get_connection())

    @property
    def piece_repo(self) -> PieceRepository:
        return SqlitePieceRepository(get_connection())

    @property
    def overlay_repo(self) -> OverlayRepository:
        return SqliteOverlayRepository(get_connection())

    @property
    def track_repo(self) -> TrackRepository:
        return SqliteTrackRepository(get_connection())

    @property
    def analytics_repo(self) -> AnalyticsRepository:
        return SqliteAnalyticsRepository(get_connection())

    @property
    def llm_cache_repo(self) -> LLMCacheRepository:
        return SqliteLLMCacheRepository(get_connection())

    @property
    def tracking_repo(self) -> TrackingRepository:
        return SqliteTrackingRepository(get_connection())

    @property
    def account_repo(self) -> AccountRepository:
        return SqliteAccountRepository(get_connection())

    @property
    def qa_repo(self) -> QARepository:
        return SqliteQARepository(get_connection())

    @property
    def bullet_bank_repo(self) -> BulletBankRepository:
        return SqliteBulletBankRepository(get_connection())
