"""根据本批次模型分类生成可核对的分析摘要和报告文件。"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from app.services.chart import render_pie_chart, write_distribution_summary
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
        "sentiment_counts": dict(sentiments),
        "top_issues": actions,
        "statistics_note": "占比分母为分类成功的反馈数，包含待人工复核的初步分类；失败项和重复项不计入饼图。",
    }


def format_analysis_report(report: dict[str, Any]) -> str:
    """输出飞书文本摘要，限制证据长度以保持报告可读。"""

    lines = [
        "✅ 产品反馈分析完成",
        f"产品：{report['product']}",
        "",
        "结果概览",
        f"• 本次反馈：{report['input_count']} 条",
        f"• 已完成分类：{report['classified_count']} 条",
        f"• 待人工复核：{report['review_count']} 条",
        f"• 处理失败：{report['failure_count']} 条",
        f"• 重复记录：{report['duplicate_count']} 条",
        "",
        "反馈类型分布",
    ]
    for item in report["category_distribution"]:
        lines.append(f"• {item['category']}：{item['count']} 条，占比 {item['ratio']:.1f}%")
    sentiments = report["sentiment_counts"]
    lines.extend(["", "情感分布：" + "，".join(f"{label} {count} 条" for label, count in sentiments.items()), "", "主要问题与建议"])
    for i, item in enumerate(report["top_issues"][:3], 1):
        evidence = str(item.get("evidence", [""])[0])[:100]
        lines.extend([
            f"{i}. {item['subcategory']}（{item['count']} 条）",
            f"典型反馈：{evidence}",
            f"处理建议：{item['suggested_action'][:180]}",
            f"建议负责人：{item['suggested_owner']}",
        ])
    lines.extend(["", f"统计口径：{report['statistics_note']}", f"运行编号：{report['run_id']}", "饼图将在下一条消息发送。"])
    return "\n".join(lines)


def write_analysis_artifacts(state: dict[str, Any]) -> dict[str, Any]:
    """将机器可读报告、文字摘要和饼图保存到本批次独立目录。"""

    report = build_analysis_report(state)
    directory = Path(state["artifact_dir"])
    report_path = write_distribution_summary(report, directory / "analysis.json")
    text = format_analysis_report(report)
    (directory / "analysis.md").write_text(text, encoding="utf-8")
    chart = render_pie_chart({
        **state["aggregates"], "product": report["product"], "review_count": report["review_count"],
    }, directory / "feedback_types.png")
    return {"analysis_report": report, "analysis_text": text, "report_ref": str(report_path), "chart_ref": str(chart)}
