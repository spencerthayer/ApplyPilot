"""Base service types."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ServiceResult:
    """Uniform result envelope for all service methods."""

    success: bool = True
    data: dict | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
