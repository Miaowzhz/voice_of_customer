"""将分类器预测结果与人工标注基准集进行评估。

Examples:
    python scripts/evaluate_classifier.py --mode keyword
    OPENAI_API_KEY=... python scripts/evaluate_classifier.py --mode llm
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.models.classification import FeedbackClassification
from app.services.classify import build_chat_model, classify_one
from app.services.cleaning import load_feedback_file


def keyword_predict(record: dict[str, Any]) -> FeedbackClassification:
    """无需 API 密钥即可运行评估流程的离线冒烟预测器。"""

    text = str(record.get("text", ""))
    sku = str(record.get("sku", ""))
    rules = [
        ("价格、优惠和活动规则", "优惠券", ["优惠券", "满减", "会员券", "叠加"]),
        ("售后服务与退款", "退款进度", ["退款", "换货", "补发", "客服", "处理中"]),
        ("配件/赠品缺失", "配件缺失", ["少了", "缺少", "赠品", "配件", "三件套"]),
        ("功能/兼容性", "电磁炉兼容", ["电磁炉", "燃气灶", "烤箱", "加热"]),
        ("使用方法与清洁", "清洁方式", ["清洁", "洗碗机", "钢丝球", "说明书", "开锅"]),
        ("包装与物流损坏", "产品运输损坏", ["包装", "物流", "快递", "签收", "磕碰", "破损"]),
        ("尺寸与规格", "尺寸不符", ["尺寸", "厘米", "重量", "容量", "放进"]),
        ("商品描述或直播承诺不一致", "参数描述不一致", ["页面", "直播", "描述", "展示"]),
        ("产品质量", "涂层", ["粘锅", "涂层", "起泡", "裂纹", "变形", "漏水", "异味", "松动", "锈"]),
    ]
    category, subcategory = "其他/待人工确认", "信息不足"
    for candidate, sub, keywords in rules:
        if any(keyword in text for keyword in keywords):
            category, subcategory = candidate, sub
            break
    severity = "高" if any(word in text for word in ["安全", "裂纹", "脱落", "漏汤", "不敢继续", "退款"]) else "中"
    if category == "其他/待人工确认":
        severity = "低"
    return FeedbackClassification(
        category=category,
        subcategory=subcategory,
        sentiment="负面" if any(word in text for word in ["粘", "破", "缺", "慢", "不一致", "问题", "没有"]) else "中性",
        severity=severity,
        is_actionable=True,
        sku=sku,
        evidence=text[:120],
        suggested_owner="客服主管" if category == "售后服务与退款" else "品控",
        suggested_action="根据分类结果核查并安排责任人处理",
        confidence=0.7 if category == "其他/待人工确认" else 0.8,
        needs_human_review=category == "其他/待人工确认",
    )


def evaluate(
    rows: list[dict[str, Any]],
    predictor: Callable[[dict[str, Any]], FeedbackClassification],
) -> dict[str, Any]:
    category_correct = 0
    subcategory_correct = 0
    severity_correct = 0
    details = []
    for row in rows:
        prediction = predictor(row)
        category_ok = prediction.category == row["expected_category"]
        subcategory_ok = prediction.subcategory == row["expected_subcategory"]
        severity_ok = prediction.severity == row["expected_severity"]
        category_correct += category_ok
        subcategory_correct += subcategory_ok
        severity_correct += severity_ok
        details.append({
            "feedback_id": row["feedback_id"],
            "predicted_category": prediction.category,
            "expected_category": row["expected_category"],
            "category_correct": category_ok,
            "predicted_subcategory": prediction.subcategory,
            "expected_subcategory": row["expected_subcategory"],
            "subcategory_correct": subcategory_ok,
            "predicted_severity": prediction.severity,
            "expected_severity": row["expected_severity"],
            "severity_correct": severity_ok,
        })
    total = len(rows)
    return {
        "total": total,
        "category_accuracy": round(category_correct / total, 4) if total else 0,
        "subcategory_accuracy": round(subcategory_correct / total, 4) if total else 0,
        "severity_accuracy": round(severity_correct / total, 4) if total else 0,
        "details": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["keyword", "llm"], default="keyword")
    parser.add_argument("--benchmark", default=str(ROOT / "data" / "benchmark_50.csv"))
    parser.add_argument("--details", default="")
    args = parser.parse_args()
    rows = load_feedback_file(args.benchmark)
    if args.mode == "keyword":
        predictor = keyword_predict
    else:
        model = build_chat_model()
        predictor = lambda row: classify_one(row, model).output
    report = evaluate(rows, predictor)
    if args.details:
        Path(args.details).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "details"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
