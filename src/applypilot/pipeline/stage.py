"""Stage protocol and result dataclass."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from applypilot.pipeline.context import PipelineContext


@dataclass
class StageResult:
    stage: str
    status: str = "ok"
    elapsed: float = 0.0
    detail: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "partial", "skipped")


@runtime_checkable
class Stage(Protocol):
    name: str
    description: str

    def run(self, ctx: PipelineContext) -> StageResult: ...
