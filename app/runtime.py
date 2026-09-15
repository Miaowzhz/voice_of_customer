"""Runtime bridge from Feishu events to LangGraph executions."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Callable

from app.graph.builder import build_graph
from app.graph.state import initial_state
from app.services.cleaning import calculate_run_id, load_feedback_file


FileDownloader = Callable[[dict[str, Any], Path], Path]
GraphFactory = Callable[[], Any]


def _single_feedback_row(event: dict[str, Any]) -> dict[str, Any]:
    text = event.get("text", "").strip()
    sku_match = re.search(r"(?:SKU|sku)\s*[=:：]\s*([^\s]+)", text)
    order_match = re.search(r"(?:订单号|订单|order[_ -]?id)\s*[=:：]\s*([^\s]+)", text, re.I)
    content_match = re.search(r"(?:内容|问题|反馈)\s*[=:：]\s*(.+)$", text)
    message_id = event.get("message_id") or event.get("event_id") or text
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
    """Create and execute runs asynchronously while keeping a queryable future."""

    repository: Any
    graph_factory: GraphFactory
    file_downloader: FileDownloader | None = None
    executor: ThreadPoolExecutor = field(default_factory=lambda: ThreadPoolExecutor(max_workers=2))
    futures: dict[str, Future] = field(default_factory=dict)

    def submit_event(self, event: dict[str, Any]) -> str:
        """Create a run from a normalized Feishu event and submit it in background."""

        if event.get("file_key"):
            if self.file_downloader is None:
                raise RuntimeError("批量文件事件需要配置 file_downloader")
            target = Path("artifacts/incoming") / (event.get("file_name") or f"{event['file_key']}.bin")
            input_path = self.file_downloader(event, target)
            run_id = calculate_run_id(input_path)
            rows = load_feedback_file(input_path)
            source_type = "batch"
            source_ref = str(input_path)
        else:
            row = _single_feedback_row(event)
            canonical = json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8")
            run_id = sha256(canonical).hexdigest()
            rows = [row]
            source_type = "single"
            source_ref = event.get("message_id", "")

        state = initial_state(run_id, rows, source_type=source_type, source_ref=source_ref)
        self.repository.upsert_run(state)
        self.futures[run_id] = self.executor.submit(self._execute, state)
        return run_id

    def _execute(self, state: dict[str, Any]) -> dict[str, Any]:
        graph = self.graph_factory()
        result = graph.invoke(state, {"configurable": {"thread_id": state["run_id"]}})
        # Keep the runtime authoritative even when the injected graph was
        # built without a repository (useful for tests and alternate runners).
        self.repository.upsert_run(result)
        self.repository.upsert_feedback(result.get("records", []), result.get("classifications", []))
        self.repository.upsert_issues(result.get("issue_candidates", []), result["run_id"])
        return result

    def wait(self, run_id: str, timeout: float | None = None) -> dict[str, Any]:
        future = self.futures[run_id]
        return future.result(timeout=timeout)

    def close(self) -> None:
        self.executor.shutdown(wait=True)
