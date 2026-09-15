"""单条反馈分类的 Pydantic 契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


Category = Literal[
    "产品质量",
    "尺寸与规格",
    "功能/兼容性",
    "包装与物流损坏",
    "配件/赠品缺失",
    "使用方法与清洁",
    "商品描述或直播承诺不一致",
    "价格、优惠和活动规则",
    "售后服务与退款",
    "其他/待人工确认",
]
Sentiment = Literal["正面", "中性", "负面", "混合", "未知"]
Severity = Literal["低", "中", "高"]
Grade = Literal["好", "中", "差"]


class FeedbackClassification(BaseModel):
    """流水线唯一接受的模型输出格式。"""

    model_config = ConfigDict(extra="forbid")

    category: Category = "其他/待人工确认"
    subcategory: str = Field(default="信息不足", min_length=1, max_length=50)
    sentiment: Sentiment = "未知"
    severity: Severity = "低"
    is_actionable: bool = False
    sku: str = Field(default="", max_length=200)
    evidence: str = Field(default="未提供有效证据", min_length=1, max_length=300)
    suggested_owner: str = Field(default="客服主管", min_length=1, max_length=50)
    suggested_action: str = Field(default="人工确认", min_length=1, max_length=300)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_human_review: bool = True
    grade: Grade = "中"


def requires_human_review(result: FeedbackClassification) -> bool:
    """在模型响应后应用确定性的复核门槛。"""

    # 模型省略字段时使用安全默认值，并强制进入人工复核，避免静默放行。
    # grade 兼容旧分类器的默认值；新模型提示词会显式要求输出该字段。
    required_fields = set(FeedbackClassification.model_fields) - {"grade"}
    if not required_fields.issubset(result.model_fields_set):
        return True
    return result.needs_human_review or result.confidence < 0.75
