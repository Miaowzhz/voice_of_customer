"""在模拟数据上运行确定性清洗与聚合流水线。

这是第一个可执行的流水线里程碑。在接入 LangChain 分类器前，
暂时使用人工基准标签作为分类结果。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.aggregate import aggregate_feedback
from app.services.chart import render_pie_chart, write_distribution_summary
from app.services.cleaning import calculate_run_id, clean_records, load_feedback_file, records_to_frame


def main() -> int:
    parser = argparse.ArgumentParser(description="导入 mock 反馈并生成统计与饼图")
    parser.add_argument("--input", default=str(ROOT / "mock_feedback_50.csv"))
    parser.add_argument("--benchmark", default=str(ROOT / "data" / "benchmark_50.csv"))
    parser.add_argument("--output-dir", default=str(ROOT / "artifacts" / "mock_run"))
    args = parser.parse_args()

    input_path = Path(args.input)
    benchmark_path = Path(args.benchmark)
    output_dir = Path(args.output_dir)
    run_id = calculate_run_id(input_path)

    raw_rows = load_feedback_file(input_path)
    cleaned = clean_records(raw_rows, run_id=run_id)

    benchmark_rows = load_feedback_file(benchmark_path)
    labels = {
        row["feedback_id"]: {
            "category": row["expected_category"],
            "subcategory": row["expected_subcategory"],
        }
        for row in benchmark_rows
    }
    labeled_records = [
        {**record, **labels.get(record["feedback_id"], {"category": "其他/待人工确认", "subcategory": "信息不足"})}
        for record in cleaned.records
    ]
    summary = aggregate_feedback(labeled_records)
    summary.update({
        "run_id": run_id,
        "input_count": cleaned.input_count,
        "duplicate_count": cleaned.duplicate_count,
        "failure_count": len(cleaned.failures),
    })

    output_dir.mkdir(parents=True, exist_ok=True)
    records_to_frame(labeled_records).to_csv(output_dir / "cleaned_feedback.csv", index=False, encoding="utf-8-sig")
    write_distribution_summary(summary, output_dir / "feedback_type_summary.json")
    render_pie_chart(summary, output_dir / "feedback_type_pie.png")
    (output_dir / "run_log.json").write_text(
        json.dumps({
            "run_id": run_id,
            "input_count": cleaned.input_count,
            "success_count": len(cleaned.records),
            "duplicate_count": cleaned.duplicate_count,
            "failure_count": len(cleaned.failures),
            "failures": [failure.__dict__ for failure in cleaned.failures],
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({
        "run_id": run_id,
        "input_count": cleaned.input_count,
        "success_count": len(cleaned.records),
        "failure_count": len(cleaned.failures),
        "duplicate_count": cleaned.duplicate_count,
        "category_distribution": summary["category_distribution"],
        "output_dir": str(output_dir),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
