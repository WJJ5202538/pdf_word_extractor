"""提取器公共能力：文件校验、元信息、统一结果结构与统计。"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

# 单个文件的大小上限：20 MB
MAX_FILE_SIZE = 20 * 1024 * 1024

# 扩展名 -> 写入 JSON 的 file_type
SUPPORTED_EXTENSIONS = {
    ".pdf": "pdf",
    ".docx": "docx",
}

# PDF 页面没有文本层时写入 pages[].text 的标记
NO_TEXT_MARKER = "无可提取文本"

# 安装提示：核心功能与可选 OCR 分开，避免用户为了跑 GUI 装整套 Paddle。
CORE_INSTALL_HINT = "python -m pip install -e ."
OCR_INSTALL_HINT = 'python -m pip install -e ".[ocr]"'

# 中文按单个汉字计词，英文/数字按连续单词计词
_WORD_PATTERN = re.compile(
    r"[㐀-䶿一-鿿]|[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*"
)


class ExtractionError(Exception):
    """可直接展示给用户的提取错误。"""


def format_file_size(size: int) -> str:
    """把字节数转换为便于阅读的大小文本。"""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def validate_file(path: str | Path) -> Path:
    """校验文件是否存在、类型是否受支持，以及是否超过 20 MB。

    Args:
        path: 待校验的文件路径。

    Returns:
        解析后的绝对路径。

    Raises:
        ExtractionError: 文件不可用或不符合要求时抛出，消息可直接展示。
    """
    file_path = Path(path).expanduser()

    if not file_path.exists():
        raise ExtractionError(f"文件不存在：{file_path}")
    if not file_path.is_file():
        raise ExtractionError(f"不是普通文件：{file_path}")

    suffix = file_path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ExtractionError(
            f"不支持的文件类型：{file_path.name}（仅支持 .pdf 和 .docx）"
        )

    try:
        size = file_path.stat().st_size
    except OSError as exc:
        raise ExtractionError(f"无法读取文件：{file_path.name}（{exc}）") from exc

    if size == 0:
        raise ExtractionError(f"文件为空：{file_path.name}")
    if size > MAX_FILE_SIZE:
        limit_mb = MAX_FILE_SIZE // (1024 * 1024)
        raise ExtractionError(
            f"文件超过 {limit_mb} MB 限制："
            f"{file_path.name}（{format_file_size(size)}）"
        )

    return file_path.resolve()


def json_safe(value: Any) -> Any:
    """递归转换为可 JSON 序列化的值，避免日期等对象导致导出失败。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    return str(value)


def make_base_meta(path: Path, file_type: str) -> dict[str, Any]:
    """生成所有文件都具备的基础元信息。"""
    stat = path.stat()
    return {
        "file_name": path.name,
        "file_type": file_type,
        "file_size_bytes": stat.st_size,
        "file_size": format_file_size(stat.st_size),
        "modified_time": datetime.fromtimestamp(stat.st_mtime).isoformat(
            timespec="seconds"
        ),
    }


def build_result(
    *,
    path: Path,
    file_type: str,
    meta: dict[str, Any],
    pages: list[dict[str, Any]] | None = None,
    paragraphs: list[str] | None = None,
    tables: list[list[list[str]]] | None = None,
    stats_text: str = "",
    page_count: int | None = None,
) -> dict[str, Any]:
    """按统一结构组装提取结果。

    Args:
        path: 源文件路径。
        file_type: ``pdf`` 或 ``docx``。
        meta: 合并后的元信息。
        pages: PDF 页列表，元素为 ``{"page": 1, "text": "..."}``。
        paragraphs: Word 段落文本列表。
        tables: 三维表格结构：表格 -> 行 -> 单元格。
        stats_text: 用于计算字符数/词数的正文；不包含「无可提取文本」标记。
        page_count: 页数；Word 无法可靠取得物理页数，因此传 0。

    Returns:
        可直接 JSON 序列化的统一结果字典。
    """
    clean_pages: list[dict[str, Any]] = []
    for index, item in enumerate(pages or [], start=1):
        clean_pages.append(
            {
                "page": int(item.get("page", index)),
                "text": str(item.get("text", "")),
            }
        )

    clean_paragraphs = [
        str(item).strip() for item in (paragraphs or []) if str(item).strip()
    ]

    clean_tables: list[list[list[str]]] = []
    for table in tables or []:
        rows: list[list[str]] = []
        for row in table:
            cells = [str(cell).strip() for cell in row]
            if any(cells):
                rows.append(cells)
        if rows:
            clean_tables.append(rows)

    total_pages = len(clean_pages) if page_count is None else int(page_count)

    return {
        "filename": path.name,
        "file_type": file_type,
        "meta": json_safe(meta),
        "pages": clean_pages,
        "paragraphs": clean_paragraphs,
        "tables": clean_tables,
        "stats": {
            "char_count": len(stats_text),
            "word_count": len(_WORD_PATTERN.findall(stats_text)),
            "page_count": total_pages,
        },
    }
