"""ApplyPilot agent backends - unified execution and configuration.

This module provides the unified AgentBackend abstraction that handles both
execution (run_job) and configuration (setup) for agent backends.

Usage:
    # Get a backend instance
    backend = get_backend("opencode")

    # Check if installed
    if backend.is_installed():
        # Setup/configure the backend
        backend.setup()

        # Execute a job
        status, duration = backend.run_job(...)
"""

from __future__ import annotations

import json
import shutil
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from typing import Sequence


class AgentBackendError(Exception):
    """Raised when agent backend operations fail."""

    pass


class AgentBackend(ABC):
    """Abstract base class for agent backends.

    Implementations provide both configuration/setup capabilities and
    execution capabilities for a specific agent backend (OpenCode, Claude, etc.).

    This unified interface allows ApplyPilot to:
    1. Detect available backends
    2. Configure/setup backends (MCP servers, etc.)
    3. Execute jobs using backends
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the backend identifier name."""
        ...

    @classmethod
    @abstractmethod
    def is_installed(cls) -> bool:
        """Check if this backend is installed and available.

        Returns:
            True if the backend CLI/tool is available on PATH.
        """
        ...

    @abstractmethod
    def get_version(self) -> str | None:
        """Get the installed version of this backend.

        Returns:
            Version string if available, None otherwise.
        """
        ...

    @abstractmethod
    def setup(self, import_from: str | None = None) -> dict[str, Any]:
        """Setup and configure the backend.

        This method handles initial configuration including MCP server
        registration, config file creation, etc.

        Args:
            import_from: Optional backend name to import config from.
                        For example, "claude" to import Claude MCP config.

        Returns:
            Dictionary with setup results:
            - success: bool
            - servers_added: list of server names
            - servers_existing: list of already present servers
            - errors: list of error messages
        """
        ...

    @abstractmethod
    def list_mcp_servers(self) -> list[str]:
        """List all MCP servers configured for this backend.

        Returns:
            List of configured MCP server names.

        Raises:
            AgentBackendError: If the operation fails.
        """
        ...

    @abstractmethod
    def add_mcp_server(
        self,
        name: str,
        command: list[str] | None = None,
        url: str | None = None,
        enabled: bool = True,
    ) -> bool:
        """Add an MCP server to this backend's configuration.

        Args:
            name: Server name.
            command: Command array for local servers (e.g., ["npx", "package"]).
            url: URL for remote servers.
            enabled: Whether the server should be enabled.

        Returns:
            True if successfully added.

        Raises:
            AgentBackendError: If the operation fails.
        """
        ...

    @abstractmethod
    def run_job(
        self,
        job: dict[str, Any],
        port: int,
        worker_id: int,
        model: str,
        agent: str | None,
        dry_run: bool,
        prompt: str,
        mcp_config_path: Path,
        worker_dir: Path,
        required_mcp_servers: Sequence[str] | None = None,
        update_callback: Any | None = None,
    ) -> tuple[str, int]:
        """Execute the agent for a single job application.

        Args:
            job: Job dictionary with url, title, site, etc.
            port: CDP port for browser connection.
            worker_id: Numeric worker identifier.
            model: Model name for the backend.
            dry_run: If True, don't actually submit applications.
            prompt: The full agent prompt text.
            mcp_config_path: Path to MCP configuration file.
            worker_dir: Working directory for the agent.
            update_callback: Optional callback for status updates.

        Returns:
            Tuple of (status_string, duration_ms).
        """
        ...

    @abstractmethod
    def get_active_proc(self, worker_id: int) -> subprocess.Popen | None:
        """Get the active process for a worker (for signal handling).

        Args:
            worker_id: Numeric worker identifier.

        Returns:
            The active subprocess.Popen instance, or None if not active.
        """
        ...


