from __future__ import annotations

import unittest

from app.repositories.sqlite import SQLiteRepository
from app.services.report import generate_weekly_report


class ReportTests(unittest.TestCase):
    def test_weekly_report_counts_period_and_repeats(self) -> None:
        repository = SQLiteRepository(":memory:")
        repository.upsert_run({"run_id": "r", "source_type": "batch", "source_ref": "", "status": "completed", "counters": {"input_count": 2, "classified_count": 2}, "errors": [], "review_ids": []})
        records = [
            {"feedback_id": "1", "run_id": "r", "text": "粘锅", "sanitized_text": "粘锅", "sku": "锅A", "channel": "评论", "created_at": "2026-09-14 10:00:00", "order_id": ""},
            {"feedback_id": "2", "run_id": "r", "text": "还是粘锅", "sanitized_text": "还是粘锅", "sku": "锅A", "channel": "评论", "created_at": "2026-09-14 11:00:00", "order_id": ""},
        ]
        classifications = [
            {"feedback_id": "1", "category": "产品质量", "subcategory": "涂层", "sentiment": "负面", "severity": "高", "evidence": "粘锅", "confidence": 0.9, "needs_human_review": False},
            {"feedback_id": "2", "category": "产品质量", "subcategory": "涂层", "sentiment": "负面", "severity": "高", "evidence": "粘锅", "confidence": 0.9, "needs_human_review": False},
        ]
        repository.upsert_feedback(records, classifications)
        report = generate_weekly_report(repository, end_date="2026-09-14")
        self.assertEqual(report["total_feedback"], 2)
        self.assertEqual(report["repeated_complaint_rate"], 1.0)
        self.assertEqual(report["category_distribution"][0]["count"], 2)
        repository.close()


if __name__ == "__main__":
    unittest.main()
