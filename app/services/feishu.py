"""飞书消息载荷与可注入的通知适配器。"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Any, Protocol

import httpx


def completion_card(state: dict[str, Any]) -> dict[str, Any]:
    counters = state.get("counters", {})
    return {
        "type": "template",
        "data": {
            "template_id": "voc_run_completed",
            "run_id": state.get("run_id", ""),
            "status": state.get("status", "completed"),
            "input_count": counters.get("input_count", 0),
            "classified_count": counters.get("classified_count", 0),
            "review_count": counters.get("review_count", 0),
            "issue_count": len(state.get("issue_candidates", [])),
        },
    }


class Notifier(Protocol):
    def send(self, payload: dict[str, Any]) -> None: ...


@dataclass
class RecordingNotifier:
    """用于测试和本地演示的通知器；生产环境可替换为 FeishuClient。"""

    messages: list[dict[str, Any]] = field(default_factory=list)

    def send(self, payload: dict[str, Any]) -> None:
        self.messages.append(payload)


class FeishuAPIError(RuntimeError):
    """飞书开放平台 API 调用返回非成功结果时抛出。"""


class FeishuClient:
    """支持注入 httpx 传输层的最小飞书开放平台 API 客户端。

    客户端不会把凭据放入业务载荷，并在进程生命周期内缓存租户令牌。
    测试时可以传入 ``httpx.MockTransport``。
    """

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        base_url: str = "https://open.feishu.cn/open-apis",
        http_client: httpx.Client | None = None,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.base_url = base_url.rstrip("/")
        self.http = http_client or httpx.Client(timeout=30)
        self._tenant_access_token = ""
        self._token_expires_at = 0.0
        self._token_lock = Lock()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self.http.request(method, f"{self.base_url}{path}", **kwargs)
        if response.status_code >= 400:
            raise FeishuAPIError(f"HTTP {response.status_code}: {response.text[:500]}")
        payload = response.json()
        if payload.get("code", 0) != 0:
            raise FeishuAPIError(f"Feishu code={payload.get('code')}: {payload.get('msg', '')}")
        return response

    def tenant_access_token(self, *, force_refresh: bool = False) -> str:
        # 长连接持续运行时令牌会过期；互斥刷新避免并行批次重复获取令牌。
        with self._token_lock:
            if self._tenant_access_token and monotonic() < self._token_expires_at and not force_refresh:
                return self._tenant_access_token
            response = self._request(
                "POST", "/auth/v3/tenant_access_token/internal",
                json={"app_id": self.app_id, "app_secret": self.app_secret},
            )
            payload = response.json()
            self._tenant_access_token = payload["tenant_access_token"]
            self._token_expires_at = monotonic() + max(0, payload.get("expire", 7200) - 60)
            return self._tenant_access_token

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tenant_access_token()}"}

    def send_text(self, receive_id: str, text: str, *, receive_id_type: str = "open_id") -> dict[str, Any]:
        response = self._request(
            "POST",
            "/im/v1/messages",
            params={"receive_id_type": receive_id_type},
            headers=self._auth_headers(),
            json={"receive_id": receive_id, "msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)},
        )
        return response.json().get("data", {})

    def send_card(self, receive_id: str, card: dict[str, Any], *, receive_id_type: str = "open_id") -> dict[str, Any]:
        response = self._request(
            "POST",
            "/im/v1/messages",
            params={"receive_id_type": receive_id_type},
            headers=self._auth_headers(),
            json={"receive_id": receive_id, "msg_type": "interactive", "content": json.dumps(card, ensure_ascii=False)},
        )
        return response.json().get("data", {})

    def download_message_resource(self, message_id: str, file_key: str, output_path: str | Path) -> Path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        response = self.http.get(
            f"{self.base_url}/im/v1/messages/{message_id}/resources/{file_key}",
            params={"type": "file"},
            headers=self._auth_headers(),
        )
        if response.status_code >= 400:
            raise FeishuAPIError(f"HTTP {response.status_code}: {response.text[:500]}")
        path.write_bytes(response.content)
        return path

    def upload_image(self, image_path: str | Path) -> str:
        """上传分析饼图，获得飞书消息引用的图片标识。"""

        path = Path(image_path)
        with path.open("rb") as image:
            response = self._request(
                "POST", "/im/v1/images", headers=self._auth_headers(),
                data={"image_type": "message"}, files={"image": (path.name, image, "image/png")},
            )
        return response.json()["data"]["image_key"]

    def send_image(self, receive_id: str, image_key: str, *, receive_id_type: str = "open_id") -> dict[str, Any]:
        response = self._request(
            "POST", "/im/v1/messages", params={"receive_id_type": receive_id_type},
            headers=self._auth_headers(),
            json={"receive_id": receive_id, "msg_type": "image", "content": json.dumps({"image_key": image_key})},
        )
        return response.json().get("data", {})

    def read_bitable_records(self, source: dict[str, str]) -> list[dict[str, Any]]:
        """读取指定数据表或视图的全部分页，不修改源多维表。"""

        token = source["token"]
        if source["kind"] == "wiki":
            response = self._request(
                "GET", "/wiki/v2/spaces/get_node", headers=self._auth_headers(), params={"token": token},
            )
            node = response.json()["data"]["node"]
            if node.get("obj_type") != "bitable":
                raise ValueError("该知识库链接不是多维表格，请发送多维表中的数据表链接")
            token = node["obj_token"]
        params: dict[str, Any] = {"page_size": 500}
        if source.get("view_id"):
            params["view_id"] = source["view_id"]
        rows = []
        seen_tokens: set[str] = set()
        while True:
            response = self._request(
                "GET", f"/bitable/v1/apps/{token}/tables/{source['table_id']}/records",
                headers=self._auth_headers(), params=params,
            )
            data = response.json().get("data", {})
            rows.extend(data.get("items", []))
            if not data.get("has_more"):
                return rows
            page_token = data.get("page_token")
            if not page_token or page_token in seen_tokens:
                raise FeishuAPIError("多维表分页响应异常，未返回完整数据，请稍后重试")
            seen_tokens.add(page_token)
            params["page_token"] = page_token

    def upsert_bitable_record(
        self,
        app_token: str,
        table_id: str,
        fields: dict[str, Any],
        *,
        record_id: str | None = None,
    ) -> dict[str, Any]:
        if record_id:
            method = "PUT"
            path = f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}"
        else:
            method = "POST"
            path = f"/bitable/v1/apps/{app_token}/tables/{table_id}/records"
        response = self._request(method, path, headers=self._auth_headers(), json={"fields": fields})
        return response.json().get("data", {})

    def close(self) -> None:
        self.http.close()
