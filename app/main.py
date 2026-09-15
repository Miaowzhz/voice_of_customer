"""FastAPI application entry point."""

import os

from app.api.feishu_webhook import create_app
from app.graph.builder import build_graph
from app.repositories.sqlite import SQLiteRepository
from app.runtime import RunService
from app.services.classify import build_chat_model, classify_one

repository = SQLiteRepository(os.getenv("VOC_DATABASE", "voc.db"))

_model = None


def llm_classifier(record: dict) -> object:
    """Lazy model construction keeps API startup independent from credentials."""

    global _model
    if _model is None:
        _model = build_chat_model()
    return classify_one(record, _model).output


run_service = RunService(
    repository=repository,
    graph_factory=lambda: build_graph(classifier=llm_classifier),
)
app = create_app(repository=repository, run_service=run_service)
