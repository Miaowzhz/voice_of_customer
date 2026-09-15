"""Feedback ingestion and deterministic cleaning helpers.

The module deliberately keeps cleaning deterministic and independent from the
LLM so that the same input file always produces the same normalized records.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import re
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd


REQUIRED_COLUMNS = {"feedback_id", "text", "sku", "channel", "created_at"}
DEFAULT_TIMEZONE = "Asia/Shanghai"


@dataclass(frozen=True)
class CleaningFailure:
    """One input row that could not be normalized."""

    row_number: int
    reason: str
    feedback_id: str = ""


@dataclass(frozen=True)
class CleaningResult:
    """Output of :func:`clean_records`."""

    records: list[dict[str, Any]]
    failures: list[CleaningFailure]
    input_count: int
    duplicate_count: int


def calculate_run_id(file_path: str | Path) -> str:
    """Return the stable SHA-256 run ID for a file's bytes."""

    digest = sha256()
    with Path(file_path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_feedback_file(file_path: str | Path) -> list[dict[str, Any]]:
    """Load CSV or XLSX input and return plain dictionaries.

    CSV is read as UTF-8 with BOM support. XLSX support is delegated to
    pandas/openpyxl, which is listed in ``requirements.txt``.
    """

    path = Path(file_path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        frame = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    elif suffix in {".xlsx", ".xls"}:
        frame = pd.read_excel(path, dtype=str).fillna("")
    else:
        raise ValueError(f"不支持的文件类型: {path.suffix or '<无扩展名>'}")

    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        missing_text = ", ".join(sorted(missing))
        raise ValueError(f"缺少必填字段: {missing_text}")
    return frame.fillna("").to_dict(orient="records")


def normalize_sku(value: Any) -> str:
    """Normalize spacing and common SKU separators without changing Chinese text."""

    text = str(value or "").strip().lower()
    text = re.sub(r"[\u3000\s]+", " ", text)
    text = re.sub(r"\s*[/|_]+\s*", "-", text)
    text = re.sub(r"\s*[-－—]\s*", "-", text)
    return text.strip(" -")


def mask_sensitive(text: Any) -> str:
    """Mask common contact identifiers before text is persisted or sent to LLM."""

    value = str(text or "")
    value = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[手机号]", value)
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[邮箱]", value)
    value = re.sub(r"(?<!\w)(?:订单号|订单|order[_ -]?id)\s*[:：=]?\s*[A-Za-z0-9-]{8,}", "[订单号]", value, flags=re.I)
    return value


def _normalize_timestamp(value: Any, timezone_name: str) -> str:
    if value is None or str(value).strip() == "":
        raise ValueError("created_at 为空")
    timestamp = pd.to_datetime(value, errors="coerce")
    if pd.isna(timestamp):
        raise ValueError(f"created_at 无法解析: {value}")
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(ZoneInfo(timezone_name))
    else:
        timestamp = timestamp.tz_convert(ZoneInfo(timezone_name))
    return timestamp.strftime("%Y-%m-%d %H:%M:%S")


def clean_records(
    rows: Iterable[dict[str, Any]],
    *,
    run_id: str = "manual",
    timezone_name: str = DEFAULT_TIMEZONE,
    max_text_length: int = 4000,
) -> CleaningResult:
    """Clean, normalize and de-duplicate feedback rows.

    Invalid rows are isolated in ``failures``. Duplicate IDs keep the first
    valid occurrence, making repeated uploads safe for an Upsert repository.
    """

    source_rows = list(rows)
    records: list[dict[str, Any]] = []
    failures: list[CleaningFailure] = []
    seen_ids: set[str] = set()
    duplicate_count = 0

    for index, source in enumerate(source_rows, start=2):
        raw_id = str(source.get("feedback_id", "") or "").strip()
        feedback_id = raw_id or f"{run_id[:12]}-{index - 1:04d}"
        try:
            text = re.sub(r"\s+", " ", str(source.get("text", "") or "")).strip()
            if not text:
                raise ValueError("text 为空")
            if len(text) > max_text_length:
                raise ValueError(f"text 超过 {max_text_length} 字符")
            if feedback_id in seen_ids:
                duplicate_count += 1
                continue
            normalized = {
                "feedback_id": feedback_id,
                "text": text,
                "sanitized_text": mask_sensitive(text),
                "sku": normalize_sku(source.get("sku", "")),
                "channel": str(source.get("channel", "") or "").strip() or "未知",
                "created_at": _normalize_timestamp(source.get("created_at"), timezone_name),
                "order_id": str(source.get("order_id", "") or "").strip(),
                "source_file": str(source.get("source_file", "") or ""),
                "run_id": run_id,
            }
            seen_ids.add(feedback_id)
            records.append(normalized)
        except (TypeError, ValueError) as exc:
            failures.append(CleaningFailure(index, str(exc), feedback_id))

    return CleaningResult(records, failures, len(source_rows), duplicate_count)


def records_to_frame(records: Iterable[dict[str, Any]]) -> pd.DataFrame:
    """Create a stable-column DataFrame for persistence or export."""

    columns = [
        "feedback_id", "text", "sanitized_text", "sku", "channel",
        "created_at", "order_id", "source_file", "run_id",
    ]
    frame = pd.DataFrame(list(records))
    for column in columns:
        if column not in frame:
            frame[column] = ""
    return frame[columns]
