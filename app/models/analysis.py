"""等级分析节点使用的结构化输出契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class GradeAnalysis(BaseModel):
    """一个等级的原因、改进方向和一句话结论。"""

    grade: Literal["好", "中", "差"]
    summary: str = Field(min_length=1, max_length=300)
    reasons: list[str] = Field(default_factory=list, max_length=8)
    improvements: list[str] = Field(default_factory=list, max_length=8)
