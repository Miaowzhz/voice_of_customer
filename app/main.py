"""FastAPI application entry point."""

import os

from app.api.feishu_webhook import create_app
from app.graph.builder import build_graph
from app.repositories.sqlite import SQLiteRepository
from app.runtime import RunService
from app.services.classify import build_chat_model, classify_one
from app.services.feishu import FeishuClient

repository = SQLiteRepository(os.getenv("VOC_DATABASE", "voc.db"))

_feishu_client = None
_app_id = os.getenv("FEISHU_APP_ID", "")
_app_secret = os.getenv("FEISHU_APP_SECRET", "")
if _app_id and _app_secret:
    _feishu_client = FeishuClient(_app_id, _app_secret)

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
    file_downloader=(
        lambda event, target: _feishu_client.download_message_resource(
            event["message_id"], event["file_key"], target
        )
    ) if _feishu_client else None,
    feishu_client=_feishu_client,
)
app = create_app(repository=repository, run_service=run_service)
