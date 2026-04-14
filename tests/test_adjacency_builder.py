"""Tests for adjacency graph builder, LLM provider, and ESCO provider."""

import pytest
from pathlib import Path
from applypilot.intelligence.adjacency_graph.graph import SkillAdjacencyGraph, AdjacencyEdge


class TestGraphLoadYaml:
    def test_loads_existing_yaml(self, tmp_path):
        g = SkillAdjacencyGraph()
        yaml_file = tmp_path / "adj.yaml"
        yaml_file.write_text("generated:\n  python:\n  - target: flask\n    confidence: 0.9\n    relation: framework\n")
        count = g.load_yaml(yaml_file)
        assert count > 0
        assert len(g._edges) > 0

    def test_missing_yaml_returns_zero(self, tmp_path):
        g = SkillAdjacencyGraph()
        assert g.load_yaml(tmp_path / "nonexistent.yaml") == 0

    def _make_graph(self, tmp_path):
        g = SkillAdjacencyGraph()
        yaml_file = tmp_path / "adj.yaml"
        yaml_file.write_text(
            "generated:\n"
            "  kotlin:\n"
            "  - target: java\n"
            "    confidence: 0.85\n"
            "    relation: interop\n"
            "  python:\n"
            "  - target: flask\n"
            "    confidence: 0.9\n"
            "    relation: framework\n"
        )
        g.load_yaml(yaml_file)
        return g

    def test_resolve_exact_match(self, tmp_path):
        g = self._make_graph(tmp_path)
        g.enrich_with_user_skills(["kotlin"])
        edge = g.resolve("kotlin", {"kotlin"})
        assert edge is not None
        assert edge.confidence == 1.0
        assert edge.relation == "exact"

    def test_resolve_adjacent(self, tmp_path):
        g = self._make_graph(tmp_path)
        edge = g.resolve("kotlin", {"java"})
        assert edge is not None
        assert edge.confidence > 0

    def test_resolve_no_match(self, tmp_path):
        g = self._make_graph(tmp_path)
        assert g.resolve("quantum_teleportation", {"kotlin"}) is None

    def test_enrich_adds_self_edges(self):
        g = SkillAdjacencyGraph()
        added = g.enrich_with_user_skills(["my_custom_skill"])
        assert added == 1
        assert g.get_edges("my_custom_skill")[0].relation == "self"


class TestBuilder:
    def test_uses_cache(self):
        from applypilot.intelligence.adjacency_graph.builder import build_graph

        # Should load from existing cache without calling LLM
        graph = build_graph(["kotlin", "python"], force=False)
        assert len(graph._edges) > 0

    def test_force_regenerate(self, tmp_path, monkeypatch):
        from applypilot.intelligence.adjacency_graph import builder

        cache = tmp_path / "test_adj.yaml"
        # Mock LLM provider
        monkeypatch.setattr(
            "applypilot.intelligence.adjacency_graph.providers.llm_provider.generate_adjacencies",
            lambda skills: {"python": [("django", 0.8, "framework")]},
        )
        graph = builder.build_graph(["python"], cache_path=cache, force=True)
        assert cache.exists()
        edges = [e for e in graph.get_edges("python") if e.relation != "self"]
        assert any(e.target == "django" for e in edges)


class TestLLMProvider:
    def test_generate_with_mock(self, monkeypatch):
        from applypilot.intelligence.adjacency_graph.providers import llm_provider

        fake_response = '{"kotlin": [{"target": "java", "confidence": 0.85, "relation": "jvm"}]}'

        class FakeClient:
            def chat(self, messages, **kw):
                return fake_response

        monkeypatch.setattr("applypilot.llm.get_client", lambda **kw: FakeClient())
        result = llm_provider._generate_batch(["kotlin"])
        assert "kotlin" in result
        assert result["kotlin"][0][0] == "java"

    def test_handles_bad_json(self, monkeypatch):
        from applypilot.intelligence.adjacency_graph.providers import llm_provider

        class FakeClient:
            def chat(self, messages, **kw):
                return "not json at all"

        monkeypatch.setattr("applypilot.llm.get_client", lambda **kw: FakeClient())
        result = llm_provider._generate_batch(["kotlin"])
        assert result == {}

    def test_empty_input(self):
        from applypilot.intelligence.adjacency_graph.providers.llm_provider import generate_adjacencies

        assert generate_adjacencies([]) == {}
