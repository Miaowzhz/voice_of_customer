from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.api.feishu_webhook import EventDeduper, FeishuEventHandler, create_app


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


if __name__ == "__main__":
    unittest.main()
