"""Generate a weekly report from SQLite.

Use --seed-mock for a self-contained local demo run.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.repositories.sqlite import SQLiteRepository
from app.services.aggregate import aggregate_feedback
from app.services.chart import render_pie_chart, write_distribution_summary
from app.services.cleaning import clean_records, load_feedback_file
from app.services.issues import build_issue_candidates
from app.services.report import generate_weekly_report


def seed_mock(repository: SQLiteRepository) -> None:
    source = ROOT / "mock_feedback_50.csv"
    benchmark = ROOT / "data" / "benchmark_50.csv"
    cleaned = clean_records(load_feedback_file(source), run_id="weekly-mock")
    labels = {
        row["feedback_id"]: {"category": row["expected_category"], "subcategory": row["expected_subcategory"], "severity": row["expected_severity"]}
        for row in load_feedback_file(benchmark)
    }
    classifications = []
    for record in cleaned.records:
        classification = {"feedback_id": record["feedback_id"], **labels[record["feedback_id"]], "sentiment": "负面", "evidence": record["sanitized_text"], "confidence": 0.9, "needs_human_review": False}
        classifications.append(classification)
    repository.upsert_run({"run_id": "weekly-mock", "source_type": "batch", "source_ref": str(source), "status": "completed", "counters": {"input_count": 50, "classified_count": 50}, "errors": [], "review_ids": []})
    repository.upsert_feedback(cleaned.records, classifications)
    repository.upsert_issues(build_issue_candidates(cleaned.records, classifications), "weekly-mock")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="voc.db")
    parser.add_argument("--end-date", default=None, help="YYYY-MM-DD，默认今天")
    parser.add_argument("--output-dir", default="artifacts/weekly_report")
    parser.add_argument("--seed-mock", action="store_true")
    args = parser.parse_args()
    repository = SQLiteRepository(args.database)
    if args.seed_mock:
        seed_mock(repository)
    report = generate_weekly_report(repository, end_date=args.end_date)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "weekly_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_distribution_summary(report, output_dir / "feedback_type_summary.json")
    render_pie_chart(report, output_dir / "feedback_type_pie.png")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    repository.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
