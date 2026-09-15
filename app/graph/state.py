"""一次 VOC 分析运行过程中传递的可序列化状态。"""

from __future__ import annotations

from typing import Any, Literal, TypedDict


class RunState(TypedDict, total=False):
    run_id: str
    source_type: Literal["single", "batch"]
    source_ref: str
    notification_target: dict[str, str]
    status: Literal["received", "running", "waiting_review", "completed", "failed"]
    input_rows: list[dict[str, Any]]
    records: list[dict[str, Any]]
    classifications: list[dict[str, Any]]
    review_ids: list[str]
    review_decisions: dict[str, dict[str, Any]]
    aggregates: dict[str, Any]
    issue_candidates: list[dict[str, Any]]
    chart_ref: str | None
    artifact_dir: str
    review_policy: Literal["pause", "report"]
    analysis_report: dict[str, Any]
    analysis_text: str
    report_ref: str
    notification_payload: dict[str, Any]
    errors: list[dict[str, Any]]
    counters: dict[str, int]


def initial_state(
    run_id: str,
    rows: list[dict[str, Any]],
    *,
    source_type: Literal["single", "batch"] = "batch",
    source_ref: str = "",
) -> RunState:
    return {
        "run_id": run_id,
        "source_type": source_type,
        "source_ref": source_ref,
        "status": "received",
        "input_rows": rows,
        "records": [],
        "classifications": [],
        "review_ids": [],
        "review_decisions": {},
        "aggregates": {},
        "issue_candidates": [],
        "chart_ref": None,
        "errors": [],
        "counters": {},
    }
