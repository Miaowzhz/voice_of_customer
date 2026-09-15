"""用于反馈摘要和图表的确定性聚合服务。"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable


def _label(record: dict[str, Any], category_field: str) -> str:
    return str(record.get(category_field, "") or "").strip() or "其他/待人工确认"


def aggregate_feedback(
    records: Iterable[dict[str, Any]],
    *,
    category_field: str = "category",
) -> dict[str, Any]:
    """聚合类别、SKU、渠道和 Issue 候选指标。

    返回的 category_distribution 可直接用于饼图渲染。
    分母为本次传入的有效记录数量，调用方负责提供分类成功的记录。
    """

    rows = list(records)
    total = len(rows)
    category_counts = Counter(_label(row, category_field) for row in rows)
    distribution = [
        {
            "category": category,
            "count": count,
            "ratio": round(count / total * 100, 1) if total else 0.0,
        }
        for category, count in category_counts.most_common()
    ]

    sku_counts = Counter(str(row.get("sku", "") or "") or "未知 SKU" for row in rows)
    channel_counts = Counter(str(row.get("channel", "") or "未知") for row in rows)

    issue_groups: defaultdict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (
            str(row.get("sku", "") or "") or "未知 SKU",
            _label(row, category_field),
            str(row.get("subcategory", "") or "") or "其他",
        )
        issue_groups[key].append(row)

    top_issues = []
    for (sku, category, subcategory), group in sorted(
        issue_groups.items(), key=lambda item: (-len(item[1]), item[0])
    )[:5]:
        top_issues.append({
            "sku": sku,
            "category": category,
            "subcategory": subcategory,
            "count": len(group),
            "evidence": [row.get("sanitized_text", row.get("text", "")) for row in group[:3]],
        })

    return {
        "total_valid": total,
        "category_distribution": distribution,
        "sku_counts": dict(sku_counts.most_common()),
        "channel_counts": dict(channel_counts.most_common()),
        "top_issues": top_issues,
    }


def grade_distribution(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """按好、中、差固定顺序统计等级占比，空等级也保留。"""

    rows = list(records)
    counts = Counter(str(row.get("grade", "中") or "中") for row in rows)
    total = len(rows)
    return [
        {"grade": grade, "count": counts.get(grade, 0), "ratio": round(counts.get(grade, 0) / total * 100, 1) if total else 0.0}
        for grade in ("好", "中", "差")
    ]
