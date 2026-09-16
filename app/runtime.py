"""连接飞书事件与 LangGraph 执行过程的运行时桥接层。"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
import json
import logging
from pathlib import Path
import re
from threading import Lock
from typing import Any, Callable

from app.graph.state import initial_state
from app.services.cleaning import load_feedback_file
from app.services.inputs import bitable_source, decode_feedback_json, json_message, normalize_input_rows
from app.services.analysis import format_analysis_report
from app.services.interaction import help_text, parse_command


FileDownloader = Callable[[dict[str, Any], Path], Path]
GraphFactory = Callable[[], Any]
logger = logging.getLogger(__name__)


def _single_feedback_row(event: dict[str, Any]) -> dict[str, Any]:
    text = event.get("text", "").strip()
    sku_match = re.search(r"(?:SKU|sku)\s*[=:：]\s*([^\s]+)", text)
    order_match = re.search(r"(?:订单号|订单|order[_ -]?id)\s*[=:：]\s*([^\s]+)", text, re.I)
    content_match = re.search(r"(?:内容|问题|反馈)\s*[=:：]\s*(.+)$", text)
    message_id = event.get("message_id") or event.get("event_id") or text
    # 消息事件 ID 比文本内容稳定，适合作为单条反馈的幂等输入。
    return {
        "feedback_id": sha256(str(message_id).encode("utf-8")).hexdigest()[:24],
        "text": content_match.group(1).strip() if content_match else text,
        "sku": sku_match.group(1).strip() if sku_match else "",
        "channel": "飞书机器人",
        "created_at": event.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "order_id": order_match.group(1).strip() if order_match else "",
    }


@dataclass
class RunService:
    """异步创建并执行运行任务，同时保留可查询的任务句柄。"""

    repository: Any
    graph_factory: GraphFactory
    file_downloader: FileDownloader | None = None
    feishu_client: Any | None = None
    executor: ThreadPoolExecutor = field(default_factory=lambda: ThreadPoolExecutor(max_workers=2))
    futures: dict[str, Future] = field(default_factory=dict)

    artifact_root: Path = Path("artifacts/runs")
    _submit_lock: Lock = field(default_factory=Lock)

    def submit_event(self, event: dict[str, Any]) -> str:
        """快速受理消息，文件读取和模型调用都在后台线程执行。"""

        identity = {
            "message": event.get("message_id") or event.get("event_id"),
            "target": event.get("chat_id") or event.get("sender_id"),
        }
        if not identity["message"]:
            identity["input"] = {key: value for key, value in event.items() if key != "raw"}
        run_id = sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        state = initial_state(run_id, [], source_type="single", source_ref=event.get("message_id", ""))
        target = event.get("chat_id") or event.get("sender_id")
        if target:
            state["notification_target"] = {
                "receive_id": target,
                "receive_id_type": "chat_id" if event.get("chat_id") else "open_id",
            }
        # 同一消息事件的重复投递复用任务；重新发送文件则产生独立的分析批次。
        with self._submit_lock:
            if run_id in self.futures:
                self._remember_session(event, run_id)
                return run_id
            self.repository.upsert_run(state)
            self._remember_session(event, run_id)
            self.futures[run_id] = self.executor.submit(self._execute_event, state, dict(event))
        return run_id

    @staticmethod
    def _conversation_key(event: dict[str, Any]) -> str:
        if event.get("chat_id"):
            return f"chat:{event['chat_id']}"
        return f"user:{event.get('sender_id', '')}"

    def _remember_session(self, event: dict[str, Any], run_id: str) -> None:
        receive_id = event.get("chat_id") or event.get("sender_id")
        if not receive_id:
            return
        self.repository.save_session(
            self._conversation_key(event), receive_id,
            "chat_id" if event.get("chat_id") else "open_id", run_id,
        )

    def handle_command(self, event: dict[str, Any]) -> None:
        """处理 Hermes 风格的会话命令，命令本身不进入分析图。"""

        command = parse_command(event.get("text", ""))
        if command is None:
            return
        target = event.get("chat_id") or event.get("sender_id")
        if not target:
            return
        receive_id_type = "chat_id" if event.get("chat_id") else "open_id"
        if command.name == "help":
            self._send_command_text(target, receive_id_type, help_text())
            return
        if command.name == "new_session":
            self.repository.clear_session(self._conversation_key(event))
            self._send_command_text(target, receive_id_type, "✅ 已新建会话\n请发送新的产品反馈文件或多维表格链接。")
            return
        run_id = command.argument or self._session_run_id(event)
        if not run_id:
            self._send_command_text(target, receive_id_type, "当前会话还没有分析记录，请先发送产品反馈数据。\n\n" + help_text())
            return
        if command.name == "status":
            self._send_status(target, receive_id_type, run_id)
        elif command.name == "report":
            self._send_saved_report(target, receive_id_type, run_id)

    def _session_run_id(self, event: dict[str, Any]) -> str:
        session = self.repository.fetch_session(self._conversation_key(event))
        return str(session["current_run_id"]) if session else ""

    def _send_command_text(self, receive_id: str, receive_id_type: str, text: str) -> None:
        if self.feishu_client is None:
            return
        try:
            self.feishu_client.send_text(receive_id, text, receive_id_type=receive_id_type)
        except Exception:
            logger.exception("飞书命令响应发送失败")

    def _send_status(self, receive_id: str, receive_id_type: str, run_id: str) -> None:
        row = self.repository.fetch_run(run_id)
        if row is None:
            self._send_command_text(receive_id, receive_id_type, f"未找到运行记录：{run_id}")
            return
        self._send_command_text(receive_id, receive_id_type, (
            f"运行状态：{row['status']}\n"
            f"反馈：{row['input_count']} 条，分类成功：{row['success_count']} 条\n"
            f"待复核：{row['review_count']} 条，失败：{row['failure_count']} 条\n"
            f"run_id：{run_id}"
        ))

    def _send_saved_report(self, receive_id: str, receive_id_type: str, run_id: str) -> None:
        row = self.repository.fetch_report(run_id)
        if row is None:
            status = self.repository.fetch_run(run_id)
            message = "该运行尚未生成报告，请稍后再试。" if status else f"未找到运行记录：{run_id}"
            self._send_command_text(receive_id, receive_id_type, message)
            return
        report = json.loads(row["report_json"])
        text_sent = False
        try:
            self.feishu_client.send_text(receive_id, format_analysis_report(report), receive_id_type=receive_id_type)
            text_sent = True
            for image_ref in (row["chart_ref"], row["wordcloud_ref"]):
                if image_ref and Path(image_ref).is_file():
                    image_key = self.feishu_client.upload_image(image_ref)
                    self.feishu_client.send_image(receive_id, image_key, receive_id_type=receive_id_type)
            self.repository.set_report_delivery(run_id, "resent", "")
        except Exception as exc:
            logger.exception("飞书历史报告发送失败 run_id=%s", run_id)
            self.repository.set_report_delivery(run_id, "partial" if text_sent else "failed", str(exc))
            self._send_command_text(receive_id, receive_id_type, f"报告已保存，但图片发送失败。\nrun_id：{run_id}")

    def _load_event_rows(self, event: dict[str, Any], run_id: str) -> tuple[list[dict[str, Any]], str, str]:
        if event.get("import_error"):
            raise ValueError(event["import_error"])
        if event.get("file_key"):
            if event.get("local_file_path"):
                input_path = Path(event["local_file_path"])
            else:
                if self.file_downloader is None:
                    raise RuntimeError("批量文件事件需要配置飞书文件下载服务")
                # 使用批次目录和文件名末段，避免同名上传相互覆盖或写出接收目录。
                name = Path(event.get("file_name") or "feedback.bin").name
                target = Path("artifacts/incoming") / run_id / name
                input_path = self.file_downloader(event, target)
            return load_feedback_file(input_path, single_product=True), "batch", str(input_path)
        text = event.get("text", "").strip()
        json_text = json_message(text)
        if json_text is not None:
            rows, product = decode_feedback_json(json_text)
            return normalize_input_rows(rows, product=product, channel="飞书 JSON"), "batch", event.get("message_id", "")
        source = bitable_source(text)
        if source:
            if self.feishu_client is None:
                raise RuntimeError("读取多维表需要配置飞书应用凭据")
            rows = self.feishu_client.read_bitable_records(source)
            product_match = re.search(r"(?:SKU|产品)\s*[=:：]\s*([^\s]+)", text, re.I)
            product = product_match[1] if product_match else ""
            return normalize_input_rows(rows, product=product, channel="飞书多维表"), "batch", source["url"]
        return [_single_feedback_row(event)], "single", event.get("message_id", "")

    def _execute_event(self, state: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
        try:
            rows, source_type, source_ref = self._load_event_rows(event, state["run_id"])
            state.update(input_rows=rows, source_type=source_type, source_ref=source_ref, status="running")
            if source_type == "batch":
                # 源表记录编号可能在不同文件中重复，使用批次前缀隔离持久化明细。
                for row in rows:
                    if row.get("feedback_id"):
                        row["feedback_id"] = f"{state['run_id'][:16]}:{row['feedback_id']}"
                state["review_policy"] = "report"
                state["artifact_dir"] = str(self.artifact_root / state["run_id"])
                state["counters"] = {"input_count": len(rows)}
                self.repository.upsert_run(state)
                product = next((str(row.get("sku", "")).strip() for row in rows if row.get("sku")), "未标注产品")
                self._send_text(state, (
                    "✅ 已接收产品反馈\n"
                    f"产品：{product}\n"
                    f"反馈数量：{len(rows)} 条\n"
                    "处理状态：分析中\n\n"
                    "正在完成数据清洗、好中差等级评价、分级原因分析、等级饼图和好评词云生成。\n"
                    "分析完成后，我会在本会话发送结果，请稍候。\n可发送 /状态 或 /报告 查看进度和结果。\n\n"
                    f"运行编号：{state['run_id']}"
                ))
            return self._execute(state)
        except Exception as exc:
            # 导入和图执行异常必须落盘并通知，不能只留在线程 Future 中。
            logger.exception("反馈处理失败 run_id=%s", state["run_id"])
            state.update(status="failed", errors=[{"node": "import_or_run", "error": str(exc)}])
            self.repository.upsert_run(state)
            self._notify_feishu(state)
            return state

    def _execute(self, state: dict[str, Any]) -> dict[str, Any]:
        graph = self.graph_factory()
        result = graph.invoke(state, {"configurable": {"thread_id": state["run_id"]}})
        # LangGraph 在人工复核节点会返回 __interrupt__，此时流程已经暂停，
        # 不是仍在后台执行。补写明确状态，避免飞书和查询接口把它显示为 running。
        if result.get("__interrupt__"):
            result = {
                **result,
                "status": "waiting_review",
                "counters": {
                    **result.get("counters", {}),
                    "review_count": len(result.get("review_ids", [])),
                },
            }
        # 即使注入的图没有仓储，也由运行服务补写最终结果，保证不同运行
        # 方式都能查询到一致的运行、反馈和问题单状态。
        self.repository.upsert_run(result)
        self.repository.upsert_feedback(result.get("records", []), result.get("classifications", []))
        self.repository.upsert_issues(result.get("issue_candidates", []), result["run_id"])
        if result.get("analysis_report"):
            self.repository.save_report(result)
        self._notify_feishu(result)
        return result

    def _send_text(self, state: dict[str, Any], text: str) -> bool:
        target = state.get("notification_target", {})
        if self.feishu_client is None or not target.get("receive_id"):
            return False
        try:
            self.feishu_client.send_text(target["receive_id"], text, receive_id_type=target.get("receive_id_type", "open_id"))
            return True
        except Exception:
            logger.exception("飞书文字发送失败 run_id=%s", state["run_id"])
            return False

    def _notify_feishu(self, result: dict[str, Any]) -> None:
        target = result.get("notification_target", {})
        if self.feishu_client is None or not target.get("receive_id"):
            return
        if result.get("analysis_report"):
            text_sent = self._send_text(result, result["analysis_text"])
            try:
                for image_ref in (result["grade_pie_ref"], result["good_wordcloud_ref"]):
                    image_key = self.feishu_client.upload_image(image_ref)
                    self.feishu_client.send_image(target["receive_id"], image_key, receive_id_type=target.get("receive_id_type", "open_id"))
                self.repository.set_report_delivery(result["run_id"], "sent" if text_sent else "partial", "" if text_sent else "摘要发送失败")
            except Exception as exc:
                # 分析成功和消息投递失败分开记录，上传图片失败不覆盖分析结果。
                logger.exception("飞书饼图发送失败 run_id=%s", result["run_id"])
                self.repository.set_report_delivery(result["run_id"], "partial" if text_sent else "failed", str(exc))
                self._send_text(result, f"分析已保存，但饼图发送失败，请检查应用的图片上传和发送消息权限。\nrun_id：{result['run_id']}")
            return
        counters = result.get("counters", {})
        status = result.get("status", "unknown")
        if status == "completed":
            text = (
                f"VOC 分析完成\nrun_id：{result.get('run_id', '')}\n"
                f"反馈：{counters.get('input_count', 0)} 条，分类成功：{counters.get('classified_count', 0)} 条\n"
                f"待复核：{counters.get('review_count', 0)} 条，Issue：{len(result.get('issue_candidates', []))} 个"
            )
        elif status == "failed":
            errors = result.get("errors", [])
            is_import_error = errors and errors[0].get("node") == "import_or_run"
            reason = str(errors[0]["error"])[:250] if is_import_error else "请查看运行日志中的分类错误。"
            text = f"VOC 分析失败\nrun_id：{result.get('run_id', '')}\n{reason}"
        elif status == "waiting_review":
            text = (
                f"VOC 分析待人工复核\nrun_id：{result.get('run_id', '')}\n"
                f"待复核：{counters.get('review_count', 0)} 条\n运行已暂停，已记录待复核项。"
            )
        else:
            text = f"VOC 分析状态：{status}\nrun_id：{result.get('run_id', '')}"
        self._send_text(result, text)

    def wait(self, run_id: str, timeout: float | None = None) -> dict[str, Any]:
        future = self.futures[run_id]
        return future.result(timeout=timeout)

    def close(self) -> None:
        self.executor.shutdown(wait=True)
