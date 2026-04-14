"""Pipeline package — composable, builder-pattern pipeline with dependency inversion.

Also re-exports run_pipeline from the legacy module for backward compatibility.
"""

from applypilot.pipeline.builder import Pipeline, VALID_STAGES
from applypilot.pipeline.context import PipelineContext
from applypilot.pipeline.stage import Stage, StageResult


# Backward compat: upstream CLI imports run_pipeline from applypilot.pipeline
def run_pipeline(**kwargs) -> dict:
    """Legacy entry point — delegates to Pipeline.batch().execute()."""
    return Pipeline.batch(
        stages=kwargs.get("stages"),
        min_score=kwargs.get("min_score", 7),
        workers=kwargs.get("workers", 1),
        validation_mode=kwargs.get("validation_mode", "normal"),
        dry_run=kwargs.get("dry_run", False),
        stream=kwargs.get("stream", False),
        chunked=kwargs.get("chunked", False),
        chunk_size=kwargs.get("chunk_size", 1000),
        limit=kwargs.get("limit", 0),
        urls=kwargs.get("urls"),
        sources=kwargs.get("sources"),
        companies=kwargs.get("companies"),
        strict_title=kwargs.get("strict_title", False),
        force=kwargs.get("force", False),
    ).execute()


__all__ = ["Pipeline", "PipelineContext", "Stage", "StageResult", "run_pipeline", "VALID_STAGES"]
