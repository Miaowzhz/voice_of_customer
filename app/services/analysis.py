"""根据本批次模型分类生成可核对的分析摘要和报告文件。"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from app.services.chart import write_distribution_summary
from app.services.cleaning import mask_sensitive


def build_analysis_report(state: dict[str, Any]) -> dict[str, Any]:
    """以分类成功记录为占比分母，显式披露复核、失败与去重数量。"""

    summary = state.get("aggregates", {})
    classified = state.get("classifications", [])
    records = state.get("records", [])
    products = sorted({row.get("sku", "") for row in records if row.get("sku")})
    sentiments = Counter(item.get("sentiment", "未知") for item in classified)
    actions = []
    for issue in summary.get("top_issues", []):
        candidates = [item for item in classified if item["category"] == issue["category"] and item["subcategory"] == issue["subcategory"]]
        first = candidates[0] if candidates else {}
        actions.append({
            **issue,
            "suggested_action": mask_sensitive(first.get("suggested_action", "人工确认")),
            "suggested_owner": mask_sensitive(first.get("suggested_owner", "待分派")),
        })
    return {
        "run_id": state["run_id"],
        "product": "、".join(products) or "未标注产品",
        "input_count": state.get("counters", {}).get("input_count", 0),
        "classified_count": len(classified),
        "duplicate_count": state.get("counters", {}).get("duplicate_count", 0),
        "failure_count": len(state.get("errors", [])),
        "review_count": len(state.get("review_ids", [])),
        "category_distribution": summary.get("category_distribution", []),
        "grade_distribution": state.get("grade_distribution", []),
        "grade_analyses": state.get("grade_analyses", {}),
        "sentiment_counts": dict(sentiments),
        "top_issues": actions,
        "statistics_note": "等级占比分母为完成等级评价的反馈数；失败项和重复项不计入统计。",
    }


def format_analysis_report(report: dict[str, Any]) -> str:
    """输出适合飞书阅读的精简摘要，详细数据仍保存在本地报告中。"""

    distribution = {item["grade"]: item for item in report.get("grade_distribution", [])}
    grade_text = "，".join(
        f"{grade}评 {distribution.get(grade, {}).get('count', 0)} 条"
        f"（{distribution.get(grade, {}).get('ratio', 0):.1f}%）"
        for grade in ("好", "中", "差")
    )
    summary = (
        f"共分析 {report['classified_count']} 条反馈，{grade_text}。"
        f"待复核 {report['review_count']} 条。"
    )
    lines = [
        "✅ 产品反馈分析完成",
        f"产品：{report['product']}",
        "",
        "简短总结",
        summary,
    ]
    analyses = report.get("grade_analyses", {})
    for grade, label, fields in (
        ("好", "好评原因", ("reasons",)),
        ("中", "中评改进", ("improvements",)),
        ("差", "差评原因", ("reasons",)),
    ):
        analysis = analyses.get(grade, {})
        lines.extend(["", label, analysis.get("summary", f"暂无{label}分析。")])
        items = [item for field in fields for item in analysis.get(field, [])[:3]]
        lines.extend(f"• {item}" for item in items)
    lines.extend(["", f"运行编号：{report['run_id']}", "等级饼图和好评词云见随后发送的图片。"])
    return "\n".join(lines)


def write_analysis_artifacts(state: dict[str, Any]) -> dict[str, Any]:
    """将机器可读报告、文字摘要和饼图保存到本批次独立目录。"""

    report = build_analysis_report(state)
    directory = Path(state["artifact_dir"])
    report_path = write_distribution_summary(report, directory / "analysis.json")
    text = format_analysis_report(report)
    (directory / "analysis.md").write_text(text, encoding="utf-8")
    from app.services.chart import render_grade_pie_chart, render_good_wordcloud
    grade_chart = render_grade_pie_chart(report["grade_distribution"], report["product"], directory / "grade_distribution.png")
    labeled = []
    for record in state.get("records", []):
        labeled.append({**record, **next((item for item in state.get("classifications", []) if item["feedback_id"] == record["feedback_id"]), {})})
    wordcloud = render_good_wordcloud([item for item in labeled if item.get("grade") == "好"], directory / "good_wordcloud.png")
    return {"analysis_report": report, "analysis_text": text, "report_ref": str(report_path), "chart_ref": str(grade_chart), "grade_pie_ref": str(grade_chart), "good_wordcloud_ref": str(wordcloud)}
