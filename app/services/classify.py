"""LangChain-backed structured classification service."""

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
    """Load the instructions and inject the versioned taxonomy context."""

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
    """Create an OpenAI-compatible ChatModel lazily."""

    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model_name or os.getenv("LLM_MODEL", "gpt-4o-mini"),
        temperature=0,
        timeout=45,
        max_retries=0,
    )


def _structured_chain(model: Any, prompt: str) -> Any:
    messages = ChatPromptTemplate.from_messages([
        ("system", prompt),
        ("human", "请分析以下反馈：\n反馈 ID：{feedback_id}\nSKU：{sku}\n渠道：{channel}\n内容：{text}"),
    ])
    return messages | model.with_structured_output(FeedbackClassification)


def classify_one(
    record: dict[str, Any],
    model: Any,
    *,
    max_attempts: int = 3,
    prompt_path: str | Path = PROMPT_PATH,
) -> ClassificationResult:
    """Classify one cleaned record and enforce review gates deterministically."""

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
            last_error = exc
        except Exception as exc:  # provider/network errors are retryable
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
    """Classify records one by one, isolating failures so a batch can continue."""

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
