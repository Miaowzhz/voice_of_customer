"""根据已持久化的分类反馈生成 VOC 周报。"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

from app.services.aggregate import aggregate_feedback


def _period(end_date: str | date | None) -> tuple[str, str, str]:
    # 周报使用含首尾日期的 7 天窗口；SQL 结束条件采用次日零点，避免漏掉当天记录。
    if end_date is None:
        end = date.today()
    elif isinstance(end_date, date):
        end = end_date
    else:
        end = date.fromisoformat(end_date)
    start = end - timedelta(days=6)
    return (
        start.strftime("%Y-%m-%d 00:00:00"),
        (end + timedelta(days=1)).strftime("%Y-%m-%d 00:00:00"),
        f"{start.isoformat()}~{end.isoformat()}",
    )


def generate_weekly_report(repository: Any, *, end_date: str | date | None = None) -> dict[str, Any]:
    start_at, end_at, period = _period(end_date)
    rows = [dict(row) for row in repository.list_feedback(start_at, end_at)]
    summary = aggregate_feedback(rows, category_field="category")
    issue_counts = Counter(
        (row.get("sku") or "未知 SKU", row.get("category") or "其他/待人工确认", row.get("subcategory") or "其他")
        for row in rows
    )
    # 重复投诉率按同一 SKU + 类别 + 子类出现多条反馈计算，反映问题集中度。
    repeated = sum(count for count in issue_counts.values() if count > 1)
    report = {
        "period": period,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_feedback": len(rows),
        "repeated_complaint_rate": round(repeated / len(rows), 4) if rows else 0.0,
        "category_distribution": summary["category_distribution"],
        "top_issues": summary["top_issues"],
        "channel_counts": summary["channel_counts"],
        "sku_counts": summary["sku_counts"],
        "issue_status_counts": Counter(row["status"] for row in repository.list_issues()),
    }
    report["issue_status_counts"] = dict(report["issue_status_counts"])
    return report
