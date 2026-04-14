"""Tests for resume decomposer and reassembler (piece-based architecture)."""

import sqlite3
import pytest

from applypilot.db.dto import PieceDTO, TrackMappingDTO
from applypilot.db.schema import init_db
from applypilot.db.sqlite.piece_repo import SqlitePieceRepository
from applypilot.db.sqlite.overlay_repo import SqliteOverlayRepository
from applypilot.db.sqlite.track_repo import SqliteTrackRepository
from applypilot.tailoring.pieces.decomposer import decompose_to_pieces
from applypilot.tailoring.pieces.reassembler import assemble_from_pieces


SAMPLE_RESUME = {
    "basics": {
        "name": "Test User",
        "label": "Software Engineer",
        "email": "test@example.com",
        "phone": "+1 555 0100",
        "summary": "Engineer with 3 years experience.",
    },
    "work": [
        {
            "name": "BigCorp",
            "position": "SDE",
            "startDate": "2023-01",
            "endDate": "",
            "highlights": ["Built API serving 1M requests/day", "Reduced latency by 40%"],
        },
        {
            "name": "StartupCo",
            "position": "Developer",
            "startDate": "2021-06",
            "endDate": "2022-12",
            "highlights": ["Shipped mobile app to 50K users"],
        },
    ],
    "skills": [
        {"name": "Languages", "keywords": ["Python", "Java"]},
        {"name": "Cloud", "keywords": ["AWS", "Docker"]},
    ],
    "education": [
        {"institution": "MIT", "studyType": "B.S.", "area": "CS", "startDate": "2017", "endDate": "2021"},
    ],
    "projects": [
        {"name": "Side Project", "description": "A cool thing", "keywords": ["Python"]},
    ],
}


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


@pytest.fixture
def piece_repo(conn):
    return SqlitePieceRepository(conn)


class TestDecompose:
    def test_returns_pieces(self, piece_repo):
        pieces = decompose_to_pieces(SAMPLE_RESUME, piece_repo)
        assert len(pieces) > 0

    def test_has_header(self, piece_repo):
        pieces = decompose_to_pieces(SAMPLE_RESUME, piece_repo)
        headers = [p for p in pieces if p.piece_type == "header"]
        assert len(headers) == 1
        assert "Test User" in headers[0].content

    def test_experience_count(self, piece_repo):
        pieces = decompose_to_pieces(SAMPLE_RESUME, piece_repo)
        exps = [p for p in pieces if p.piece_type == "experience_entry"]
        assert len(exps) == 2

    def test_bullet_count(self, piece_repo):
        pieces = decompose_to_pieces(SAMPLE_RESUME, piece_repo)
        bullets = [p for p in pieces if p.piece_type == "bullet"]
        assert len(bullets) == 3

    def test_bullets_have_parent(self, piece_repo):
        pieces = decompose_to_pieces(SAMPLE_RESUME, piece_repo)
        exp_ids = {p.id for p in pieces if p.piece_type == "experience_entry"}
        for b in pieces:
            if b.piece_type == "bullet":
                assert b.parent_piece_id in exp_ids

    def test_skill_groups(self, piece_repo):
        pieces = decompose_to_pieces(SAMPLE_RESUME, piece_repo)
        skills = [p for p in pieces if p.piece_type == "skill_group"]
        assert len(skills) == 2

    def test_education(self, piece_repo):
        pieces = decompose_to_pieces(SAMPLE_RESUME, piece_repo)
        edu = [p for p in pieces if p.piece_type == "education"]
        assert len(edu) == 1
        assert "MIT" in edu[0].content

    def test_project(self, piece_repo):
        pieces = decompose_to_pieces(SAMPLE_RESUME, piece_repo)
        proj = [p for p in pieces if p.piece_type == "project"]
        assert len(proj) == 1

    def test_empty_resume(self, piece_repo):
        pieces = decompose_to_pieces({"basics": {}}, piece_repo)
        assert len(pieces) == 1  # just header

    def test_no_projects(self, piece_repo):
        r = {**SAMPLE_RESUME, "projects": []}
        pieces = decompose_to_pieces(r, piece_repo)
        assert not any(p.piece_type == "project" for p in pieces)

    def test_dedup_on_second_call(self, piece_repo):
        pieces1 = decompose_to_pieces(SAMPLE_RESUME, piece_repo)
        pieces2 = decompose_to_pieces(SAMPLE_RESUME, piece_repo)
        assert all(p1.content_hash == p2.content_hash for p1, p2 in zip(pieces1, pieces2))


class TestAssemble:
    def test_round_trip(self, piece_repo):
        pieces = decompose_to_pieces(SAMPLE_RESUME, piece_repo)
        text = assemble_from_pieces(pieces)
        assert "Test User" in text
        assert "SUMMARY" in text
        assert "EXPERIENCE" in text
        assert "Built API serving 1M requests/day" in text

    def test_contains_all_sections(self, piece_repo):
        pieces = decompose_to_pieces(SAMPLE_RESUME, piece_repo)
        text = assemble_from_pieces(pieces)
        assert "TECHNICAL SKILLS" in text
        assert "EDUCATION" in text
        assert "MIT" in text

    def test_empty_pieces(self):
        text = assemble_from_pieces([])
        assert text.strip() == ""
