"""飞书会话命令解析与交互文案。"""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class FeishuCommand:
    """一条可以由机器人直接处理的会话命令。"""

    name: str
    argument: str = ""


_COMMANDS = {
    "帮助": "help",
    "help": "help",
    "状态": "status",
    "status": "status",
    "查询": "status",
    "报告": "report",
    "report": "report",
    "复核": "help",
    "生成周报": "help",
    "新会话": "new_session",
    "new": "new_session",
    "reset": "new_session",
}


def parse_command(text: str) -> FeishuCommand | None:
    """只识别完整的命令首词，避免把普通反馈误判为命令。"""

    value = text.strip()
    if value.startswith("/"):
        value = value[1:].strip()
    if not value:
        return None
    parts = re.split(r"\s+", value, maxsplit=1)
    name = _COMMANDS.get(parts[0].lower())
    if name is None:
        return None
    return FeishuCommand(name=name, argument=parts[1].strip() if len(parts) > 1 else "")


def help_text() -> str:
    """返回面向业务用户的简短命令说明。"""

    return (
        "VOC 反馈分析助手\n"
        "\n"
        "发送 Excel、CSV、JSON 或飞书多维表格链接即可开始分析。\n"
        "\n"
        "可用命令：\n"
        "• /状态：查看当前会话最近一次分析状态\n"
        "• /报告：重新发送最近一次分析摘要和图片\n"
        "• /状态 <run_id>：查询指定运行\n"
        "• /报告 <run_id>：查看指定运行报告\n"
        "• /新会话：清除当前会话关联的运行\n"
        "• /帮助：查看本说明"
    )
