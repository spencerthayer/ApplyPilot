"""Storage adapter for ComprehensiveTailoringEngine — replaces direct sqlite3 calls."""

from __future__ import annotations

import json
import sqlite3
from typing import Any


class ComprehensiveStorage:
    """Wraps bullet bank + evidence DB access for the comprehensive engine.

    Accepts either a DI-provided connection or creates one from a path.
    """

    def __init__(self, conn: sqlite3.Connection | None = None, *, db_path: str = ""):
        if conn is not None:
            self._conn = conn
            self._owns_conn = False
        else:
            from applypilot.db.sqlite.connection import get_connection

            self._conn = get_connection(db_path) if db_path else get_connection()
            self._owns_conn = True
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS bullets (
                id TEXT PRIMARY KEY, text TEXT, variants TEXT, tags TEXT,
                skills TEXT, domains TEXT, role_families TEXT, evidence_links TEXT,
                metrics TEXT, vague_claim BOOLEAN, implied_scale BOOLEAN,
                tech_mismatch BOOLEAN, keyword_mismatch BOOLEAN,
                ownership_level INTEGER, recency_score REAL,
                has_proof BOOLEAN, has_metric BOOLEAN,
                use_count INTEGER DEFAULT 0, success_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS bullet_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT, bullet_id TEXT,
                job_title TEXT, outcome TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (bullet_id) REFERENCES bullets(id)
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT, claim TEXT,
                bullet_id TEXT, proof_links TEXT, interview_script TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS metrics_registry (
                id INTEGER PRIMARY KEY AUTOINCREMENT, metric_key TEXT UNIQUE,
                value TEXT, timeframe TEXT, definition TEXT, source TEXT,
                allowed_phrases TEXT, verified BOOLEAN DEFAULT FALSE
            )
        """)
        self._conn.commit()

    def save_bullet(self, bullet: Any) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO bullets "
            "(id, text, variants, tags, skills, domains, role_families, "
            "evidence_links, metrics, vague_claim, implied_scale, has_proof, has_metric) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                bullet.id,
                bullet.text,
                json.dumps(bullet.variants),
                json.dumps(bullet.tags),
                json.dumps(bullet.skills),
                json.dumps(bullet.domains),
                json.dumps(bullet.role_families),
                json.dumps(bullet.evidence_links),
                json.dumps(bullet.metrics),
                bullet.vague_claim,
                bullet.implied_scale,
                bullet.has_proof,
                bullet.has_metric,
            ),
        )
        self._conn.commit()

    def get_metric_bullets(self) -> list[sqlite3.Row]:
        self._conn.row_factory = sqlite3.Row
        return self._conn.execute("SELECT * FROM bullets WHERE has_metric = 1").fetchall()

    def get_all_bullets(self) -> list[sqlite3.Row]:
        self._conn.row_factory = sqlite3.Row
        return self._conn.execute("SELECT * FROM bullets").fetchall()

    def record_feedback(self, bullet_id: str, job_title: str, outcome: str) -> None:
        self._conn.execute(
            "INSERT INTO bullet_feedback (bullet_id, job_title, outcome) VALUES (?,?,?)",
            (bullet_id, job_title, outcome),
        )
        self._conn.commit()

    def save_evidence(self, claim: str, bullet_id: str, proof_links: str, script: str) -> None:
        self._conn.execute(
            "INSERT INTO evidence (claim, bullet_id, proof_links, interview_script) VALUES (?,?,?,?)",
            (claim, bullet_id, proof_links, script),
        )
        self._conn.commit()

    def save_metric(self, key: str, value: str, timeframe: str, definition: str, source: str, phrases: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO metrics_registry "
            "(metric_key, value, timeframe, definition, source, allowed_phrases) "
            "VALUES (?,?,?,?,?,?)",
            (key, value, timeframe, definition, source, phrases),
        )
        self._conn.commit()
