from __future__ import annotations

import unittest

from app.graph.builder import build_graph
from app.graph.state import initial_state
from app.models.classification import FeedbackClassification
from langgraph.types import Command


def fake_classifier(record: dict) -> FeedbackClassification:
    return FeedbackClassification(
        category="产品质量",
        subcategory="涂层",
        sentiment="负面",
        severity="中",
        is_actionable=True,
        sku=record.get("sku", ""),
        evidence=record["text"][:30],
        suggested_owner="品控",
        suggested_action="核查同批次投诉",
        confidence=0.91,
        needs_human_review=False,
    )


def review_classifier(record: dict) -> FeedbackClassification:
    result = fake_classifier(record)
    return result.model_copy(update={"confidence": 0.6})


class GraphTests(unittest.TestCase):
    def test_graph_runs_from_cleaning_to_aggregate(self) -> None:
        graph = build_graph(classifier=fake_classifier)
        state = initial_state("run-test", [{
            "feedback_id": "FB1",
            "text": "锅底有点粘",
            "sku": "双枪-炒锅-28cm",
            "channel": "电商评论",
            "created_at": "2026-09-15 10:00:00",
        }])
        result = graph.invoke(state, {"configurable": {"thread_id": "run-test"}})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["counters"]["classified_count"], 1)
        self.assertEqual(result["aggregates"]["total_valid"], 1)
        self.assertEqual(result["aggregates"]["category_distribution"][0]["category"], "产品质量")

    def test_missing_classifier_fails_explicitly(self) -> None:
        graph = build_graph()
        result = graph.invoke(initial_state("run-fail", []), {"configurable": {"thread_id": "run-fail"}})
        self.assertEqual(result["status"], "failed")
        self.assertTrue(any("未注入 classifier" in error.get("error", "") for error in result["errors"]))

    def test_review_interrupt_resumes_from_checkpoint(self) -> None:
        graph = build_graph(classifier=review_classifier)
        config = {"configurable": {"thread_id": "run-review"}}
        state = initial_state("run-review", [{
            "feedback_id": "FB2",
            "text": "锅底有点粘",
            "sku": "双枪-炒锅-28cm",
            "channel": "电商评论",
            "created_at": "2026-09-15 10:00:00",
        }])
        paused = graph.invoke(state, config)
        self.assertTrue(paused["__interrupt__"])
        resumed = graph.invoke(Command(resume={"FB2": {"category": "产品质量"}}), config)
        self.assertEqual(resumed["status"], "completed")


if __name__ == "__main__":
    unittest.main()
