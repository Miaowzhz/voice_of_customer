"""在 1000 条合成反馈记录上运行确定性流水线。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.aggregate import aggregate_feedback
from app.services.cleaning import clean_records, load_feedback_file


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(ROOT / "mock_feedback_50.csv"))
    parser.add_argument("--copies", type=int, default=20)
    parser.add_argument("--benchmark", default=str(ROOT / "data" / "benchmark_50.csv"))
    args = parser.parse_args()
    source = load_feedback_file(args.input)
    benchmark = {
        row["feedback_id"]: row
        for row in load_feedback_file(args.benchmark)
    }
    rows = [{**row, "feedback_id": f"{row['feedback_id']}-R{copy:02d}"} for copy in range(args.copies) for row in source]
    start = time.perf_counter()
    cleaned = clean_records(rows, run_id="stress-test")
    labeled = []
    for record in cleaned.records:
        original_id = record["feedback_id"].rsplit("-R", 1)[0]
        gold = benchmark.get(original_id, {})
        labeled.append({
            **record,
            "category": gold.get("expected_category", "其他/待人工确认"),
            "subcategory": gold.get("expected_subcategory", "信息不足"),
        })
    summary = aggregate_feedback(labeled)
    elapsed = time.perf_counter() - start
    result = {"input_count": len(rows), "cleaned_count": len(cleaned.records), "failure_count": len(cleaned.failures), "elapsed_seconds": round(elapsed, 4), "category_distribution": summary["category_distribution"]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
