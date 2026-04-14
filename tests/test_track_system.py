"""Tests for P2 track system — discovery, graph singleton, track-aware tailoring."""

import pytest
from applypilot.services.track_service import discover_tracks, _infer_family


class TestInferFamily:
    def test_backend(self):
        assert _infer_family("backend engineer") == "backend_engineering"

    def test_mobile(self):
        assert _infer_family("android developer") == "mobile"

    def test_devops(self):
        assert _infer_family("devops engineer") == "devops_sre"

    def test_serverless(self):
        assert _infer_family("serverless architect") == "serverless_cloud"
        assert _infer_family("lambda engineer") == "serverless_cloud"

    def test_data(self):
        assert _infer_family("data scientist") == "data_ml"

    def test_default(self):
        assert _infer_family("software engineer") == "software_engineering"


class TestDiscoverTracks:
    def test_discovers_from_work(self):
        profile = {
            "work": [
                {"position": "Android Developer", "technologies": ["Kotlin", "Jetpack"]},
                {"position": "Backend Engineer", "technologies": ["Python", "Flask"]},
            ],
            "skills": [],
        }
        tracks = discover_tracks(profile)
        names = {t.name for t in tracks}
        assert "Mobile" in names
        assert "Backend Engineering" in names

    def test_single_track_fallback(self):
        profile = {"work": [], "skills": [{"name": "Languages", "keywords": ["Python", "Go"]}]}
        tracks = discover_tracks(profile)
        assert len(tracks) == 1

    def test_serverless_track(self):
        profile = {
            "work": [
                {"position": "Serverless Engineer", "technologies": ["Lambda", "Step Functions", "CDK"]},
            ],
            "skills": [],
        }
        tracks = discover_tracks(profile)
        assert any("Serverless" in t.name for t in tracks)


class TestGraphSingleton:
    def test_container_returns_same_graph(self):
        import sqlite3
        from applypilot.db.container import Container

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        c = Container(conn, auto_init=True)
        g1 = c.skill_graph
        g2 = c.skill_graph
        assert g1 is g2  # same object — singleton

    def test_graph_has_edges(self):
        import sqlite3
        from applypilot.db.container import Container

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        c = Container(conn, auto_init=True)
        assert len(c.skill_graph._edges) > 0


class TestTrackDiscoveryMerge:
    def test_merge_adds_new_tracks(self):
        from applypilot.services.track_discovery import DiscoveredTrack, merge_user_tracks

        existing = [DiscoveredTrack(track_id="a", name="Android", skills=["kotlin"])]
        result = merge_user_tracks(existing, ["Backend", "DevOps"])
        assert len(result) == 3
        names = {t.name for t in result}
        assert "Backend" in names
        assert "DevOps" in names

    def test_merge_skips_duplicates(self):
        from applypilot.services.track_discovery import DiscoveredTrack, merge_user_tracks

        existing = [DiscoveredTrack(track_id="a", name="Android", skills=["kotlin"])]
        result = merge_user_tracks(existing, ["android", "Android"])
        assert len(result) == 1  # no duplicates

    def test_user_tracks_marked_unknown(self):
        from applypilot.services.track_discovery import DiscoveredTrack, merge_user_tracks

        result = merge_user_tracks([], ["Serverless"])
        assert result[0].data_strength == "unknown"
        assert result[0].source == "user"

    def test_heuristic_fallback(self):
        from applypilot.services.track_discovery import _discover_tracks_heuristic

        profile = {
            "skills": [
                {"name": "Mobile", "keywords": ["Kotlin", "Android"]},
                {"name": "DevOps", "keywords": ["Docker", "K8s"]},
            ],
            "work": [{"highlights": ["Built Python Flask API"]}],
        }
        tracks = _discover_tracks_heuristic(profile)
        names = {t.name for t in tracks}
        assert "Mobile Engineering" in names or len(tracks) >= 1


class TestTrackResumeGeneration:
    def test_generates_files(self, tmp_path, monkeypatch):
        from applypilot.services.track_resumes import generate_track_base_resumes
        from applypilot.services.track_discovery import DiscoveredTrack

        monkeypatch.setattr("applypilot.services.track_resumes.APP_DIR", tmp_path)

        resume = {
            "basics": {"name": "Test", "label": "SDE"},
            "work": [{"name": "Co", "position": "Dev", "highlights": ["Built Kotlin app", "Deployed Docker"]}],
            "skills": [{"name": "Mobile", "keywords": ["Kotlin"]}],
        }
        tracks = [
            DiscoveredTrack(track_id="t1", name="Mobile", skills=["kotlin"], active=True),
            DiscoveredTrack(track_id="t2", name="Inactive", skills=[], active=False),
        ]
        paths = generate_track_base_resumes(resume, tracks)
        assert len(paths) == 1
        assert paths[0].exists()
        assert "Kotlin" in paths[0].read_text()
        # JSON version also created
        assert paths[0].with_suffix(".json").exists()


class TestTrackResumeResolution:
    def test_uses_track_resume_when_available(self):
        from applypilot.scoring.tailor.orchestrator import _resolve_resume_for_job

        job = {"best_track_id": "abc123", "url": "http://example.com"}
        tracks = {"abc123": "Track-specific resume text"}
        result = _resolve_resume_for_job(job, "Generic resume", tracks)
        assert result == "Track-specific resume text"

    def test_falls_back_to_generic(self):
        from applypilot.scoring.tailor.orchestrator import _resolve_resume_for_job

        job = {"best_track_id": None, "url": "http://example.com"}
        result = _resolve_resume_for_job(job, "Generic resume", {})
        assert result == "Generic resume"

    def test_unknown_track_falls_back(self):
        from applypilot.scoring.tailor.orchestrator import _resolve_resume_for_job

        job = {"best_track_id": "unknown", "url": "http://example.com"}
        result = _resolve_resume_for_job(job, "Generic resume", {"other": "Other text"})
        assert result == "Generic resume"
