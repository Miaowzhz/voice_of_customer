"""Pydantic contract for one feedback classification."""

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


class FeedbackClassification(BaseModel):
    """The only model output accepted by the pipeline."""

    model_config = ConfigDict(extra="forbid")

    category: Category
    subcategory: str = Field(min_length=1, max_length=50)
    sentiment: Sentiment
    severity: Severity
    is_actionable: bool
    sku: str = Field(default="", max_length=200)
    evidence: str = Field(min_length=1, max_length=300)
    suggested_owner: str = Field(min_length=1, max_length=50)
    suggested_action: str = Field(min_length=1, max_length=300)
    confidence: float = Field(ge=0.0, le=1.0)
    needs_human_review: bool


def requires_human_review(result: FeedbackClassification) -> bool:
    """Apply deterministic review gates after the model response."""

    return result.needs_human_review or result.confidence < 0.75
