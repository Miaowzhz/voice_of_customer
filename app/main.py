"""FastAPI application entry point."""

import os

from app.api.feishu_webhook import create_app
from app.repositories.sqlite import SQLiteRepository

app = create_app(repository=SQLiteRepository(os.getenv("VOC_DATABASE", "voc.db")))
