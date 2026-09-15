"""Feishu bot event parsing and FastAPI webhook application."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from typing import Any, Callable

from fastapi import FastAPI, Header, HTTPException


@dataclass
class EventDeduper:
    """Process each Feishu event once per process; persist this key in production."""

    seen: set[str] = field(default_factory=set)

    def first_seen(self, event_id: str) -> bool:
        if not event_id:
            return True
        if event_id in self.seen:
            return False
        self.seen.add(event_id)
        return True


def _message_content(event: dict[str, Any]) -> dict[str, Any]:
    message = event.get("message", {})
    content = message.get("content", {})
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"text": content}
    return content if isinstance(content, dict) else {}


def parse_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize Feishu callback variants into a small internal event contract."""

    header = payload.get("header", {})
    event = payload.get("event", {})
    message = event.get("message", {})
    message_type = message.get("message_type", "")
    content = _message_content(event)
    sender = event.get("sender", {}).get("sender_id", {})
    return {
        "event_id": header.get("event_id") or payload.get("event_id", ""),
        "event_type": header.get("event_type", ""),
        "message_type": message_type,
        "message_id": message.get("message_id", ""),
        "chat_id": message.get("chat_id", ""),
        "sender_id": sender.get("open_id") or sender.get("user_id") or "",
        "text": str(content.get("text", "") or "").strip(),
        "file_key": content.get("file_key") or content.get("file_token") or "",
        "file_name": content.get("file_name") or "",
        "raw": payload,
    }


@dataclass
class FeishuEventHandler:
    verification_token: str = ""
    deduper: EventDeduper = field(default_factory=EventDeduper)
    on_feedback: Callable[[dict[str, Any]], None] | None = None
    on_command: Callable[[dict[str, Any]], None] | None = None

    def validate(self, payload: dict[str, Any], header_token: str | None = None) -> None:
        expected = self.verification_token
        if expected and payload.get("token") != expected and header_token != expected:
            raise HTTPException(status_code=403, detail="invalid verification token")

    def handle(self, payload: dict[str, Any], header_token: str | None = None) -> dict[str, Any]:
        self.validate(payload, header_token)
        if "challenge" in payload:
            return {"challenge": payload["challenge"]}
        event = parse_event(payload)
        if not self.deduper.first_seen(event["event_id"]):
            return {"code": 0, "message": "duplicate event ignored"}
        if event["message_type"] in {"text", "post"} and event["text"]:
            if event["text"].startswith(("查询", "复核", "生成周报")):
                if self.on_command:
                    self.on_command(event)
                return {"code": 0, "accepted": True, "kind": "command"}
            if self.on_feedback:
                self.on_feedback(event)
            return {"code": 0, "accepted": True, "kind": "single_feedback"}
        if event["file_key"]:
            if self.on_feedback:
                self.on_feedback(event)
            return {"code": 0, "accepted": True, "kind": "batch_file", "file_key": event["file_key"]}
        return {"code": 0, "accepted": False, "reason": "unsupported message"}


def create_app(handler: FeishuEventHandler | None = None) -> FastAPI:
    app = FastAPI(title="VOC Agent API", version="0.1.0")
    event_handler = handler or FeishuEventHandler(
        verification_token=os.getenv("FEISHU_VERIFICATION_TOKEN", "")
    )

    @app.post("/webhooks/feishu")
    async def feishu_webhook(
        payload: dict[str, Any],
        x_feishu_verification_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        return event_handler.handle(payload, x_feishu_verification_token)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
