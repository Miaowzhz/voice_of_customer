"""构建确定性的 VOC LangGraph 工作流。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.graph.state import RunState
from app.models.classification import FeedbackClassification, requires_human_review
from app.services.aggregate import aggregate_feedback, grade_distribution
from app.services.analysis import write_analysis_artifacts
from app.services.cleaning import clean_records, mask_sensitive
from app.services.feishu import Notifier, completion_card
from app.services.issues import build_issue_candidates


Classifier = Callable[[dict[str, Any]], FeedbackClassification]
GradeAnalyzer = Callable[[list[dict[str, Any]], str], Any]


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
                # 产品归属来自输入数据，不能被模型猜测的 SKU 覆盖。
                "sku": record.get("sku", ""),
                "evidence": mask_sensitive(result.evidence),
                "needs_human_review": requires_human_review(result) or not record.get("sku"),
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
    # 批量分析先报告初步结果，人工复核标记随明细保存，不阻塞整批统计。
    if state.get("review_policy") == "report":
        return "continue"
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
        "grade_distribution": grade_distribution(labeled),
        "counters": {**state.get("counters", {}), "aggregated_count": summary["total_valid"]},
    }


def _finalize_node(state: RunState, repository: Any | None = None) -> dict[str, Any]:
    status = "completed" if state.get("status") != "failed" else "failed"
    if repository is not None:
        repository.upsert_run({**state, "status": status})
    return {"status": status}


def _build_issues_node(state: RunState) -> dict[str, Any]:
    classifications = state.get("classifications", [])
    if state.get("review_policy") == "report":
        # 初步分析不直接转成执行任务，避免把不确定或不可行动的结果下派。
        classifications = [item for item in classifications if not item.get("needs_human_review") and item.get("is_actionable")]
    issues = build_issue_candidates(state.get("records", []), classifications)
    return {"issue_candidates": issues}


def _report_node(state: RunState) -> dict[str, Any]:
    if not state.get("artifact_dir"):
        return {}
    analyses = {
        grade: state.get(key, {})
        for grade, key in (("好", "good_analysis"), ("中", "middle_analysis"), ("差", "bad_analysis"))
    }
    state = {**state, "grade_analyses": analyses}
    return write_analysis_artifacts(state)


def _grade_analysis_node(state: RunState, grade: str, output_key: str, analyzer: GradeAnalyzer | None) -> dict[str, Any]:
    records_by_id = {record["feedback_id"]: record for record in state.get("records", [])}
    rows = []
    for item in state.get("classifications", []):
        if item.get("grade", "中") == grade:
            rows.append({**records_by_id.get(item["feedback_id"], {}), **item})
    if analyzer is not None and rows:
        try:
            result = analyzer(rows, grade)
            value = result.model_dump() if hasattr(result, "model_dump") else dict(result)
        except Exception as exc:
            value = _fallback_grade_analysis(rows, grade)
            value["error"] = f"等级分析模型调用失败：{exc}"
    else:
        value = _fallback_grade_analysis(rows, grade)
    value["count"] = len(rows)
    return {output_key: value}


def _fallback_grade_analysis(rows: list[dict[str, Any]], grade: str) -> dict[str, Any]:
    if not rows:
        return {"grade": grade, "summary": f"本批次暂无{grade}评反馈。", "reasons": [], "improvements": []}
    counts: dict[str, int] = {}
    for row in rows:
        label = f"{row.get('category', '其他')} / {row.get('subcategory', '其他')}"
        counts[label] = counts.get(label, 0) + 1
    top = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:3]
    labels = "、".join(f"{label}（{count}条）" for label, count in top)
    if grade == "好":
        return {"grade": grade, "summary": f"好评主要集中在{labels}。", "reasons": [f"反馈中最常出现：{labels}"], "improvements": []}
    if grade == "中":
        return {"grade": grade, "summary": f"中评主要集中在{labels}，建议针对这些环节优化体验。", "reasons": [f"反馈中最常出现：{labels}"], "improvements": ["补充使用说明并跟进用户未解决的问题"]}
    return {"grade": grade, "summary": f"差评主要集中在{labels}，建议优先排查并闭环。", "reasons": [f"反馈中最常出现：{labels}"], "improvements": ["建立专项负责人和处理时限，优先核查高风险问题"]}


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
    grade_analyzer: GradeAnalyzer | None = None,
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
    graph.add_node("analyze_good", lambda state: _grade_analysis_node(state, "好", "good_analysis", grade_analyzer))
    graph.add_node("analyze_middle", lambda state: _grade_analysis_node(state, "中", "middle_analysis", grade_analyzer))
    graph.add_node("analyze_bad", lambda state: _grade_analysis_node(state, "差", "bad_analysis", grade_analyzer))
    graph.add_node("report", _report_node)
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
    graph.add_edge("aggregate", "analyze_good")
    graph.add_edge("aggregate", "analyze_middle")
    graph.add_edge("aggregate", "analyze_bad")
    graph.add_edge("analyze_good", "report")
    graph.add_edge("analyze_middle", "report")
    graph.add_edge("analyze_bad", "report")
    graph.add_edge("report", "build_issues")
    graph.add_edge("build_issues", "persist")
    graph.add_edge("persist", "notify")
    graph.add_edge("notify", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer or MemorySaver())
