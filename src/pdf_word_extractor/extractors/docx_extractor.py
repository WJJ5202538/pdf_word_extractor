"""Word 提取器：读取段落、表格和文档核心属性。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from docx import Document

from .base import ExtractionError, build_result, json_safe, make_base_meta

# python-docx 的 CoreProperties 属性 -> 统一元信息中的键
_CORE_FIELDS = {
    "title": "title",
    "subject": "subject",
    "author": "author",
    "last_modified_by": "last_modified_by",
    "created": "created",
    "modified": "modified",
    "revision": "revision",
    "category": "category",
    "comments": "comments",
    "keywords": "keywords",
    "language": "language",
    "identifier": "identifier",
    "version": "version",
    "content_status": "content_status",
    "last_printed": "last_printed",
}


def _build_meta(document: Any, path: Path) -> dict[str, Any]:
    """合并基础文件信息与 Word 核心属性。"""
    meta = make_base_meta(path, "docx")
    properties = document.core_properties

    for attribute, key in _CORE_FIELDS.items():
        try:
            value = getattr(properties, attribute, None)
        except Exception:
            continue
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        meta[key] = json_safe(value)

    return json_safe(meta)


def _extract_table(table: Any) -> list[list[str]]:
    """把一个 Word 表格转换为二维字符串数组。"""
    rows: list[list[str]] = []
    for row in table.rows:
        cells = [" ".join(cell.text.split()) for cell in row.cells]
        if any(cells):
            rows.append(cells)
    return rows


def extract_docx(path: Path) -> dict[str, Any]:
    """提取 .docx 的正文段落、表格与核心属性。

    Word 不提供可靠的物理页数，因此统计中的 page_count 固定为 0，
    界面会显示为「页数 —」。

    Args:
        path: 已通过基础校验的 Word 路径。

    Returns:
        统一结构的提取结果。

    Raises:
        ExtractionError: 文件损坏、不是有效 .docx 或无法读取时抛出。
    """
    try:
        document = Document(str(path))
    except Exception as exc:
        raise ExtractionError(
            f"Word 文档解析失败：文件可能已损坏或不是有效的 .docx 文件。（{exc}）"
        ) from exc

    try:
        paragraphs = [
            paragraph.text.strip()
            for paragraph in document.paragraphs
            if paragraph.text.strip()
        ]

        tables: list[list[list[str]]] = []
        warnings: list[str] = []
        for index, table in enumerate(document.tables, start=1):
            try:
                rows = _extract_table(table)
                if rows:
                    tables.append(rows)
            except Exception as exc:  # 单个表格异常不影响正文与其他表格
                warnings.append(f"第 {index} 个表格提取失败：{exc}")

        meta = _build_meta(document, path)
        if warnings:
            meta["warnings"] = warnings

    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(f"Word 文档内容提取失败：{exc}") from exc

    return build_result(
        path=path,
        file_type="docx",
        meta=meta,
        pages=[],
        paragraphs=paragraphs,
        tables=tables,
        stats_text="\n".join(paragraphs),
        page_count=0,
    )
