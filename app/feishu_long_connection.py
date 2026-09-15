"""通过飞书 WebSocket 长连接接收反馈事件。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv
from lark_oapi.channel import Events, FeishuChannel

from app.runtime import RunService


load_dotenv()


def message_to_event(message: Any) -> dict[str, Any]:
    """将 SDK 标准化消息转换为项目内部事件格式。"""

    raw = getattr(message, "raw", {}) or {}
    header = raw.get("header", {}) if isinstance(raw, dict) else {}
    content = getattr(message, "content", None)
    file_key = getattr(content, "file_key", "") or ""
    file_name = getattr(content, "file_name", "") or ""
    resources = getattr(message, "resources", []) or []
    if not file_key and resources:
        resource = resources[0]
        file_key = getattr(resource, "file_key", "") or ""
        file_name = file_name or getattr(resource, "file_name", "") or ""

    return {
        "event_id": header.get("event_id") or getattr(message, "message_id", ""),
        "event_type": header.get("event_type", "im.message.receive_v1"),
        "message_type": getattr(message, "raw_content_type", "") or getattr(content, "kind", ""),
        "message_id": getattr(message, "message_id", "") or getattr(message, "id", ""),
        "chat_id": getattr(message, "chat_id", ""),
        "sender_id": getattr(message, "sender_id", ""),
        "text": str(getattr(message, "content_text", "") or "").strip(),
        "file_key": file_key,
        "file_name": file_name,
        "raw": raw,
    }


@dataclass
class LongConnectionBridge:
    """把长连接消息转发给现有运行服务，并负责异步下载附件。"""

    channel: Any
    run_service: RunService
    download_dir: Path = Path("artifacts/incoming")
    on_submit: Callable[[str], None] | None = None

    def register(self) -> None:
        """注册消息事件处理器。"""

        self.channel.on(Events.MESSAGE, self.handle_message)

    async def handle_message(self, message: Any) -> None:
        """处理一条标准化消息，下载文件后提交后台分析。"""

        event = message_to_event(message)
        if event["file_key"]:
            local_path = await self.channel.download_resource_to_file(
                event["file_key"],
                resource_type="file",
                message_id=event["message_id"],
                dest_dir=self.download_dir,
                file_name=event["file_name"] or None,
            )
            event["local_file_path"] = str(local_path)
        run_id = self.run_service.submit_event(event)
        if self.on_submit:
            self.on_submit(run_id)

    async def run(self) -> None:
        """启动并保持 WebSocket 长连接，直到进程退出。"""

        self.register()
        await self.channel.connect()


def build_channel(
    run_service: RunService,
    *,
    app_id: str,
    app_secret: str,
    verification_token: str = "",
    encrypt_key: str = "",
    channel_factory: Callable[..., Any] = FeishuChannel,
) -> LongConnectionBridge:
    """创建长连接通道并绑定项目运行服务。"""

    options: dict[str, Any] = {
        "app_id": app_id,
        "app_secret": app_secret,
        "transport": "ws",
    }
    if verification_token:
        options["verification_token"] = verification_token
    if encrypt_key:
        options["encrypt_key"] = encrypt_key
    channel = channel_factory(**options)
    return LongConnectionBridge(channel=channel, run_service=run_service)


async def _serve() -> None:
    """读取环境变量并启动长连接进程。"""

    app_id = os.getenv("FEISHU_APP_ID", "")
    app_secret = os.getenv("FEISHU_APP_SECRET", "")
    if not app_id or not app_secret:
        raise RuntimeError("长连接需要配置 FEISHU_APP_ID 和 FEISHU_APP_SECRET")

    # 复用主应用中的仓储、分类器和飞书出站通知配置。
    from app.main import run_service

    bridge = build_channel(
        run_service,
        app_id=app_id,
        app_secret=app_secret,
        verification_token=os.getenv("FEISHU_VERIFICATION_TOKEN", ""),
        encrypt_key=os.getenv("FEISHU_ENCRYPT_KEY", ""),
    )
    try:
        await bridge.run()
    finally:
        run_service.close()


def main() -> None:
    """长连接命令行入口。"""

    asyncio.run(_serve())


if __name__ == "__main__":
    main()
