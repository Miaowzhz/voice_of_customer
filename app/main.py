"""FastAPI 应用入口。"""

import os
from threading import Lock

from dotenv import load_dotenv

from app.api.feishu_webhook import create_app
from app.graph.builder import build_graph
from app.repositories.sqlite import SQLiteRepository
from app.runtime import RunService
from app.services.classify import analyze_grade, build_chat_model, classify_one
from app.services.feishu import FeishuClient

load_dotenv()

repository = SQLiteRepository(os.getenv("VOC_DATABASE", "voc.db"))

_feishu_client = None
_app_id = os.getenv("FEISHU_APP_ID", "")
_app_secret = os.getenv("FEISHU_APP_SECRET", "")
if _app_id and _app_secret:
    _feishu_client = FeishuClient(_app_id, _app_secret)

_model = None
_model_lock = Lock()


def _get_model():
    """并发批量首次调用时只初始化一次模型客户端。"""

    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                _model = build_chat_model()
    return _model


def llm_classifier(record: dict) -> object:
    """延迟构建模型，使 API 启动不依赖模型凭据。"""

    return classify_one(record, _get_model()).output


def llm_grade_analyzer(records: list[dict], grade: str) -> object:
    """使用同一模型总结指定等级的原因和改进方向。"""

    return analyze_grade(records, grade, _get_model())


run_service = RunService(
    repository=repository,
    graph_factory=lambda: build_graph(classifier=llm_classifier, grade_analyzer=llm_grade_analyzer),
    file_downloader=(
        lambda event, target: _feishu_client.download_message_resource(
            event["message_id"], event["file_key"], target
        )
    ) if _feishu_client else None,
    feishu_client=_feishu_client,
)
app = create_app(repository=repository, run_service=run_service)
