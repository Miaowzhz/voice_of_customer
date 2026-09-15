"""根据已分类反馈构建稳定且去重的 Issue 候选。"""

from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
from typing import Any, Iterable


def issue_key(sku: str, category: str, subcategory: str) -> str:
    raw = "|".join((sku.strip().lower(), category.strip(), subcategory.strip()))
    return sha256(raw.encode("utf-8")).hexdigest()[:24]


def build_issue_candidates(
    records: Iterable[dict[str, Any]],
    classifications: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """合并标准化后 SKU、类别和子类相同的反馈。"""

    records_by_id = {row["feedback_id"]: row for row in records}
    groups: defaultdict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for classification in classifications:
        record = records_by_id.get(classification.get("feedback_id", ""), {})
        sku = str(classification.get("sku") or record.get("sku") or "未知 SKU")
        category = str(classification.get("category") or "其他/待人工确认")
        subcategory = str(classification.get("subcategory") or "信息不足")
        groups[(sku, category, subcategory)].append({**record, **classification})

    candidates: list[dict[str, Any]] = []
    for (sku, category, subcategory), group in sorted(
        groups.items(), key=lambda item: (-len(item[1]), item[0])
    ):
        first = group[0]
        candidates.append({
            "issue_key": issue_key(sku, category, subcategory),
            "issue_id": f"ISSUE-{issue_key(sku, category, subcategory)[:10].upper()}",
            "title": f"{sku}｜{category}｜{subcategory}",
            "sku": sku,
            "category": category,
            "subcategory": subcategory,
            "feedback_count": len(group),
            "evidence": [
                row.get("evidence") or row.get("sanitized_text") or row.get("text", "")
                for row in group[:3]
            ],
            "suggested_action": first.get("suggested_action", "人工确认并制定处理动作"),
            "owner": first.get("suggested_owner", "客服主管"),
            "status": "待处理",
            "feedback_ids": [row["feedback_id"] for row in group],
        })
    return candidates
