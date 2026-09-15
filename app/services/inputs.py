"""将表格、JSON 和飞书多维表记录转换为统一的反馈输入。"""

from __future__ import annotations

from datetime import datetime
import json
import re
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from app.services.cleaning import normalize_sku


FIELD_ALIASES = {
    "feedback_id": ("feedback_id", "反馈ID", "反馈id", "反馈编号", "记录ID"),
    "text": ("text", "反馈内容", "客户反馈", "内容", "评价内容"),
    "sku": ("sku", "SKU", "产品", "产品名称", "商品名称", "产品型号"),
    "channel": ("channel", "渠道", "反馈渠道", "来源"),
    "created_at": ("created_at", "反馈时间", "时间", "创建时间", "日期"),
    "order_id": ("order_id", "订单号", "订单编号"),
}


def strip_leading_mentions(text: str, mentions: list[Any]) -> str:
    """仅移除消息开头已由飞书标记的收件人，保留 JSON 内的正文。"""

    tokens = []
    for mention in mentions:
        key = mention.get("key", "") if isinstance(mention, dict) else getattr(mention, "key", "")
        name = mention.get("name", "") if isinstance(mention, dict) else getattr(mention, "name", "")
        tokens.extend(token for token in (key, f"@{name}" if name else "") if token)
    value = text.strip()
    while True:
        token = next((token for token in sorted(tokens, key=len, reverse=True) if value.startswith(token) and (len(value) == len(token) or value[len(token)].isspace())), None)
        if token is None:
            return value
        value = value[len(token):].lstrip()


def json_message(text: str) -> str | None:
    """识别 JSON 消息及代码块，保留解析错误供用户修正。"""

    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
        value = re.sub(r"\s*```$", "", value)
    # 飞书富文本链接可能以 [标题](链接) 开头，不能将其误当作 JSON 数组。
    return value if value.startswith("{") or re.match(r"^\[\s*(?:\{|\]|$)", value) else None


def decode_feedback_json(text: str) -> tuple[list[dict[str, Any]], str]:
    """接受记录数组，或包含 product/sku 和 feedbacks/records 的对象。"""

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 格式错误：第 {exc.lineno} 行第 {exc.colno} 列") from exc
    product = ""
    if isinstance(data, dict):
        product = str(data.get("product") or data.get("sku") or data.get("产品") or "").strip()
        data = data.get("feedbacks", data.get("records", data.get("反馈列表")))
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        raise ValueError("JSON 需要是反馈对象数组，或包含 feedbacks 数组的对象")
    return data, product


def bitable_source(text: str) -> dict[str, str] | None:
    """只提取飞书链接中的标识符，后端始终请求固定的飞书 API 域名。"""

    for match in re.finditer(r"https://[^\s<>\"）)]+", text):
        url = match.group(0).rstrip("。，;；")
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if not (host == "feishu.cn" or host.endswith(".feishu.cn") or host.endswith(".larksuite.com")):
            continue
        path = re.fullmatch(r"/(base|wiki)/([A-Za-z0-9]+)/*", parsed.path)
        if not path:
            continue
        params = parse_qs(parsed.query)
        table_id = params.get("table", params.get("table_id", [""]))[0]
        view_id = params.get("view", params.get("view_id", [""]))[0]
        if not re.fullmatch(r"tbl[A-Za-z0-9]+", table_id):
            raise ValueError("请打开多维表中的目标数据表，复制包含 table=tbl… 的链接")
        if view_id and not re.fullmatch(r"vew[A-Za-z0-9]+", view_id):
            raise ValueError("多维表链接中的视图 ID 无效")
        return {"kind": path[1], "token": path[2], "table_id": table_id, "view_id": view_id, "url": url}
    return None


def _cell(value: Any) -> Any:
    # 多维表的富文本单元格通常是片段数组；日期则保留毫秒时间戳处理。
    if isinstance(value, list):
        return "".join(str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in value)
    if isinstance(value, dict):
        return value.get("text", value.get("name", ""))
    return "" if value is None else value


def normalize_input_rows(
    rows: list[dict[str, Any]], *, product: str = "", channel: str = "批量导入", single_product: bool = True,
) -> list[dict[str, Any]]:
    """映射中英文列名，补齐导入元数据，并阻止不同产品被混为一份报告。"""

    if not rows:
        raise ValueError("没有可分析的反馈记录")
    if not all(isinstance(row, dict) and isinstance(row.get("fields", row), dict) for row in rows):
        raise ValueError("每条反馈必须是字段对象，多维表记录的 fields 也必须是对象")
    columns = {str(key).strip() for row in rows for key in (row.get("fields", row)).keys()}
    if not columns.intersection(FIELD_ALIASES["text"]):
        raise ValueError("缺少反馈内容列，请使用 text 或“反馈内容”作为列名")
    imported_at = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    normalized = []
    for source in rows:
        fields = {str(k).strip(): _cell(v) for k, v in source.get("fields", source).items()}
        row = {}
        for field, aliases in FIELD_ALIASES.items():
            row[field] = next((fields[key] for key in aliases if key in fields and fields[key] != ""), "")
        row["feedback_id"] = row["feedback_id"] or source.get("record_id", "")
        row["sku"] = row["sku"] or product
        row["channel"] = row["channel"] or channel
        timestamp = row["created_at"]
        if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
            timestamp = datetime.fromtimestamp(timestamp / 1000, ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
        row["created_at"] = timestamp or imported_at
        normalized.append(row)
    products = {normalize_sku(row["sku"]) for row in normalized if row["sku"]}
    if single_product and len(products) > 1:
        raise ValueError("本次数据包含多个产品，请先筛选一个产品再上传，或发送该产品的多维表视图链接")
    inferred = next(iter(products)) if len(products) == 1 else ""
    for row in normalized:
        row["sku"] = row["sku"] or inferred
    return normalized
