"""FastAPI application entry point."""

from app.api.feishu_webhook import create_app

app = create_app()
