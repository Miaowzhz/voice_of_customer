from __future__ import annotations

import unittest

from app.models.classification import FeedbackClassification, requires_human_review
from scripts.evaluate_classifier import evaluate, keyword_predict


class ClassificationContractTests(unittest.TestCase):
    def test_contract_rejects_unknown_category(self) -> None:
        with self.assertRaises(ValueError):
            FeedbackClassification.model_validate({
                "category": "未知分类",
                "subcategory": "信息不足",
                "sentiment": "中性",
                "severity": "低",
                "is_actionable": False,
                "evidence": "无法判断",
                "suggested_owner": "客服主管",
                "suggested_action": "人工确认",
                "confidence": 0.4,
                "needs_human_review": True,
            })

    def test_low_confidence_forces_review(self) -> None:
        result = FeedbackClassification(
            category="产品质量",
            subcategory="涂层",
            sentiment="负面",
            severity="中",
            is_actionable=True,
            evidence="锅底粘",
            suggested_owner="品控",
            suggested_action="核查",
            confidence=0.74,
            needs_human_review=False,
        )
        self.assertTrue(requires_human_review(result))


class EvaluatorTests(unittest.TestCase):
    def test_evaluator_returns_metrics(self) -> None:
        rows = [{
            "feedback_id": "1",
            "text": "优惠券不能使用",
            "sku": "锅A",
            "expected_category": "价格、优惠和活动规则",
            "expected_subcategory": "优惠券",
            "expected_severity": "中",
        }]
        report = evaluate(rows, keyword_predict)
        self.assertEqual(report["total"], 1)
        self.assertEqual(report["category_accuracy"], 1.0)


if __name__ == "__main__":
    unittest.main()
