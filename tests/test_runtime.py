from __future__ import annotations

import unittest

from app.api.feishu_webhook import FeishuEventHandler, create_app
from app.graph.builder import build_graph
from app.models.classification import FeedbackClassification
from app.repositories.sqlite import SQLiteRepository
from app.runtime import RunService
from app.services.feishu import RecordingNotifier
from fastapi.testclient import TestClient


def classifier(record: dict) -> FeedbackClassification:
    return FeedbackClassification(
        category="产品质量", subcategory="涂层", sentiment="负面", severity="中",
        is_actionable=True, sku=record.get("sku", ""), evidence=record["text"],
        suggested_owner="品控", suggested_action="核查同批次", confidence=0.91,
        needs_human_review=False,
    )


def review_classifier(record: dict) -> FeedbackClassification:
    return classifier(record).model_copy(update={"confidence": 0.6})


def payload(text: str) -> dict:
    return {
        "header": {"event_id": "evt-runtime", "event_type": "im.message.receive_v1"},
        "event": {"message": {
            "message_id": "om-runtime", "message_type": "text", "chat_id": "oc-runtime",
            "content": '{"text": "' + text + '"}',
        }},
    }


class RuntimeTests(unittest.TestCase):
    def test_webhook_runs_graph_and_persists_results(self) -> None:
        repository = SQLiteRepository(":memory:")
        service = RunService(repository=repository, graph_factory=lambda: build_graph(classifier=classifier))
        handler = FeishuEventHandler()
        client = TestClient(create_app(handler=handler, repository=repository, run_service=service))
        response = client.post("/webhooks/feishu", json=payload("反馈 SKU=锅A 内容=锅底有点粘"))
        self.assertEqual(response.status_code, 200)
        run_id = next(iter(service.futures))
        result = service.wait(run_id, timeout=3)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(repository.fetch_run(run_id)["status"], "completed")
        self.assertEqual(repository.connection.execute("SELECT COUNT(*) FROM issues").fetchone()[0], 1)
        service.close()
        repository.close()

    def test_completion_is_sent_to_original_chat(self) -> None:
        repository = SQLiteRepository(":memory:")

        class FakeFeishu:
            def __init__(self):
                self.messages = []

            def send_text(self, receive_id, text, *, receive_id_type):
                self.messages.append((receive_id, text, receive_id_type))

        feishu = FakeFeishu()
        service = RunService(
            repository=repository,
            graph_factory=lambda: build_graph(classifier=classifier),
            feishu_client=feishu,
        )
        event = {
            "event_id": "evt-notify", "message_id": "om-notify", "chat_id": "oc-notify",
            "text": "反馈 SKU=锅A 内容=锅底有点粘",
        }
        run_id = service.submit_event(event)
        result = service.wait(run_id, timeout=3)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(feishu.messages), 2)
        self.assertEqual(feishu.messages[-1][0], "oc-notify")
        self.assertEqual(feishu.messages[-1][2], "chat_id")
        service.close()
        repository.close()

    def test_file_event_uses_downloader(self) -> None:
        repository = SQLiteRepository(":memory:")

        def downloader(event: dict, target):
            source = "feedback_id,text,sku,channel,created_at\nFB1,锅底粘锅,锅A,电商评论,2026-09-15 10:00:00\n"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source, encoding="utf-8")
            return target

        service = RunService(repository=repository, graph_factory=lambda: build_graph(classifier=classifier), file_downloader=downloader)
        run_id = service.submit_event({"event_id": "evt-file", "message_id": "om-file", "file_key": "file-1", "file_name": "mock.csv"})
        result = service.wait(run_id, timeout=3)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["counters"]["input_count"], 1)
        service.close()
        repository.close()

    def test_interrupt_is_persisted_and_notified_as_waiting_review(self) -> None:
        repository = SQLiteRepository(":memory:")

        class FakeFeishu:
            def __init__(self):
                self.messages = []

            def send_text(self, receive_id, text, *, receive_id_type):
                self.messages.append((receive_id, text, receive_id_type))

        feishu = FakeFeishu()
        service = RunService(
            repository=repository,
            graph_factory=lambda: build_graph(classifier=review_classifier),
            feishu_client=feishu,
        )
        run_id = service.submit_event({
            "event_id": "evt-review-status", "message_id": "om-review-status",
            "chat_id": "oc-review-status", "text": "反馈 SKU=锅A 内容=锅底有点粘",
        })
        result = service.wait(run_id, timeout=3)
        self.assertTrue(result["__interrupt__"])
        self.assertEqual(repository.fetch_run(run_id)["status"], "waiting_review")
        self.assertIn("待人工复核", feishu.messages[-1][1])
        service.close()
        repository.close()

    def test_session_commands_query_status_and_reset(self) -> None:
        repository = SQLiteRepository(":memory:")

        class FakeFeishu:
            def __init__(self):
                self.messages = []

            def send_text(self, receive_id, text, *, receive_id_type):
                self.messages.append((receive_id, text, receive_id_type))

        feishu = FakeFeishu()
        service = RunService(
            repository=repository,
            graph_factory=lambda: build_graph(classifier=classifier),
            feishu_client=feishu,
        )
        event = {
            "event_id": "evt-command", "message_id": "om-command", "chat_id": "oc-command",
            "text": "反馈 SKU=锅A 内容=锅底有点粘",
        }
        run_id = service.submit_event(event)
        service.wait(run_id, timeout=3)
        service.handle_command({"chat_id": "oc-command", "text": "/状态"})
        self.assertIn("运行状态：completed", feishu.messages[-1][1])
        service.handle_command({"chat_id": "oc-command", "text": "/新会话"})
        service.handle_command({"chat_id": "oc-command", "text": "/状态"})
        self.assertIn("还没有分析记录", feishu.messages[-1][1])
        service.close()
        repository.close()


if __name__ == "__main__":
    unittest.main()
