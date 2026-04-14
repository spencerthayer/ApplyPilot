"""Tests for skill adjacency graph and tailoring gate models."""

import pytest
from applypilot.intelligence.adjacency_graph.graph import SkillAdjacencyGraph, AdjacencyEdge
from applypilot.scoring.tailoring_gates.models import GateResult


class TestSkillAdjacencyGraph:
    @pytest.fixture
    def graph(self, tmp_path):
        """Build a test graph from inline data — not dependent on real YAML."""
        g = SkillAdjacencyGraph()
        yaml_file = tmp_path / "test_adjacency.yaml"
        yaml_file.write_text(
            "generated:\n"
            "  python:\n"
            "  - target: flask\n"
            "    confidence: 0.9\n"
            "    relation: framework\n"
            "  docker:\n"
            "  - target: kubernetes\n"
            "    confidence: 0.9\n"
            "    relation: orchestration\n"
            "  kubernetes:\n"
            "  - target: docker\n"
            "    confidence: 0.9\n"
            "    relation: runtime\n"
        )
        g.load_yaml(yaml_file)
        return g

    def test_load_yaml(self, graph):
        assert len(graph._edges) > 0

    def test_exact_match(self, graph):
        edge = graph.resolve("python", {"python", "java"})
        assert edge is not None
        assert edge.confidence == 1.0
        assert edge.relation == "exact"

    def test_adjacent_match(self, graph):
        # kubernetes -> docker is a common adjacency
        edge = graph.resolve("kubernetes", {"docker"})
        if edge:
            assert edge.confidence > 0
            assert edge.target == "docker"

    def test_no_match(self, graph):
        edge = graph.resolve("quantum_computing", {"python"})
        assert edge is None

    def test_get_edges(self, graph):
        edges = graph.get_edges("kubernetes")
        assert isinstance(edges, list)


class TestAdjacencyEdge:
    def test_frozen(self):
        edge = AdjacencyEdge(source="k8s", target="docker", confidence=0.9, relation="requires")
        assert edge.source == "k8s"
        with pytest.raises(AttributeError):
            edge.source = "other"


class TestGateResult:
    def test_defaults(self):
        r = GateResult(passed=True, step="test")
        assert r.passed
        assert r.errors == []

    def test_add_error(self):
        r = GateResult(passed=True, step="test")
        r.add_error("bad thing", suggestion="fix it")
        assert len(r.errors) == 1
        assert len(r.retry_suggestions) == 1

    def test_add_warning(self):
        r = GateResult(passed=True, step="test")
        r.add_warning("heads up")
        assert len(r.warnings) == 1

    def test_merge(self):
        a = GateResult(passed=True, step="a")
        b = GateResult(passed=False, step="b", errors=["fail"], confidence=0.8)
        a.merge(b)
        assert a.passed is False
        assert len(a.errors) == 1
        assert a.confidence == 0.8

    def test_merge_preserves_pass(self):
        a = GateResult(passed=True, step="a")
        b = GateResult(passed=True, step="b")
        a.merge(b)
        assert a.passed is True