class OpenCodeBackend(AgentBackend):
    """OpenCode CLI backend implementation.

    Handles both configuration (MCP server setup) and execution (job running)
    for the OpenCode agent backend.
    """

    CLI_NAME = "opencode"
    CONFIG_FILENAME = "opencode.jsonc"
    DEFAULT_SERVERS = {
        "playwright": {
            "type": "local",
            "enabled": True,
            "command": [
                "npx",
                "@playwright/mcp@latest",
                "--cdp-endpoint=http://localhost:9222",
            ],
        },
        "gmail": {
            "type": "local",
            "enabled": True,
            "command": ["npx", "-y", "@gongrzhe/server-gmail-autoauth-mcp"],
        },
    }

    def __init__(self) -> None:
        self._active_procs: dict[int, subprocess.Popen] = {}
        from applypilot.config import APP_DIR

        self._config_dir = APP_DIR / ".opencode"
        self._config_path = self._config_dir / self.CONFIG_FILENAME

    @property
    def name(self) -> str:
        return "opencode"

    @classmethod
    def is_installed(cls) -> bool:
        return shutil.which(cls.CLI_NAME) is not None

    def get_version(self) -> str | None:
        try:
            result = subprocess.run(
                [self.CLI_NAME, "--version"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None

    def setup(self, import_from: str | None = None) -> dict[str, Any]:
        """Setup OpenCode backend with MCP servers.

        Args:
            import_from: Optional backend to import config from (e.g., "claude").

        Returns:
            Setup results dictionary.
        """
        result = {
            "success": False,
            "servers_added": [],
            "servers_existing": [],
            "errors": [],
        }

        if not self.is_installed():
            result["errors"].append("OpenCode CLI is not installed")
            return result

        # Ensure config directory exists
        self._config_dir.mkdir(parents=True, exist_ok=True)

        # Import from another backend if requested
        if import_from == "claude":
            self._import_from_claude(result)

        # Add default required servers
        self._add_default_servers(result)

        # Write config file
        try:
            self._write_config(result)
            result["success"] = True
        except Exception as e:
            result["errors"].append(f"Failed to write config: {e}")

        return result

    def _import_from_claude(self, result: dict[str, Any]) -> None:
        """Import MCP servers from Claude config."""
        claude_config_path = Path.home() / ".claude" / "claude.json"

        if not claude_config_path.exists():
            return

        try:
            content = claude_config_path.read_text(encoding="utf-8")
            claude_config = json.loads(content)
            claude_servers = claude_config.get("mcpServers", {})

            for name, config in claude_servers.items():
                try:
                    existing = set(self.list_mcp_servers())
                    if name in existing:
                        result["servers_existing"].append(name)
                        continue

                    # Convert Claude format to OpenCode format
                    opencode_config: dict[str, Any] = {"enabled": True}
                    if "command" in config:
                        opencode_config["type"] = "local"
                        command_parts = [config["command"]]
                        if "args" in config:
                            command_parts.extend(config["args"])
                        opencode_config["command"] = command_parts
                    elif "url" in config:
                        opencode_config["type"] = "remote"
                        opencode_config["url"] = config["url"]

                    if self.add_mcp_server(
                        name=name,
                        command=opencode_config.get("command"),
                        url=opencode_config.get("url"),
                        enabled=True,
                    ):
                        result["servers_added"].append(name)
                except Exception as e:
                    result["errors"].append(f"Error importing {name}: {e}")
        except Exception as e:
            result["errors"].append(f"Could not read Claude config: {e}")

    def _add_default_servers(self, result: dict[str, Any]) -> None:
        """Add default required MCP servers."""
        try:
            existing = set(self.list_mcp_servers())
        except AgentBackendError:
            existing = set()

        required = ["playwright", "gmail"]
        for server_name in required:
            if server_name not in existing and server_name in self.DEFAULT_SERVERS:
                config = self.DEFAULT_SERVERS[server_name]
                try:
                    if self.add_mcp_server(
                        name=server_name,
                        command=config.get("command"),
                        url=config.get("url"),
                        enabled=True,
                    ):
                        if server_name not in result["servers_added"]:
                            result["servers_added"].append(server_name)
                except Exception as e:
                    result["errors"].append(f"Error adding {server_name}: {e}")

    def _write_config(self, result: dict[str, Any]) -> None:
        """Write the opencode.jsonc configuration file."""
        all_servers = set(self.list_mcp_servers())

        full_config: dict[str, Any] = {
            "mcp": {},
            "_meta": {
                "initialized_by": "applypilot",
            },
        }

        # Add all configured servers
        for name in all_servers:
            if name in self.DEFAULT_SERVERS:
                full_config["mcp"][name] = self.DEFAULT_SERVERS[name]

        self._config_path.write_text(
            json.dumps(full_config, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def list_mcp_servers(self) -> list[str]:
        """List MCP servers using opencode CLI."""
        try:
            result = subprocess.run(
                [self.CLI_NAME, "mcp", "list"],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode != 0:
                raise AgentBackendError(f"Failed to list MCP servers: {result.stderr}")

            import re

            servers = []
            for line in result.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue

                # Remove ANSI escape codes and status indicators
                cleaned = re.sub(r"\x1B\[[0-9;]*[A-Za-z]", "", line)
                cleaned = re.sub(r"^[●✓✗*\s]+", "", cleaned)

                # Extract server name
                match = re.match(r"^([A-Za-z0-9_-]+)", cleaned)
                if match:
                    servers.append(match.group(1))

            return servers
        except Exception as e:
            raise AgentBackendError(f"Failed to list MCP servers: {e}")

    def add_mcp_server(
        self,
        name: str,
        command: list[str] | None = None,
        url: str | None = None,
        enabled: bool = True,
    ) -> bool:
        """Add an MCP server to OpenCode."""
        cmd_parts = [self.CLI_NAME, "mcp", "add", name]

        if url:
            cmd_parts.extend(["--url", url])
        elif command:
            cmd_parts.extend(["--command", command[0]])
            if len(command) > 1:
                cmd_parts.extend(["--"] + command[1:])

        try:
            result = subprocess.run(
                cmd_parts,
                capture_output=True,
                text=True,
                check=False,
            )
            return result.returncode == 0
        except Exception as e:
            raise AgentBackendError(f"Failed to add MCP server: {e}")

    def run_job(
        self,
        job: dict[str, Any],
        port: int,
        worker_id: int,
        model: str,
        agent: str | None,
        dry_run: bool,
        prompt: str,
        mcp_config_path: Path,
        worker_dir: Path,
        required_mcp_servers: Sequence[str] | None = None,
        update_callback: Any | None = None,
    ) -> tuple[str, int]:
        """Execute OpenCode for a single job application."""
        # Implementation would go here - simplified for this refactor
        raise NotImplementedError("run_job implementation needed")

    def get_active_proc(self, worker_id: int) -> subprocess.Popen | None:
        """Get the active process for a worker."""
        return self._active_procs.get(worker_id)


class ClaudeBackend(AgentBackend):
    """Claude Code CLI backend implementation."""

    CLI_NAME = "claude"
    CONFIG_FILENAME = "claude.json"
    DEFAULT_SERVERS = {
        "playwright": {
            "command": "npx",
            "args": ["@playwright/mcp@latest", "--cdp-endpoint=http://localhost:9222"],
        },
        "gmail": {
            "command": "npx",
            "args": ["-y", "@gongrzhe/server-gmail-autoauth-mcp"],
        },
    }

    def __init__(self) -> None:
        self._active_procs: dict[int, subprocess.Popen] = {}
        self._config_dir = Path.home() / ".claude"
        self._config_path = self._config_dir / self.CONFIG_FILENAME

    @property
    def name(self) -> str:
        return "claude"

    @classmethod
    def is_installed(cls) -> bool:
        return shutil.which(cls.CLI_NAME) is not None

    def get_version(self) -> str | None:
        try:
            result = subprocess.run(
                [self.CLI_NAME, "--version"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None

    def setup(self, import_from: str | None = None) -> dict[str, Any]:
        """Setup Claude backend with MCP servers."""
        result = {
            "success": False,
            "servers_added": [],
            "servers_existing": [],
            "errors": [],
        }

        if not self.is_installed():
            result["errors"].append("Claude CLI is not installed")
            return result

        # Ensure config directory exists
        self._config_dir.mkdir(parents=True, exist_ok=True)

        # Add default required servers
        self._add_default_servers(result)

        # Write config file
        try:
            self._write_config(result)
            result["success"] = True
        except Exception as e:
            result["errors"].append(f"Failed to write config: {e}")

        return result

    def _add_default_servers(self, result: dict[str, Any]) -> None:
        """Add default required MCP servers."""
        existing = set(self.list_mcp_servers())
        required = ["playwright", "gmail"]

        for server_name in required:
            if server_name not in existing and server_name in self.DEFAULT_SERVERS:
                config = self.DEFAULT_SERVERS[server_name]
                try:
                    if self.add_mcp_server(
                        name=server_name,
                        command=[config["command"]] + config.get("args", []),
                        enabled=True,
                    ):
                        result["servers_added"].append(server_name)
                except Exception as e:
                    result["errors"].append(f"Error adding {server_name}: {e}")
            elif server_name in existing:
                result["servers_existing"].append(server_name)

    def _write_config(self, result: dict[str, Any]) -> None:
        """Write the claude.json configuration file."""
        config = self._read_config()
        config["_meta"] = {"initialized_by": "applypilot"}

        self._config_path.write_text(
            json.dumps(config, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _read_config(self) -> dict[str, Any]:
        """Read the claude.json configuration file."""
        if not self._config_path.exists():
            return {"mcpServers": {}}
        try:
            content = self._config_path.read_text(encoding="utf-8")
            return json.loads(content)
        except Exception:
            return {"mcpServers": {}}

    def list_mcp_servers(self) -> list[str]:
        """List MCP servers from Claude config file."""
        config = self._read_config()
        return list(config.get("mcpServers", {}).keys())

    def add_mcp_server(
        self,
        name: str,
        command: list[str] | None = None,
        url: str | None = None,
        enabled: bool = True,
    ) -> bool:
        """Add an MCP server to Claude config."""
        config = self._read_config()

        if "mcpServers" not in config:
            config["mcpServers"] = {}

        server_config: dict[str, Any] = {}
        if command:
            server_config["command"] = command[0]
            if len(command) > 1:
                server_config["args"] = command[1:]
        elif url:
            server_config["url"] = url

        config["mcpServers"][name] = server_config

        self._config_path.write_text(
            json.dumps(config, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return True

    def run_job(
        self,
        job: dict[str, Any],
        port: int,
        worker_id: int,
        model: str,
        agent: str | None,
        dry_run: bool,
        prompt: str,
        mcp_config_path: Path,
        worker_dir: Path,
        required_mcp_servers: Sequence[str] | None = None,
        update_callback: Any | None = None,
    ) -> tuple[str, int]:
        """Execute Claude for a single job application."""
        # Implementation would go here - simplified for this refactor
        raise NotImplementedError("run_job implementation needed")

    def get_active_proc(self, worker_id: int) -> subprocess.Popen | None:
        """Get the active process for a worker."""
        return self._active_procs.get(worker_id)


# Backend registry
_BACKENDS: dict[str, type[AgentBackend]] = {
    "opencode": OpenCodeBackend,
    "claude": ClaudeBackend,
}


def get_backend(name: str) -> AgentBackend:
    """Get a backend instance by name.

    Args:
        name: Backend name ("opencode" or "claude").

    Returns:
        Backend instance.

    Raises:
        AgentBackendError: If backend is unknown.
    """
    if name not in _BACKENDS:
        raise AgentBackendError(f"Unknown backend: {name}. Available: {', '.join(_BACKENDS.keys())}")
    return _BACKENDS[name]()


def list_backends() -> list[str]:
    """List available backend names."""
    return list(_BACKENDS.keys())


def detect_backends() -> list[str]:
    """Detect which backends are installed."""
    return [name for name, cls in _BACKENDS.items() if cls.is_installed()]


def get_preferred_backend() -> str | None:
    """Get the preferred available backend.

    Returns:
        Name of preferred backend, or None if none available.
        Preference order: opencode > claude
    """
    available = detect_backends()
    if "opencode" in available:
        return "opencode"
    if "claude" in available:
        return "claude"
    return None




# Backward compatibility exports
VALID_BACKENDS: frozenset[str] = frozenset(_BACKENDS.keys())
DEFAULT_BACKEND: str = "claude"


class InvalidBackendError(AgentBackendError):
    """Raised when an unsupported backend identifier is provided."""

    def __init__(self, backend: str, available: frozenset[str] | None = None) -> None:
        self.backend = backend
        self.available = available or VALID_BACKENDS
        super().__init__(
            f"Invalid backend '{backend}'. "
            f"Supported backends: {', '.join(sorted(self.available))}. "
            f"Set via APPLY_BACKEND environment variable or backend config option."
        )


def get_available_backends() -> frozenset[str]:
    """Return the set of valid backend identifiers."""
    return VALID_BACKENDS


def resolve_default_model(backend: str) -> str:
    """Resolve the default model for a backend.
    
    Args:
        backend: Backend name.
        
    Returns:
        Default model name for the backend.
    """
    import os
    
    backend_lower = backend.lower().strip()
    
    if backend_lower == "claude":
        return os.getenv("APPLY_CLAUDE_MODEL", "haiku")
    elif backend_lower == "opencode":
        return os.getenv("APPLY_OPENCODE_MODEL") or os.getenv("LLM_MODEL", "gpt-4o-mini")
    else:
        return "gpt-4o-mini"


def resolve_default_agent(backend: str) -> str | None:
    """Resolve the default agent for a backend.
    
    Args:
        backend: Backend name.
        
    Returns:
        Default agent name or None.
    """
    import os
    
    backend_lower = backend.lower().strip()
    
    if backend_lower == "opencode":
        return os.getenv("APPLY_OPENCODE_AGENT")
    
    return None


# Override get_backend to support case-insensitive names and env var
_original_get_backend = get_backend


def _get_backend_compat(name: str | None = None) -> AgentBackend:
    """Get a backend instance with backward compatibility.
    
    Supports:
    - Case-insensitive backend names
    - APPLY_BACKEND environment variable
    - Whitespace trimming
    - InvalidBackendError exception
    """
    import os
    
    # If no name provided, check env var
    if name is None:
        name = os.getenv("APPLY_BACKEND", DEFAULT_BACKEND)
    
    # Normalize: lowercase and strip whitespace
    name_normalized = name.lower().strip()
    
    if name_normalized not in _BACKENDS:
        raise InvalidBackendError(name)
    
    return _BACKENDS[name_normalized]()


# Replace get_backend with compat version
get_backend = _get_backend_compat
