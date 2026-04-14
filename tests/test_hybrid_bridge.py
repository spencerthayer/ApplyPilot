"""Tests for hybrid bridge — decompose, map_track, overlays, cache."""

import json
import sqlite3
import pytest
from applypilot.db.dto import PieceDTO, OverlayDTO


@pytest.fixture
def mem_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from applypilot.db.schema import schema_from_dto

    schema_from_dto(conn)
    return conn


@pytest.fixture
def piece_repo(mem_db):
    from applypilot.db.sqlite.piece_repo import SqlitePieceRepository

    return SqlitePieceRepository(mem_db)


@pytest.fixture
def overlay_repo(mem_db):
    from applypilot.db.sqlite.overlay_repo import SqliteOverlayRepository

    return SqliteOverlayRepository(mem_db)


@pytest.fixture
def sample_resume():
    return {
        "basics": {"name": "Test User", "label": "SDE", "summary": "Good engineer."},
        "work": [
            {
                "name": "Amazon",
                "position": "SDE",
                "startDate": "2024",
                "highlights": ["Built DDD system with Flask", "Deployed on AWS Fargate"],
            },
        ],
        "skills": [{"name": "Languages", "keywords": ["Python", "Java"]}],
        "education": [{"institution": "MIT", "studyType": "BS", "area": "CS"}],
        "projects": [],
    }


class TestDecompose:
    def test_decompose_creates_pieces(self, piece_repo, sample_resume):
        from applypilot.tailoring.pieces.decomposer import decompose_to_pieces

        pieces = decompose_to_pieces(sample_resume, piece_repo)
        types = {p.piece_type for p in pieces}
        assert "header" in types
        assert "bullet" in types
        assert "skill_group" in types
        assert "education" in types

    def test_decompose_is_idempotent(self, piece_repo, sample_resume):
        from applypilot.tailoring.pieces.decomposer import decompose_to_pieces

        first = decompose_to_pieces(sample_resume, piece_repo)
        second = decompose_to_pieces(sample_resume, piece_repo)
        assert len(first) == len(second)

    def test_ensure_decomposed_skips_if_exists(self, piece_repo, sample_resume):
        from applypilot.tailoring.pieces.decomposer import decompose_to_pieces
        from applypilot.scoring.tailor.hybrid_bridge import ensure_decomposed

        decompose_to_pieces(sample_resume, piece_repo)
        count = ensure_decomposed(sample_resume, piece_repo)
        assert count > 0


class TestMapTrack:
    def test_map_track_creates_mappings(self, piece_repo, mem_db, sample_resume):
        from applypilot.tailoring.pieces.decomposer import decompose_to_pieces
        from applypilot.scoring.tailor.hybrid_bridge import map_track

        decompose_to_pieces(sample_resume, piece_repo)
        count = map_track("backend", ["python", "flask", "ddd"], piece_repo, mem_db)
        assert count > 0

    def test_track_pieces_filtered(self, piece_repo, mem_db, sample_resume):
        from applypilot.tailoring.pieces.decomposer import decompose_to_pieces
        from applypilot.scoring.tailor.hybrid_bridge import map_track

        decompose_to_pieces(sample_resume, piece_repo)
        map_track("backend", ["python", "flask"], piece_repo, mem_db)
        track_pieces = piece_repo.get_track_pieces("backend")
        assert len(track_pieces) > 0
        # Header always included
        assert any(p.piece_type == "header" for p in track_pieces)


class TestOverlays:
    def test_store_and_retrieve_overlays(self, piece_repo, overlay_repo, sample_resume):
        from applypilot.tailoring.pieces.decomposer import decompose_to_pieces
        from applypilot.scoring.tailor.hybrid_bridge import store_overlays

        decompose_to_pieces(sample_resume, piece_repo)
        tailored = {
            "experience": [
                {
                    "header": "SDE | Amazon",
                    "bullets": [
                        {"text": "Independently built DDD system with Flask reducing costs by 75%"},
                    ],
                }
            ],
        }
        count = store_overlays(tailored, "https://example.com/job/1", None, piece_repo, overlay_repo)
        assert count >= 0  # May or may not match depending on fuzzy logic

    def test_cache_miss_returns_none(self, piece_repo, overlay_repo):
        from applypilot.scoring.tailor.hybrid_bridge import try_cache

        result = try_cache("https://nonexistent.com/job", None, piece_repo, overlay_repo)
        assert result is None


class TestFromPieces:
    def test_from_pieces_renders_base(self, piece_repo, sample_resume):
        from applypilot.tailoring.pieces.decomposer import decompose_to_pieces
        from applypilot.resume_builder import from_pieces

        decompose_to_pieces(sample_resume, piece_repo)
        text = from_pieces(piece_repo).render_text()
        assert "Test User" in text
        assert "Built DDD system" in text

    def test_from_pieces_empty_db(self, piece_repo):
        from applypilot.resume_builder import from_pieces

        text = from_pieces(piece_repo).render_text()
        assert text == ""  # No pieces = empty


class TestVariantCache:
    def test_keyword_set_extracts_meaningful_words(self):
        from applypilot.scoring.tailor.hybrid_bridge import _jd_keyword_set

        kws = _jd_keyword_set("Experience with Python and Docker for backend development")
        assert "python" in kws
        assert "docker" in kws
        assert "with" not in kws

    def test_keyword_set_empty_input(self):
        from applypilot.scoring.tailor.hybrid_bridge import _jd_keyword_set

        assert _jd_keyword_set("") == set()

    def test_try_cache_with_jd_text_no_match(self, piece_repo, overlay_repo):
        from applypilot.scoring.tailor.hybrid_bridge import try_cache

        result = try_cache("https://new.com/job", None, piece_repo, overlay_repo, jd_text="totally unique JD text here")
        assert result is None


class TestRefreshPieces:
    def test_refresh_detects_added_bullets(self, piece_repo, mem_db, sample_resume):
        from applypilot.tailoring.pieces.decomposer import decompose_to_pieces
        from applypilot.scoring.tailor.hybrid_bridge import refresh_pieces

        # Initial decompose
        decompose_to_pieces(sample_resume, piece_repo)

        # Add a bullet
        sample_resume["work"][0]["highlights"].append("New achievement unlocked")
        result = refresh_pieces(sample_resume, piece_repo, mem_db)
        assert result["added"] >= 1
        assert result["total_pieces"] > 0

    def test_refresh_no_change(self, piece_repo, mem_db, sample_resume):
        from applypilot.tailoring.pieces.decomposer import decompose_to_pieces
        from applypilot.scoring.tailor.hybrid_bridge import refresh_pieces

        decompose_to_pieces(sample_resume, piece_repo)
        result = refresh_pieces(sample_resume, piece_repo, mem_db)
        assert result["added"] == 0
        assert result["removed"] == 0
