"""构建确定性的 VOC LangGraph 工作流。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.graph.state import RunState
from app.models.classification import FeedbackClassification, requires_human_review
from app.services.aggregate import aggregate_feedback
from app.services.cleaning import clean_records
from app.services.feishu import Notifier, completion_card
from app.services.issues import build_issue_candidates


Classifier = Callable[[dict[str, Any]], FeedbackClassification]


def _clean_node(state: RunState) -> dict[str, Any]:
    result = clean_records(state.get("input_rows", []), run_id=state["run_id"])
    return {
        "status": "running",
        "records": result.records,
        "counters": {
            "input_count": result.input_count,
            "cleaned_count": len(result.records),
            "duplicate_count": result.duplicate_count,
            "cleaning_failure_count": len(result.failures),
        },
        "errors": [failure.__dict__ for failure in result.failures],
    }


def _classify_node(state: RunState, classifier: Classifier | None) -> dict[str, Any]:
    if classifier is None:
        return {
            "status": "failed",
            "errors": state.get("errors", []) + [{
                "node": "classify",
                "error": "未注入 classifier；生产运行需要配置 LangChain 模型",
            }],
        }
    outputs: list[dict[str, Any]] = []
    review_ids: list[str] = []
    failures = list(state.get("errors", []))
    for record in state.get("records", []):
        try:
            result = classifier(record)
            if not isinstance(result, FeedbackClassification):
                raise TypeError("classifier 必须返回 FeedbackClassification")
            item = {
                "feedback_id": record["feedback_id"],
                **result.model_dump(),
                "needs_human_review": requires_human_review(result),
            }
            outputs.append(item)
            if item["needs_human_review"]:
                review_ids.append(record["feedback_id"])
        except Exception as exc:
            failures.append({
                "node": "classify",
                "feedback_id": record.get("feedback_id", ""),
                "error": str(exc),
            })
    return {
        "status": "running" if outputs else "failed",
        "classifications": outputs,
        "review_ids": review_ids,
        "errors": failures,
        "counters": {
            **state.get("counters", {}),
            "classified_count": len(outputs),
            "review_count": len(review_ids),
        },
    }


def _review_route(state: RunState) -> str:
    # 路由决策必须由确定性条件控制，不能让模型决定是否绕过人工复核。
    if state.get("status") == "failed":
        return "failed"
    return "review" if state.get("review_ids") else "continue"


def _wait_review_node(state: RunState) -> dict[str, Any]:
    # 中断会把完整状态写入检查点，人工提交结果后从这里继续执行。
    decision = interrupt({
        "type": "feedback_review",
        "run_id": state["run_id"],
        "review_ids": state.get("review_ids", []),
        "message": "请确认低置信度或敏感反馈的分类结果",
    })
    return {
        "status": "running",
        "review_decisions": decision if isinstance(decision, dict) else {},
    }


def _aggregate_node(state: RunState) -> dict[str, Any]:
    records_by_id = {record["feedback_id"]: record for record in state.get("records", [])}
    labeled = []
    for classification in state.get("classifications", []):
        record = records_by_id.get(classification["feedback_id"], {})
        labeled.append({**record, **classification})
    summary = aggregate_feedback(labeled)
    return {
        "status": "running",
        "aggregates": summary,
        "counters": {**state.get("counters", {}), "aggregated_count": summary["total_valid"]},
    }


def _finalize_node(state: RunState, repository: Any | None = None) -> dict[str, Any]:
    status = "completed" if state.get("status") != "failed" else "failed"
    if repository is not None:
        repository.upsert_run({**state, "status": status})
    return {"status": status}


def _build_issues_node(state: RunState) -> dict[str, Any]:
    issues = build_issue_candidates(state.get("records", []), state.get("classifications", []))
    return {"issue_candidates": issues}


def _persist_node(state: RunState, repository: Any | None) -> dict[str, Any]:
    if repository is None:
        return {"counters": {**state.get("counters", {}), "persisted": 0}}
    # 先写运行和明细，再写问题单；三者都使用幂等更新，支持重复运行。
    repository.upsert_run(state)
    repository.upsert_feedback(state.get("records", []), state.get("classifications", []))
    repository.upsert_issues(state.get("issue_candidates", []), state["run_id"])
    return {"counters": {**state.get("counters", {}), "persisted": 1}}


def _notify_node(state: RunState, notifier: Notifier | None) -> dict[str, Any]:
    payload = completion_card(state)
    if notifier is not None:
        notifier.send(payload)
    return {"notification_payload": payload}


def build_graph(
    *,
    classifier: Classifier | None = None,
    checkpointer: Any | None = None,
    repository: Any | None = None,
    notifier: Notifier | None = None,
) -> Any:
    """编译工作流，并支持注入分类器与检查点保存器。"""

    graph = StateGraph(RunState)
    graph.add_node("clean_records", _clean_node)
    graph.add_node("classify", lambda state: _classify_node(state, classifier))
    graph.add_node("wait_review", _wait_review_node)
    graph.add_node("aggregate", _aggregate_node)
    graph.add_node("build_issues", _build_issues_node)
    graph.add_node("persist", lambda state: _persist_node(state, repository))
    graph.add_node("notify", lambda state: _notify_node(state, notifier))
    graph.add_node("finalize", lambda state: _finalize_node(state, repository))
    graph.add_edge(START, "clean_records")
    graph.add_edge("clean_records", "classify")
    graph.add_conditional_edges(
        "classify",
        _review_route,
        {"review": "wait_review", "continue": "aggregate", "failed": "finalize"},
    )
    graph.add_edge("wait_review", "aggregate")
    graph.add_edge("aggregate", "build_issues")
    graph.add_edge("build_issues", "persist")
    graph.add_edge("persist", "notify")
    graph.add_edge("notify", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer or MemorySaver())
