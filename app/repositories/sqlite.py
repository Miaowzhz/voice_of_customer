"""用于保存运行日志、反馈记录和 Issue 的 SQLite 仓储。"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from functools import wraps
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any, Iterable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _serialized(method):
    """同一连接的读写串行执行，防止后台批次之间交错提交事务。"""

    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped


class SQLiteRepository:
    """适用于演示和本地测试的小型 Upsert 仓储。"""

    def __init__(self, database: str | Path = "voc.db") -> None:
        self.database = str(database)
        self._lock = RLock()
        # FastAPI 可能在线程中处理请求；演示环境仍是单进程 SQLite，因此允许跨线程
        # 使用连接。多实例部署时应换成共享数据库。
        self.connection = sqlite3.connect(self.database, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_ref TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                input_count INTEGER NOT NULL DEFAULT 0,
                success_count INTEGER NOT NULL DEFAULT 0,
                failure_count INTEGER NOT NULL DEFAULT 0,
                review_count INTEGER NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                errors_json TEXT NOT NULL DEFAULT '[]',
                workflow_version TEXT NOT NULL DEFAULT '0.1.0'
            );
            CREATE TABLE IF NOT EXISTS feedback (
                feedback_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                text TEXT NOT NULL,
                sanitized_text TEXT NOT NULL,
                sku TEXT NOT NULL,
                channel TEXT NOT NULL,
                created_at TEXT NOT NULL,
                order_id TEXT NOT NULL DEFAULT '',
                category TEXT,
                subcategory TEXT,
                sentiment TEXT,
                severity TEXT,
                evidence TEXT,
                confidence REAL,
                needs_human_review INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS issues (
                issue_key TEXT PRIMARY KEY,
                issue_id TEXT NOT NULL,
                title TEXT NOT NULL,
                sku TEXT NOT NULL,
                category TEXT NOT NULL,
                subcategory TEXT NOT NULL,
                feedback_count INTEGER NOT NULL DEFAULT 0,
                evidence_json TEXT NOT NULL DEFAULT '[]',
                suggested_action TEXT NOT NULL,
                owner TEXT NOT NULL,
                status TEXT NOT NULL,
                run_id TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS run_reports (
                run_id TEXT PRIMARY KEY,
                report_json TEXT NOT NULL,
                chart_ref TEXT NOT NULL,
                delivery_status TEXT NOT NULL DEFAULT 'pending',
                delivery_error TEXT NOT NULL DEFAULT ''
            );
            """
        )
        self.connection.commit()

    @_serialized
    def upsert_run(self, state: dict[str, Any]) -> None:
        counters = state.get("counters", {})
        self.connection.execute(
            """
            INSERT INTO runs(run_id, source_type, source_ref, status, input_count,
                success_count, failure_count, review_count, started_at, finished_at,
                errors_json)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT started_at FROM runs WHERE run_id = ?), ?), ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                source_type=excluded.source_type, source_ref=excluded.source_ref,
                status=excluded.status, input_count=excluded.input_count,
                success_count=excluded.success_count, failure_count=excluded.failure_count,
                review_count=excluded.review_count, finished_at=excluded.finished_at,
                errors_json=excluded.errors_json
            """,
            (
                state["run_id"], state.get("source_type", "batch"), state.get("source_ref", ""),
                state.get("status", "running"), counters.get("input_count", 0),
                counters.get("classified_count", 0), len(state.get("errors", [])),
                len(state.get("review_ids", [])), state["run_id"], _now(),
                _now() if state.get("status") in {"completed", "failed"} else None,
                json.dumps(state.get("errors", []), ensure_ascii=False),
            ),
        )
        self.connection.commit()

    @_serialized
    def upsert_feedback(
        self,
        records: Iterable[dict[str, Any]],
        classifications: Iterable[dict[str, Any]],
    ) -> None:
        classifications_by_id = {row["feedback_id"]: row for row in classifications}
        now = _now()
        for record in records:
            classification = classifications_by_id.get(record["feedback_id"], {})
            self.connection.execute(
                """
                INSERT INTO feedback(feedback_id, run_id, text, sanitized_text, sku, channel,
                    created_at, order_id, category, subcategory, sentiment, severity, evidence,
                    confidence, needs_human_review, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(feedback_id) DO UPDATE SET
                    run_id=excluded.run_id, text=excluded.text, sanitized_text=excluded.sanitized_text,
                    sku=excluded.sku, channel=excluded.channel, created_at=excluded.created_at,
                    order_id=excluded.order_id, category=excluded.category, subcategory=excluded.subcategory,
                    sentiment=excluded.sentiment, severity=excluded.severity, evidence=excluded.evidence,
                    confidence=excluded.confidence, needs_human_review=excluded.needs_human_review,
                    updated_at=excluded.updated_at
                """,
                (
                    record["feedback_id"], record.get("run_id", ""), record.get("text", ""),
                    record.get("sanitized_text", ""), record.get("sku", ""), record.get("channel", "未知"),
                    record.get("created_at", ""), record.get("order_id", ""),
                    classification.get("category"), classification.get("subcategory"),
                    classification.get("sentiment"), classification.get("severity"),
                    classification.get("evidence"), classification.get("confidence"),
                    int(bool(classification.get("needs_human_review", False))), now,
                ),
            )
        self.connection.commit()

    @_serialized
    def upsert_issues(self, issues: Iterable[dict[str, Any]], run_id: str) -> None:
        now = _now()
        for issue in issues:
            self.connection.execute(
                """
                INSERT INTO issues(issue_key, issue_id, title, sku, category, subcategory,
                    feedback_count, evidence_json, suggested_action, owner, status, run_id, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(issue_key) DO UPDATE SET
                    -- 同一 run 重放时覆盖数量，跨 run 才累加，避免重复上传放大统计。
                    feedback_count=CASE
                        WHEN issues.run_id = excluded.run_id THEN excluded.feedback_count
                        ELSE issues.feedback_count + excluded.feedback_count
                    END,
                    evidence_json=excluded.evidence_json, suggested_action=excluded.suggested_action,
                    owner=excluded.owner, run_id=excluded.run_id, updated_at=excluded.updated_at
                """,
                (
                    issue["issue_key"], issue["issue_id"], issue["title"], issue["sku"],
                    issue["category"], issue["subcategory"], issue["feedback_count"],
                    json.dumps(issue.get("evidence", []), ensure_ascii=False), issue["suggested_action"],
                    issue["owner"], issue.get("status", "待处理"), run_id, now,
                ),
            )
        self.connection.commit()

    @_serialized
    def fetch_issue(self, key: str) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM issues WHERE issue_key = ?", (key,)).fetchone()

    @_serialized
    def fetch_run(self, run_id: str) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()

    @_serialized
    def save_report(self, state: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT INTO run_reports(run_id,report_json,chart_ref) VALUES(?,?,?) "
            "ON CONFLICT(run_id) DO UPDATE SET report_json=excluded.report_json,chart_ref=excluded.chart_ref",
            (state["run_id"], json.dumps(state["analysis_report"], ensure_ascii=False), state["chart_ref"]),
        )
        self.connection.commit()

    @_serialized
    def set_report_delivery(self, run_id: str, status: str, error: str = "") -> None:
        self.connection.execute(
            "UPDATE run_reports SET delivery_status=?,delivery_error=? WHERE run_id=?", (status, error, run_id),
        )
        self.connection.commit()

    @_serialized
    def fetch_report(self, run_id: str) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM run_reports WHERE run_id=?", (run_id,)).fetchone()

    @_serialized
    def list_feedback(self, start_at: str | None = None, end_at: str | None = None) -> list[sqlite3.Row]:
        query = "SELECT * FROM feedback"
        params: list[str] = []
        conditions = []
        if start_at:
            conditions.append("created_at >= ?")
            params.append(start_at)
        if end_at:
            conditions.append("created_at < ?")
            params.append(end_at)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at, feedback_id"
        return list(self.connection.execute(query, params).fetchall())

    @_serialized
    def list_issues(self) -> list[sqlite3.Row]:
        return list(self.connection.execute("SELECT * FROM issues ORDER BY feedback_count DESC, issue_key").fetchall())

    @_serialized
    def close(self) -> None:
        self.connection.close()
