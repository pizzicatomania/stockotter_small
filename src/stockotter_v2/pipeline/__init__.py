"""Pipeline orchestration for one-command runs."""

from .run import (
    CandidateReportRow,
    PipelineRunResult,
    PipelineStageSummary,
    render_report_table,
    render_stage_table,
    run_pipeline,
)

__all__ = [
    "CandidateReportRow",
    "PipelineRunResult",
    "PipelineStageSummary",
    "render_report_table",
    "render_stage_table",
    "run_pipeline",
]
