"""JSON / Markdown 导出：不依赖 Tkinter，方便单元测试与脚本复用。"""

from __future__ import annotations

import json
from typing import Any

from .extractors.base import NO_TEXT_MARKER
from .labels import FILE_TYPE_LABELS, META_LABELS

_NEWLINE = "\n"


def display_meta_value(value: Any) -> str:
    """把任意元信息值转换成适合界面或 Markdown 展示的文本。"""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, (list, tuple)):
        return "；".join(display_meta_value(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    text = str(value).strip()
    return text or "—"


def _md_inline(value: Any) -> str:
    """元信息写入 Markdown 时，把换行变成 <br>，避免破坏列表结构。"""
    return "<br>".join(display_meta_value(value).splitlines()) or "—"


def _md_table_cell(value: Any) -> str:
    """转义 Markdown 表格中的竖线，并保留空单元格。"""
    text = "" if value is None else str(value).strip()
    text = "<br>".join(text.splitlines())
    return text.replace("|", "\\|")


def build_markdown(data: dict[str, Any]) -> str:
    """把统一提取结果渲染成 Markdown 文本。"""
    filename = str(data.get("filename", "未命名文件"))
    file_type = str(data.get("file_type", ""))
    meta = data.get("meta") or {}
    pages = data.get("pages") or []
    paragraphs = data.get("paragraphs") or []
    tables = data.get("tables") or []
    stats = data.get("stats") or {}

    lines: list[str] = [
        f"# {filename}",
        "",
        f"- **文件类型**：{FILE_TYPE_LABELS.get(file_type, file_type or '未知')}",
    ]

    lines += ["", "## 元信息", ""]
    if meta:
        for key, value in meta.items():
            label = META_LABELS.get(str(key), str(key))
            lines.append(f"- **{label}**：{_md_inline(value)}")
    else:
        lines.append("_无元信息。_")

    lines += ["", "## 正文", ""]
    if pages:
        for page in pages:
            page_number = page.get("page", "")
            lines += [f"### 第 {page_number} 页", ""]
            page_text = str(page.get("text", "")).strip()
            if page_text == NO_TEXT_MARKER:
                lines.append("_（本页无可提取文本）_")
            elif page_text:
                lines.append(page_text)
            else:
                lines.append("_（本页为空）_")
            lines.append("")
    elif paragraphs:
        for paragraph in paragraphs:
            lines += [str(paragraph), ""]
    else:
        lines += ["_没有可提取的正文。_", ""]

    if tables:
        lines += ["## 表格", ""]
        for table_index, table in enumerate(tables, start=1):
            lines += [f"### 表格 {table_index}", ""]
            column_count = max((len(row) for row in table), default=0)
            if column_count <= 0:
                lines += ["_空表格。_", ""]
                continue
            headers = [f"列 {index}" for index in range(1, column_count + 1)]
            lines.append("| " + " | ".join(headers) + " |")
            lines.append("| " + " | ".join(["---"] * column_count) + " |")
            for row in table:
                cells = [
                    _md_table_cell(row[index]) if index < len(row) else ""
                    for index in range(column_count)
                ]
                lines.append("| " + " | ".join(cells) + " |")
            lines.append("")

    page_count = stats.get("page_count", 0) or 0
    page_text = str(page_count) if file_type == "pdf" else "—"
    lines += [
        "## 统计",
        "",
        f"- 字符数：{stats.get('char_count', 0):,}",
        f"- 词数：{stats.get('word_count', 0):,}",
        f"- 页数：{page_text}",
        f"- 表格数：{len(tables)}",
        "",
    ]
    return _NEWLINE.join(lines).rstrip() + _NEWLINE
