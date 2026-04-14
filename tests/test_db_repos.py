"""Tests for db/ DAO layer — PieceRepository, OverlayRepository, TrackRepository."""

import sqlite3
import pytest

from applypilot.db.dto import PieceDTO, OverlayDTO, TrackMappingDTO
from applypilot.db.schema import init_db
from applypilot.db.sqlite.piece_repo import SqlitePieceRepository
from applypilot.db.sqlite.overlay_repo import SqliteOverlayRepository
from applypilot.db.sqlite.track_repo import SqliteTrackRepository


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


@pytest.fixture
def overlay_repo(conn):
    return SqliteOverlayRepository(conn)


@pytest.fixture
def track_repo(conn):
    return SqliteTrackRepository(conn)


# ── PieceRepository ──────────────────────────────────────────────────


class TestPieceRepository:
    def test_save_and_get(self, piece_repo):
        p = PieceDTO(id="p1", content_hash="h1", piece_type="header", content="Jane Doe")
        piece_repo.save(p)
        got = piece_repo.get_by_id("p1")
        assert got is not None
        assert got.content == "Jane Doe"
        assert got.piece_type == "header"

    def test_save_many(self, piece_repo):
        pieces = [
            PieceDTO(id="p1", content_hash="h1", piece_type="bullet", content="A"),
            PieceDTO(id="p2", content_hash="h2", piece_type="bullet", content="B"),
        ]
        piece_repo.save_many(pieces)
        assert piece_repo.get_by_id("p1") is not None
        assert piece_repo.get_by_id("p2") is not None

    def test_get_by_hash(self, piece_repo):
        piece_repo.save(PieceDTO(id="p1", content_hash="abc123", piece_type="bullet", content="X"))
        got = piece_repo.get_by_hash("abc123")
        assert got is not None
        assert got.id == "p1"

    def test_get_by_type(self, piece_repo):
        piece_repo.save_many(
            [
                PieceDTO(id="p1", content_hash="h1", piece_type="bullet", content="A"),
                PieceDTO(id="p2", content_hash="h2", piece_type="bullet", content="B"),
                PieceDTO(id="p3", content_hash="h3", piece_type="header", content="C"),
            ]
        )
        bullets = piece_repo.get_by_type("bullet")
        assert len(bullets) == 2

    def test_get_children(self, piece_repo):
        piece_repo.save_many(
            [
                PieceDTO(id="exp", content_hash="h0", piece_type="experience_entry", content="Job"),
                PieceDTO(id="b1", content_hash="h1", piece_type="bullet", content="A", parent_piece_id="exp"),
                PieceDTO(id="b2", content_hash="h2", piece_type="bullet", content="B", parent_piece_id="exp"),
            ]
        )
        children = piece_repo.get_children("exp")
        assert len(children) == 2

    def test_get_track_pieces(self, piece_repo, track_repo):
        piece_repo.save_many(
            [
                PieceDTO(id="p1", content_hash="h1", piece_type="header", content="A", sort_order=0),
                PieceDTO(id="p2", content_hash="h2", piece_type="bullet", content="B", sort_order=1),
            ]
        )
        track_repo.save_mapping(TrackMappingDTO(track_id="t1", piece_id="p1", include=1))
        track_repo.save_mapping(TrackMappingDTO(track_id="t1", piece_id="p2", include=1))
        pieces = piece_repo.get_track_pieces("t1")
        assert len(pieces) == 2

    def test_dedup_by_hash(self, piece_repo):
        piece_repo.save(PieceDTO(id="p1", content_hash="same", piece_type="bullet", content="A"))
        existing = piece_repo.get_by_hash("same")
        assert existing is not None
        assert existing.id == "p1"


# ── OverlayRepository ────────────────────────────────────────────────


class TestOverlayRepository:
    def test_save_and_get_for_job(self, overlay_repo):
        o = OverlayDTO(id="o1", piece_id="p1", job_url="https://j/1", overlay_type="keyword_inject", content_delta="X")
        overlay_repo.save(o)
        overlays = overlay_repo.get_for_job("https://j/1")
        assert len(overlays) == 1
        assert overlays[0].content_delta == "X"

    def test_get_for_job_with_track(self, overlay_repo):
        overlay_repo.save(OverlayDTO(id="o1", piece_id="p1", job_url="https://j/1", track_id="t1", content_delta="A"))
        overlay_repo.save(OverlayDTO(id="o2", piece_id="p2", job_url="https://j/1", track_id="t2", content_delta="B"))
        assert len(overlay_repo.get_for_job("https://j/1", track_id="t1")) == 1

    def test_get_for_piece(self, overlay_repo):
        overlay_repo.save(OverlayDTO(id="o1", piece_id="p1", job_url="https://j/1", content_delta="A"))
        overlay_repo.save(OverlayDTO(id="o2", piece_id="p1", job_url="https://j/2", content_delta="B"))
        assert len(overlay_repo.get_for_piece("p1")) == 2


# ── TrackRepository ──────────────────────────────────────────────────


class TestTrackRepository:
    def test_save_and_get_mappings(self, track_repo):
        track_repo.save_mapping(TrackMappingDTO(track_id="t1", piece_id="p1", emphasis=1.0))
        track_repo.save_mapping(TrackMappingDTO(track_id="t1", piece_id="p2", emphasis=0.5))
        mappings = track_repo.get_mappings("t1")
        assert len(mappings) == 2

    def test_delete_track(self, track_repo):
        track_repo.save_mapping(TrackMappingDTO(track_id="t1", piece_id="p1"))
        track_repo.save_mapping(TrackMappingDTO(track_id="t1", piece_id="p2"))
        deleted = track_repo.delete_track("t1")
        assert deleted == 2
        assert track_repo.get_mappings("t1") == []

    def test_track_isolation(self, track_repo):
        track_repo.save_mapping(TrackMappingDTO(track_id="t1", piece_id="p1"))
        track_repo.save_mapping(TrackMappingDTO(track_id="t2", piece_id="p2"))
        assert len(track_repo.get_mappings("t1")) == 1
        assert len(track_repo.get_mappings("t2")) == 1
