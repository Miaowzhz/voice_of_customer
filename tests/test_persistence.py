from __future__ import annotations

import unittest

from app.graph.builder import build_graph
from app.graph.state import initial_state
from app.models.classification import FeedbackClassification
from app.repositories.sqlite import SQLiteRepository
from app.services.feishu import RecordingNotifier
from app.services.issues import build_issue_candidates, issue_key


def issue_classifier(record: dict) -> FeedbackClassification:
    return FeedbackClassification(
        category="产品质量",
        subcategory="涂层",
        sentiment="负面",
        severity="高",
        is_actionable=True,
        sku=record.get("sku", ""),
        evidence=record["text"][:30],
        suggested_owner="品控",
        suggested_action="核查同批次投诉并抽检库存",
        confidence=0.91,
        needs_human_review=False,
    )


class IssueTests(unittest.TestCase):
    def test_same_issue_is_grouped(self) -> None:
        records = [
            {"feedback_id": "1", "sku": "锅A", "text": "粘锅"},
            {"feedback_id": "2", "sku": "锅A", "text": "还是粘锅"},
        ]
        classifications = [
            {"feedback_id": "1", "category": "产品质量", "subcategory": "涂层", "suggested_action": "核查", "suggested_owner": "品控"},
            {"feedback_id": "2", "category": "产品质量", "subcategory": "涂层", "suggested_action": "核查", "suggested_owner": "品控"},
        ]
        issues = build_issue_candidates(records, classifications)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["feedback_count"], 2)
        self.assertEqual(issues[0]["issue_key"], issue_key("锅A", "产品质量", "涂层"))


class RepositoryTests(unittest.TestCase):
    def test_grade_is_persisted_in_feedback_detail(self) -> None:
        repo = SQLiteRepository(":memory:")
        repo.upsert_run({"run_id": "grade-run", "source_type": "batch", "status": "completed", "counters": {"input_count": 1, "classified_count": 1}, "errors": [], "review_ids": []})
        repo.upsert_feedback([{"feedback_id": "grade-1", "run_id": "grade-run", "text": "很好", "sanitized_text": "很好", "sku": "锅A", "channel": "测试", "created_at": "2026-09-15 10:00:00"}], [{"feedback_id": "grade-1", "category": "其他/待人工确认", "subcategory": "未知类别", "grade": "好", "sentiment": "正面", "severity": "低", "evidence": "很好", "confidence": 0.9, "needs_human_review": False}])
        row = repo.connection.execute("SELECT grade FROM feedback WHERE feedback_id='grade-1'").fetchone()
        self.assertEqual(row["grade"], "好")
        repo.close()

    def test_upsert_is_idempotent_and_accumulates_issue(self) -> None:
        repo = SQLiteRepository(":memory:")
        state = {
            "run_id": "run-1", "source_type": "batch", "source_ref": "mock.csv",
            "status": "completed", "counters": {"input_count": 1, "classified_count": 1},
            "errors": [], "review_ids": [],
        }
        repo.upsert_run(state)
        repo.upsert_run(state)
        record = {
            "feedback_id": "FB1", "run_id": "run-1", "text": "粘锅", "sanitized_text": "粘锅",
            "sku": "锅A", "channel": "电商评论", "created_at": "2026-09-15 10:00:00", "order_id": "",
        }
        classification = {"feedback_id": "FB1", "category": "产品质量", "subcategory": "涂层", "severity": "高"}
        issue = build_issue_candidates([record], [{**classification, "suggested_action": "核查", "suggested_owner": "品控"}])[0]
        repo.upsert_feedback([record], [classification])
        repo.upsert_issues([issue], "run-1")
        repo.upsert_issues([issue], "run-1")
        row = repo.fetch_issue(issue["issue_key"])
        self.assertIsNotNone(row)
        self.assertEqual(row["feedback_count"], 1)
        repo.upsert_issues([issue], "run-2")
        self.assertEqual(repo.fetch_issue(issue["issue_key"])["feedback_count"], 2)
        self.assertEqual(repo.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)
        self.assertEqual(repo.connection.execute("SELECT COUNT(*) FROM feedback").fetchone()[0], 1)
        repo.close()


class GraphPersistenceTests(unittest.TestCase):
    def test_graph_persists_and_notifies(self) -> None:
        repo = SQLiteRepository(":memory:")
        notifier = RecordingNotifier()
        graph = build_graph(classifier=issue_classifier, repository=repo, notifier=notifier)
        result = graph.invoke(initial_state("run-graph", [{
            "feedback_id": "FB1", "text": "锅底粘锅", "sku": "锅A",
            "channel": "电商评论", "created_at": "2026-09-15 10:00:00",
        }]), {"configurable": {"thread_id": "run-graph"}})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["counters"]["persisted"], 1)
        self.assertEqual(len(notifier.messages), 1)
        self.assertEqual(repo.connection.execute("SELECT COUNT(*) FROM issues").fetchone()[0], 1)
        repo.close()


if __name__ == "__main__":
    unittest.main()
