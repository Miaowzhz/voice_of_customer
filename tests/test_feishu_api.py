from __future__ import annotations

import unittest

from fastapi.testclient import TestClient
import httpx

from app.api.feishu_webhook import EventDeduper, FeishuEventHandler, create_app
from app.repositories.sqlite import SQLiteRepository
from app.services.feishu import FeishuClient


def text_payload(event_id: str, text: str) -> dict:
    return {
        "header": {"event_id": event_id, "event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_test"}},
            "message": {
                "message_id": "om_test",
                "message_type": "text",
                "chat_id": "oc_test",
                "content": '{"text": "' + text + '"}',
            },
        },
    }


class FeishuApiTests(unittest.TestCase):
    def test_challenge_is_echoed(self) -> None:
        client = TestClient(create_app())
        response = client.post("/webhooks/feishu", json={"challenge": "abc", "token": "dev"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"challenge": "abc"})

    def test_text_event_is_deduplicated(self) -> None:
        received: list[dict] = []
        handler = FeishuEventHandler(on_feedback=received.append)
        client = TestClient(create_app(handler))
        payload = text_payload("event-1", "锅底有点粘")
        self.assertEqual(client.post("/webhooks/feishu", json=payload).json()["kind"], "single_feedback")
        self.assertEqual(client.post("/webhooks/feishu", json=payload).json()["message"], "duplicate event ignored")
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["text"], "锅底有点粘")

    def test_invalid_token_is_rejected(self) -> None:
        handler = FeishuEventHandler(verification_token="secret")
        client = TestClient(create_app(handler))
        response = client.post("/webhooks/feishu", json=text_payload("event-2", "反馈"))
        self.assertEqual(response.status_code, 403)

    def test_run_status_reads_repository(self) -> None:
        repository = SQLiteRepository(":memory:")
        repository.upsert_run({
            "run_id": "run-status", "source_type": "batch", "source_ref": "mock.csv",
            "status": "completed", "counters": {"input_count": 2, "classified_count": 2},
            "errors": [], "review_ids": [],
        })
        client = TestClient(create_app(repository=repository))
        response = client.get("/runs/run-status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "completed")
        self.assertEqual(client.get("/runs/missing").status_code, 404)
        repository.close()


class FeishuClientTests(unittest.TestCase):
    def test_token_message_and_bitable_calls(self) -> None:
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            if request.url.path.endswith("/tenant_access_token/internal"):
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "tenant-token"})
            return httpx.Response(200, json={"code": 0, "data": {"record_id": "rec-1"}})

        http = httpx.Client(transport=httpx.MockTransport(handler))
        client = FeishuClient("app", "secret", base_url="https://feishu.test/open-apis", http_client=http)
        self.assertEqual(client.tenant_access_token(), "tenant-token")
        client.send_text("ou-test", "已受理")
        client.upsert_bitable_record("app-token", "table-id", {"反馈 ID": "FB1"})
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[1].url.path, "/open-apis/im/v1/messages")
        self.assertEqual(calls[2].url.path, "/open-apis/bitable/v1/apps/app-token/tables/table-id/records")
        client.close()


if __name__ == "__main__":
    unittest.main()
