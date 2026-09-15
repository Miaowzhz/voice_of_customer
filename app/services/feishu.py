"""Feishu message payloads and an injectable notification adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
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
    """Test and local-demo notifier; production can replace it with FeishuClient."""

    messages: list[dict[str, Any]] = field(default_factory=list)

    def send(self, payload: dict[str, Any]) -> None:
        self.messages.append(payload)


class FeishuAPIError(RuntimeError):
    """Raised when a Feishu Open API call returns a non-success response."""


class FeishuClient:
    """Minimal Feishu Open API client with injectable httpx transport.

    The client keeps credentials out of payloads and caches the tenant token
    for the lifetime of the process. Tests can pass ``httpx.MockTransport``.
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

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self.http.request(method, f"{self.base_url}{path}", **kwargs)
        if response.status_code >= 400:
            raise FeishuAPIError(f"HTTP {response.status_code}: {response.text[:500]}")
        payload = response.json()
        if payload.get("code", 0) != 0:
            raise FeishuAPIError(f"Feishu code={payload.get('code')}: {payload.get('msg', '')}")
        return response

    def tenant_access_token(self, *, force_refresh: bool = False) -> str:
        if self._tenant_access_token and not force_refresh:
            return self._tenant_access_token
        response = self._request(
            "POST",
            "/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
        )
        self._tenant_access_token = response.json()["tenant_access_token"]
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
