"""反馈类型分布图渲染服务。"""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any


_RENDER_LOCK = Lock()


def write_distribution_summary(summary: dict[str, Any], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def render_pie_chart(summary: dict[str, Any], output_path: str | Path) -> Path:
    """使用无窗口画布渲染中文饼图，允许后台工作线程安全调用。"""

    from matplotlib import rc_context
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    distribution = summary.get("category_distribution", [])
    fonts = ["PingFang SC", "Heiti SC", "Hiragino Sans GB", "Arial Unicode MS", "Noto Sans CJK SC", "WenQuanYi Micro Hei", "DejaVu Sans"]
    # Matplotlib 的字体和样式缓存为进程共享状态，渲染阶段串行即可。
    with _RENDER_LOCK, rc_context({"font.sans-serif": fonts, "axes.unicode_minus": False}):
        fig = Figure(figsize=(12, 7), dpi=150, facecolor="#f8fafc")
        FigureCanvasAgg(fig)
        ax = fig.add_axes((0.03, 0.16, 0.58, 0.68))
        colors = ["#2563eb", "#0d9488", "#f59e0b", "#8b5cf6", "#e11d48", "#06b6d4", "#84cc16", "#f97316", "#6366f1", "#94a3b8"]
        if distribution:
            wedges, _, _ = ax.pie(
                [item["count"] for item in distribution], startangle=90, counterclock=False,
                colors=colors, wedgeprops={"width": 0.38, "edgecolor": "#f8fafc", "linewidth": 2},
                autopct=lambda pct: f"{pct:.1f}%" if pct >= 5 else "", pctdistance=0.81,
                textprops={"color": "white", "fontsize": 11, "weight": "bold"},
            )
            labels = [f"{item['category']}  {item['count']} 条  {item['ratio']:.1f}%" for item in distribution]
            fig.legend(wedges, labels, loc="center left", bbox_to_anchor=(0.60, 0.50), frameon=False, fontsize=10)
            ax.text(0, 0.10, str(summary.get("total_valid", 0)), ha="center", va="center", fontsize=34, color="#0f172a")
            ax.text(0, -0.17, "分类成功反馈", ha="center", va="center", fontsize=11, color="#64748b")
        else:
            ax.text(0.5, 0.5, "暂无有效反馈", ha="center", va="center")
        product = summary.get("product", "")
        fig.text(0.06, 0.91, "反馈类型分布", fontsize=23, weight="bold", color="#0f172a")
        fig.text(0.06, 0.86, str(product)[:60], fontsize=12, color="#475569")
        note = f"占比分母：分类成功的 {summary.get('total_valid', 0)} 条反馈。"
        if summary.get("review_count"):
            note += f"包含 {summary['review_count']} 条待人工复核的初步分类。"
        fig.text(0.06, 0.07, note, fontsize=10, color="#64748b")
        fig.savefig(path, facecolor=fig.get_facecolor())
        fig.clear()
    return path
