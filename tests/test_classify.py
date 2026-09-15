from __future__ import annotations

import unittest
from unittest.mock import patch

from langchain_core.runnables import RunnableLambda

from app.models.classification import FeedbackClassification, requires_human_review
from app.services.classify import _structured_chain, classify_one
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


class StructuredOutputTests(unittest.TestCase):
    def test_uses_configured_json_mode(self) -> None:
        class CaptureModel:
            def __init__(self):
                self.method = None

            def with_structured_output(self, schema, **kwargs):
                self.method = kwargs.get("method")
                return self

            def __call__(self, value):
                return value

        model = CaptureModel()
        with patch.dict("os.environ", {"LLM_STRUCTURED_OUTPUT_METHOD": "json_mode"}):
            _structured_chain(model, "测试提示词")
        self.assertEqual(model.method, "json_mode")

    def test_partial_model_output_falls_back_to_human_review(self) -> None:
        class PartialModel:
            def with_structured_output(self, schema, **kwargs):
                return RunnableLambda(lambda _: {
                    "category": "其他/待人工确认",
                    "subcategory": "信息不足",
                    "evidence": "你好",
                    "confidence": 0.1,
                    "needs_human_review": True,
                })

        with patch.dict("os.environ", {"LLM_STRUCTURED_OUTPUT_METHOD": "json_mode"}):
            result = classify_one(
                {"feedback_id": "hello", "text": "你好", "sanitized_text": "你好"},
                PartialModel(),
            )
        self.assertEqual(result.output.category, "其他/待人工确认")
        self.assertTrue(result.needs_human_review)


if __name__ == "__main__":
    unittest.main()
