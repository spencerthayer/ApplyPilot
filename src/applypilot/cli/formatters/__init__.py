"""CLI formatting utilities — Rich tables and panels."""

from applypilot.cli.formatters.table import (
    stats_table,
    score_distribution_table,
    jobs_table,
)
from applypilot.cli.formatters.panel import (
    info_panel,
    error_panel,
    success_panel,
    stage_panel,
)

__all__ = [
    "stats_table",
    "score_distribution_table",
    "jobs_table",
    "info_panel",
    "error_panel",
    "success_panel",
    "stage_panel",
]
