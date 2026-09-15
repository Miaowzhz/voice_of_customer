"""Chart rendering for feedback distributions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_distribution_summary(summary: dict[str, Any], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def render_pie_chart(summary: dict[str, Any], output_path: str | Path) -> Path:
    """Render a PNG pie chart from an aggregation summary."""

    import matplotlib.pyplot as plt

    # macOS has Chinese fonts available, while Matplotlib's default DejaVu
    # Sans does not contain CJK glyphs. Keep a portable fallback list so the
    # generated demo chart renders Chinese labels without tofu boxes.
    plt.rcParams["font.sans-serif"] = [
        "Heiti SC", "Hiragino Sans GB", "Arial Unicode MS", "DejaVu Sans"
    ]
    plt.rcParams["axes.unicode_minus"] = False

    distribution = summary.get("category_distribution", [])
    labels = [item["category"] for item in distribution]
    values = [item["count"] for item in distribution]
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(9, 6), dpi=150)
    if values:
        ax.pie(values, labels=labels, autopct="%1.1f%%", startangle=90, counterclock=False)
    else:
        ax.text(0.5, 0.5, "暂无有效反馈", ha="center", va="center")
    ax.set_title(f"反馈类型分布（共 {summary.get('total_valid', 0)} 条）")
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path
