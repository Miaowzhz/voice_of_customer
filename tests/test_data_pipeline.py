from __future__ import annotations

import unittest

from app.services.aggregate import aggregate_feedback
from app.services.cleaning import clean_records, mask_sensitive, normalize_sku


class CleaningTests(unittest.TestCase):
    def test_normalization_and_duplicate_handling(self) -> None:
        result = clean_records([
            {
                "feedback_id": "FB1",
                "text": " 联系电话 13812345678，锅很粘。 ",
                "sku": " 双枪 / 炒锅 28cm ",
                "channel": "客服聊天",
                "created_at": "2026-09-15T01:00:00Z",
            },
            {
                "feedback_id": "FB1",
                "text": "重复反馈",
                "sku": "x",
                "channel": "客服聊天",
                "created_at": "2026-09-15 10:00:00",
            },
        ], run_id="abc123")
        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.duplicate_count, 1)
        self.assertEqual(result.records[0]["sku"], "双枪-炒锅 28cm")
        self.assertIn("[手机号]", result.records[0]["sanitized_text"])

    def test_invalid_rows_are_isolated(self) -> None:
        result = clean_records([{
            "feedback_id": "FB2",
            "text": "",
            "sku": "x",
            "channel": "客服聊天",
            "created_at": "2026-09-15",
        }], run_id="abc123")
        self.assertEqual(len(result.records), 0)
        self.assertEqual(len(result.failures), 1)


class AggregateTests(unittest.TestCase):
    def test_distribution_and_top_issue(self) -> None:
        summary = aggregate_feedback([
            {"sku": "锅A", "category": "产品质量", "subcategory": "涂层", "text": "a"},
            {"sku": "锅A", "category": "产品质量", "subcategory": "涂层", "text": "b"},
            {"sku": "锅B", "category": "售后服务与退款", "subcategory": "退款进度", "text": "c"},
        ])
        self.assertEqual(summary["total_valid"], 3)
        self.assertEqual(summary["category_distribution"][0]["ratio"], 66.7)
        self.assertEqual(summary["top_issues"][0]["count"], 2)


if __name__ == "__main__":
    unittest.main()
