"""基于 LangChain 的结构化分类服务。"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Callable, Iterable

from langchain_core.prompts import ChatPromptTemplate
from pydantic import ValidationError
import yaml

from app.models.classification import FeedbackClassification, requires_human_review


PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "classify.md"
TAXONOMY_PATH = Path(__file__).resolve().parents[2] / "config" / "taxonomy.yaml"


@dataclass(frozen=True)
class ClassificationFailure:
    feedback_id: str
    error: str
    attempts: int


@dataclass(frozen=True)
class ClassificationResult:
    feedback_id: str
    output: FeedbackClassification
    needs_human_review: bool


def load_system_prompt(
    prompt_path: str | Path = PROMPT_PATH,
    taxonomy_path: str | Path = TAXONOMY_PATH,
) -> str:
    """读取分类说明并注入带版本的分类体系上下文。"""

    prompt = Path(prompt_path).read_text(encoding="utf-8")
    taxonomy = yaml.safe_load(Path(taxonomy_path).read_text(encoding="utf-8"))
    context = [
        f"Taxonomy 版本：{taxonomy.get('version', 'unknown')}",
        "可用类别定义：",
    ]
    for category in taxonomy.get("categories", []):
        context.append(
            f"- {category['name']}：{category['definition']}；"
            f"子类={', '.join(category.get('subcategories', []))}；"
            f"关键词={', '.join(category.get('keywords', []))}；"
            f"反例={', '.join(category.get('counterexamples', []))}"
        )
    return prompt + "\n\n## 当前 Taxonomy\n" + "\n".join(context)


def build_chat_model(model_name: str | None = None) -> Any:
    """延迟创建兼容 OpenAI 接口的 ChatModel。"""

    from langchain_openai import ChatOpenAI

    options: dict[str, Any] = {
        "model": model_name or os.getenv("LLM_MODEL", "gpt-4o-mini"),
        "temperature": 0,
        "timeout": 45,
        "max_retries": 0,
    }
    # DeepSeek 使用 OpenAI 兼容协议，通过基础地址切换供应商。
    base_url = os.getenv("OPENAI_BASE_URL", "").strip()
    if base_url:
        options["base_url"] = base_url
    return ChatOpenAI(**options)


def _structured_chain(model: Any, prompt: str) -> Any:
    # 使用结构化输出，让模型响应直接映射到 Pydantic 契约，避免下游解析散文。
    messages = ChatPromptTemplate.from_messages([
        ("system", prompt),
        ("human", "请分析以下反馈：\n反馈 ID：{feedback_id}\nSKU：{sku}\n渠道：{channel}\n内容：{text}"),
    ])
    method = os.getenv("LLM_STRUCTURED_OUTPUT_METHOD", "").strip()
    if not method:
        # DeepSeek 当前不支持默认的 json_schema 响应格式，自动切换为 JSON 模式。
        base_url = os.getenv("OPENAI_BASE_URL", "").lower()
        method = "json_mode" if "deepseek.com" in base_url else "json_schema"
    return messages | model.with_structured_output(FeedbackClassification, method=method)


def classify_one(
    record: dict[str, Any],
    model: Any,
    *,
    max_attempts: int = 3,
    prompt_path: str | Path = PROMPT_PATH,
) -> ClassificationResult:
    """分类一条已清洗记录，并确定性地执行复核门槛。"""

    chain = _structured_chain(model, load_system_prompt(prompt_path))
    last_error: Exception | None = None
    for _attempt in range(1, max_attempts + 1):
        try:
            raw = chain.invoke({
                "feedback_id": record.get("feedback_id", ""),
                "sku": record.get("sku", ""),
                "channel": record.get("channel", "未知"),
                "text": record.get("sanitized_text", record.get("text", "")),
            })
            output = raw if isinstance(raw, FeedbackClassification) else FeedbackClassification.model_validate(raw)
            return ClassificationResult(
                feedback_id=str(record.get("feedback_id", "")),
                output=output,
                needs_human_review=requires_human_review(output),
            )
        except (ValidationError, ValueError, TypeError) as exc:
            # 输出结构错误可通过重试修复；每次重试仍受同一契约约束。
            last_error = exc
        except Exception as exc:  # 供应商或网络错误可以重试
            # 超时、限流等供应商错误也隔离在单条反馈内，避免整批失败。
            last_error = exc
    raise RuntimeError(
        f"分类失败 feedback_id={record.get('feedback_id', '')}, attempts={max_attempts}: {last_error}"
    ) from last_error


def classify_records(
    records: Iterable[dict[str, Any]],
    model: Any,
    *,
    max_attempts: int = 3,
    on_result: Callable[[ClassificationResult], None] | None = None,
) -> tuple[list[ClassificationResult], list[ClassificationFailure]]:
    """逐条分类记录并隔离失败项，使批量处理可以继续。"""

    successes: list[ClassificationResult] = []
    failures: list[ClassificationFailure] = []
    for record in records:
        feedback_id = str(record.get("feedback_id", ""))
        try:
            result = classify_one(record, model, max_attempts=max_attempts)
            successes.append(result)
            if on_result:
                on_result(result)
        except RuntimeError as exc:
            failures.append(ClassificationFailure(feedback_id, str(exc), max_attempts))
    return successes, failures


def analyze_grade(records: list[dict[str, Any]], grade: str, model: Any) -> Any:
    """调用同一模型总结一个等级的原因和改进方向。"""

    from app.models.analysis import GradeAnalysis
    from langchain_core.prompts import ChatPromptTemplate

    prompt = ChatPromptTemplate.from_messages([
        ("system", """你是客户反馈洞察分析员。只输出符合结构化契约的结果。
好评需要总结客户认可的具体原因；中评需要指出体验卡点和可执行改进；差评需要说明核心原因、风险和优先处理方向。
所有结论必须能由反馈证据支持，不得编造数据，不得回显手机号、地址、姓名和完整订单号。"""),
        ("human", "目标等级：{grade}\n反馈数量：{count}\n反馈明细：\n{feedbacks}"),
    ])
    method = os.getenv("LLM_STRUCTURED_OUTPUT_METHOD", "").strip()
    if not method:
        method = "json_mode" if "deepseek.com" in os.getenv("OPENAI_BASE_URL", "").lower() else "json_schema"
    chain = prompt | model.with_structured_output(GradeAnalysis, method=method)
    evidence = "\n".join(
        f"- {record.get('sanitized_text', record.get('text', ''))[:500]}（类别：{record.get('category', '未知')} / {record.get('subcategory', '未知')}）"
        for record in records
    )
    return chain.invoke({"grade": grade, "count": len(records), "feedbacks": evidence or "暂无该等级反馈"})
