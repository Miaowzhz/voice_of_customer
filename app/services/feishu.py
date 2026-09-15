"""Feishu message payloads and an injectable notification adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


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
