"""飞书机器人事件解析与 FastAPI 回调应用。"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from app.services.inputs import strip_leading_mentions
from app.services.interaction import parse_command


@dataclass
class EventDeduper:
    """在单进程内确保每个飞书事件只处理一次；生产环境应持久化该键。"""

    seen: set[str] = field(default_factory=set)

    def first_seen(self, event_id: str) -> bool:
        # 飞书可能重复投递事件；先挡住同一进程内的重复任务，生产环境应把
        # 将事件编号写入共享存储，避免多实例各自重复消费。
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
            # 某些消息类型的内容字段不是 JSON，保留为纯文本，避免丢失反馈。
            return {"text": content}
    return content if isinstance(content, dict) else {}


def parse_event(payload: dict[str, Any]) -> dict[str, Any]:
    """将飞书回调的不同格式归一化为内部事件契约。"""

    header = payload.get("header", {})
    event = payload.get("event", {})
    message = event.get("message", {})
    message_type = message.get("message_type", "")
    content = _message_content(event)
    text = str(content.get("text", "") or "")
    if message_type == "post":
        # 富文本消息可能把多维表链接和 JSON 代码块放在不同段落中。
        post = content if "content" in content else content.get("zh_cn", next(iter(content.values()), {}))
        if isinstance(post, dict):
            lines = []
            for paragraph in post.get("content", []):
                lines.append("".join(
                    str(item.get("href", "")) if item.get("tag") == "a" else str(item.get("text", ""))
                    for item in paragraph if item.get("tag") != "at"
                ))
            text = "\n".join(lines)
    sender = event.get("sender", {}).get("sender_id", {})
    return {
        "event_id": header.get("event_id") or payload.get("event_id", ""),
        "event_type": header.get("event_type", ""),
        "message_type": message_type,
        "message_id": message.get("message_id", ""),
        "chat_id": message.get("chat_id", ""),
        "sender_id": sender.get("open_id") or sender.get("user_id") or "",
        "text": strip_leading_mentions(text, message.get("mentions", [])),
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
            # 网址验证必须原样回显挑战值，不能进入业务处理流程。
            return {"challenge": payload["challenge"]}
        event = parse_event(payload)
        if not self.deduper.first_seen(event["event_id"]):
            return {"code": 0, "message": "duplicate event ignored"}
        if event["message_type"] in {"text", "post"} and event["text"]:
            if parse_command(event["text"]):
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


def create_app(
    handler: FeishuEventHandler | None = None,
    repository: Any | None = None,
    run_service: Any | None = None,
) -> FastAPI:
    app = FastAPI(title="VOC Agent API", version="0.1.0")
    event_handler = handler or FeishuEventHandler(
        verification_token=os.getenv("FEISHU_VERIFICATION_TOKEN", "")
    )
    if run_service is not None:
        if event_handler.on_feedback is None:
            event_handler.on_feedback = run_service.submit_event
        if event_handler.on_command is None and hasattr(run_service, "handle_command"):
            event_handler.on_command = run_service.handle_command

    @app.post("/webhooks/feishu")
    async def feishu_webhook(
        payload: dict[str, Any],
        x_feishu_verification_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        try:
            # 回调只负责受理；耗时的文件下载和 LangGraph 执行由运行服务后台处理。
            return event_handler.handle(payload, x_feishu_verification_token)
        except RuntimeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/runs/{run_id}")
    async def run_status(run_id: str) -> dict[str, Any]:
        if repository is None:
            raise HTTPException(status_code=404, detail="run repository is not configured")
        row = repository.fetch_run(run_id)
        if row is None:
            raise HTTPException(status_code=404, detail="run not found")
        return dict(row)

    @app.get("/runs/{run_id}/report")
    async def analysis_report(run_id: str) -> dict[str, Any]:
        row = repository.fetch_report(run_id) if repository is not None else None
        if row is None:
            raise HTTPException(status_code=404, detail="该批次暂无分析报告")
        return {"report": json.loads(row["report_json"]), "delivery_status": row["delivery_status"]}

    @app.get("/runs/{run_id}/chart")
    async def analysis_chart(run_id: str) -> FileResponse:
        row = repository.fetch_report(run_id) if repository is not None else None
        if row is None or not Path(row["chart_ref"]).is_file():
            raise HTTPException(status_code=404, detail="该批次暂无饼图")
        return FileResponse(row["chart_ref"], media_type="image/png")

    return app
