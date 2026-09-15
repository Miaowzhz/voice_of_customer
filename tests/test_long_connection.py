from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest

from app.feishu_long_connection import LongConnectionBridge, build_channel, message_to_event


class FakeChannel:
    def __init__(self, **options):
        self.options = options
        self.handlers = {}
        self.downloads = []

    def on(self, event_name, handler):
        self.handlers[event_name] = handler

    async def connect(self):
        return None

    async def download_resource_to_file(self, file_key, **kwargs):
        self.downloads.append((file_key, kwargs))
        target = Path(kwargs["dest_dir"]) / (kwargs.get("file_name") or file_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("mock", encoding="utf-8")
        return target.resolve()


class FakeRunService:
    def __init__(self):
        self.events = []

    def submit_event(self, event):
        self.events.append(event)
        return "run-1"


class LongConnectionTests(unittest.TestCase):
    def test_group_json_ignores_leading_bot_mention(self):
        message = SimpleNamespace(
            content_text='@反馈机器人 {"feedbacks":[{"text":"粘锅"}]}',
            mentions=[SimpleNamespace(name="反馈机器人", key="@_user_1")],
        )
        self.assertTrue(message_to_event(message)["text"].startswith('{"feedbacks"'))

    def test_message_to_event_maps_text_message(self):
        message = SimpleNamespace(
            raw={"header": {"event_id": "evt-1"}}, message_id="om-1", id="om-1",
            raw_content_type="text", content=SimpleNamespace(kind="text", file_key=""),
            content_text="锅底粘锅", chat_id="oc-1", sender_id="ou-1", resources=[],
        )
        event = message_to_event(message)
        self.assertEqual(event["event_id"], "evt-1")
        self.assertEqual(event["text"], "锅底粘锅")
        self.assertEqual(event["chat_id"], "oc-1")

    def test_file_message_downloads_before_submit(self):
        channel = FakeChannel()
        service = FakeRunService()
        bridge = LongConnectionBridge(channel, service, download_dir=Path("artifacts/test_long_connection"))
        message = SimpleNamespace(
            raw={"header": {"event_id": "evt-file"}}, message_id="om-file", id="om-file",
            raw_content_type="file", content=SimpleNamespace(kind="file", file_key="file-1", file_name="feedback.csv"),
            content_text="", chat_id="oc-1", sender_id="ou-1", resources=[],
        )
        asyncio.run(bridge.handle_message(message))
        self.assertEqual(service.events[0]["local_file_path"].endswith("feedback.csv"), True)
        self.assertEqual(channel.downloads[0][0], "file-1")

    def test_build_channel_uses_websocket_transport(self):
        service = FakeRunService()
        bridge = build_channel(
            service,
            app_id="cli-test",
            app_secret="secret",
            verification_token="token",
            channel_factory=FakeChannel,
        )
        self.assertEqual(bridge.channel.options["transport"], "ws")
        self.assertEqual(bridge.channel.options["verification_token"], "token")
        bridge.register()
        self.assertIn("message", bridge.channel.handlers)


if __name__ == "__main__":
    unittest.main()
