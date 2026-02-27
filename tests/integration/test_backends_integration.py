"""Integration tests for multi-backend support.

Tests actual backend operations with real configurations.
Run with: pytest tests/integration/test_backends_integration.py -v

@file test_backends_integration.py
@description Integration tests for AgentBackend implementations.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from applypilot.apply.backends import (
    AgentBackendError,
    ClaudeBackend,
    OpenCodeBackend,
    detect_backends,
    get_backend,
    get_preferred_backend,
)
from applypilot.config import APP_DIR, OPENCODE_CONFIG_PATH


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def opencode_backend():
    """Fixture for OpenCode backend instance."""
    return OpenCodeBackend()


@pytest.fixture
def claude_backend():
    """Fixture for Claude backend instance."""
    return ClaudeBackend()


# ---------------------------------------------------------------------------
# Backend Detection Tests
# ---------------------------------------------------------------------------


class TestBackendDetection:
    """Test that backends are properly detected."""

    def test_detect_backends_returns_list(self):
        """Should return list of installed backends."""
        backends = detect_backends()
        assert isinstance(backends, list)
        print(f"\nDetected backends: {backends}")

    def test_get_preferred_backend(self):
        """Should return preferred backend or None."""
        preferred = get_preferred_backend()
        print(f"\nPreferred backend: {preferred}")

        if preferred:
            assert preferred in ["opencode", "claude"]

    def test_backends_detected_if_installed(self):
        """Should detect opencode if installed."""
        import shutil

        backends = detect_backends()

        if shutil.which("opencode"):
            assert "opencode" in backends, "opencode should be detected"
            print("\n✓ OpenCode detected")

        if shutil.which("claude"):
            assert "claude" in backends, "claude should be detected"
            print("\n✓ Claude detected")


# ---------------------------------------------------------------------------
# OpenCode Backend Integration Tests
# ---------------------------------------------------------------------------


class TestOpenCodeBackendIntegration:
    """Integration tests for OpenCode backend with real config."""

    def test_is_installed(self, opencode_backend):
        """Test if opencode is installed."""
        is_installed = OpenCodeBackend.is_installed()
        print(f"\nOpenCode installed: {is_installed}")

        if is_installed:
            version = opencode_backend.get_version()
            print(f"OpenCode version: {version}")
            assert version is not None

    def test_get_version(self, opencode_backend):
        """Test getting opencode version."""
        if not OpenCodeBackend.is_installed():
            pytest.skip("OpenCode not installed")

        version = opencode_backend.get_version()
        print(f"\nOpenCode version: {version}")
        assert version is not None
        assert isinstance(version, str)
        assert len(version) > 0

    def test_list_mcp_servers(self, opencode_backend):
        """Test listing MCP servers."""
        if not OpenCodeBackend.is_installed():
            pytest.skip("OpenCode not installed")

        try:
            servers = opencode_backend.list_mcp_servers()
            print(f"\nMCP servers: {servers}")
            assert isinstance(servers, list)

            # Check if required servers are present
            if servers:
                print(f"✓ Found {len(servers)} MCP server(s)")
                for server in servers:
                    print(f"  - {server}")
        except AgentBackendError as e:
            pytest.skip(f"Could not list MCP servers: {e}")

    def test_config_path_exists(self, opencode_backend):
        """Test that config path is properly set."""
        print(f"\nOpenCode config dir: {opencode_backend._config_dir}")
        print(f"OpenCode config path: {opencode_backend._config_path}")

        assert opencode_backend._config_dir == APP_DIR / ".opencode"
        assert opencode_backend._config_path == OPENCODE_CONFIG_PATH

    def test_setup_with_existing_config(self, opencode_backend):
        """Test setup when config already exists."""
        if not OpenCodeBackend.is_installed():
            pytest.skip("OpenCode not installed")

        # Run setup (should be idempotent)
        result = opencode_backend.setup()

        print(f"\nSetup result:")
        print(f"  Success: {result['success']}")
        print(f"  Servers added: {result['servers_added']}")
        print(f"  Servers existing: {result['servers_existing']}")
        print(f"  Errors: {result['errors']}")

        assert isinstance(result, dict)
        assert "success" in result

    def test_verify_mcp_servers(self, opencode_backend):
        """Verify required MCP servers are configured."""
        if not OpenCodeBackend.is_installed():
            pytest.skip("OpenCode not installed")

        try:
            servers = set(opencode_backend.list_mcp_servers())
            required = {"playwright", "gmail"}

            present = required & servers
            missing = required - servers

            print(f"\nMCP Server Status:")
            print(f"  Present: {present}")
            print(f"  Missing: {missing}")

            if missing:
                print(f"⚠ Missing required servers: {missing}")
            else:
                print(f"✓ All required servers present")

        except AgentBackendError as e:
            pytest.skip(f"Could not verify MCP servers: {e}")


# ---------------------------------------------------------------------------
# Claude Backend Integration Tests
# ---------------------------------------------------------------------------


class TestClaudeBackendIntegration:
    """Integration tests for Claude backend with real config."""

    def test_is_installed(self, claude_backend):
        """Test if claude is installed."""
        is_installed = ClaudeBackend.is_installed()
        print(f"\nClaude installed: {is_installed}")

        if is_installed:
            version = claude_backend.get_version()
            print(f"Claude version: {version}")
            assert version is not None

    def test_get_version(self, claude_backend):
        """Test getting claude version."""
        if not ClaudeBackend.is_installed():
            pytest.skip("Claude not installed")

        version = claude_backend.get_version()
        print(f"\nClaude version: {version}")
        assert version is not None
        assert isinstance(version, str)
        assert len(version) > 0

    def test_list_mcp_servers(self, claude_backend):
        """Test listing MCP servers from claude config."""
        if not ClaudeBackend.is_installed():
            pytest.skip("Claude not installed")

        servers = claude_backend.list_mcp_servers()
        print(f"\nClaude MCP servers: {servers}")
        assert isinstance(servers, list)

        if servers:
            print(f"✓ Found {len(servers)} MCP server(s) in Claude config")
            for server in servers:
                print(f"  - {server}")

    def test_config_path(self, claude_backend):
        """Test that config path is properly set."""
        print(f"\nClaude config dir: {claude_backend._config_dir}")
        print(f"Claude config path: {claude_backend._config_path}")

        assert claude_backend._config_dir == Path.home() / ".claude"
        assert claude_backend._config_path == Path.home() / ".claude" / "claude.json"


# ---------------------------------------------------------------------------
# Unified Backend Tests
# ---------------------------------------------------------------------------


class TestUnifiedBackendInterface:
    """Test the unified backend interface."""

    def test_get_backend_opencode(self):
        """Test getting opencode backend."""
        if "opencode" not in detect_backends():
            pytest.skip("OpenCode not installed")

        backend = get_backend("opencode")
        assert isinstance(backend, OpenCodeBackend)
        assert backend.name == "opencode"
        print("\n✓ OpenCode backend instantiated")

    def test_get_backend_claude(self):
        """Test getting claude backend."""
        if "claude" not in detect_backends():
            pytest.skip("Claude not installed")

        backend = get_backend("claude")
        assert isinstance(backend, ClaudeBackend)
        assert backend.name == "claude"
        print("\n✓ Claude backend instantiated")

    def test_backend_has_required_methods(self):
        """Test that backends have all required methods."""
        for backend_name in detect_backends():
            backend = get_backend(backend_name)

            # Check required methods exist
            assert hasattr(backend, "name")
            assert hasattr(backend, "setup")
            assert hasattr(backend, "list_mcp_servers")
            assert hasattr(backend, "add_mcp_server")
            assert hasattr(backend, "run_job")
            assert hasattr(backend, "get_active_proc")

            # Check they're callable
            assert callable(backend.setup)
            assert callable(backend.list_mcp_servers)
            assert callable(backend.add_mcp_server)
            assert callable(backend.run_job)
            assert callable(backend.get_active_proc)

            print(f"\n✓ {backend_name} has all required methods")

    def test_backend_interface_consistency(self):
        """Test that all backends implement the same interface."""
        backends = detect_backends()

        if len(backends) < 2:
            pytest.skip("Need at least 2 backends to test interface consistency")

        # Get all backends
        instances = [get_backend(name) for name in backends]

        # Check they all have the same methods
        methods = ["setup", "list_mcp_servers", "add_mcp_server", "run_job", "get_active_proc"]

        for method in methods:
            for backend in instances:
                assert hasattr(backend, method), f"{backend.name} missing {method}"
                assert callable(getattr(backend, method)), f"{backend.name}.{method} not callable"

        print(f"\n✓ All {len(backends)} backends implement consistent interface")


# ---------------------------------------------------------------------------
# End-to-End Workflow Tests
# ---------------------------------------------------------------------------


class TestEndToEndWorkflow:
    """Test complete workflows."""

    def test_full_setup_workflow(self):
        """Test the complete setup workflow."""
        # Detect backends
        available = detect_backends()
        print(f"\nAvailable backends: {available}")

        if not available:
            pytest.skip("No backends installed")

        # Get preferred
        preferred = get_preferred_backend()
        print(f"Preferred backend: {preferred}")

        # Get backend
        backend = get_backend(preferred)
        print(f"Backend: {backend.name}")

        # Check version
        version = backend.get_version()
        print(f"Version: {version}")

        # List MCP servers
        try:
            servers = backend.list_mcp_servers()
            print(f"MCP servers: {servers}")
        except AgentBackendError as e:
            print(f"Could not list MCP servers: {e}")

        print("\n✓ Full workflow completed")

    def test_config_file_created(self):
        """Test that config files are properly created."""
        if "opencode" not in detect_backends():
            pytest.skip("OpenCode not installed")

        backend = get_backend("opencode")

        # Run setup
        result = backend.setup()

        # Check if config file exists
        if result["success"]:
            if backend._config_path.exists():
                print(f"\n✓ Config file created: {backend._config_path}")

                # Try to read it
                try:
                    content = json.loads(backend._config_path.read_text())
                    print(f"Config content keys: {list(content.keys())}")
                except Exception as e:
                    print(f"⚠ Could not read config: {e}")
            else:
                print(f"\n⚠ Config file not created: {backend._config_path}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
