"""PDF / Word 提取器包：对外暴露统一入口与公共常量。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .base import (
    CORE_INSTALL_HINT,
    MAX_FILE_SIZE,
    NO_TEXT_MARKER,
    OCR_INSTALL_HINT,
    SUPPORTED_EXTENSIONS,
    ExtractionError,
    build_result,
    format_file_size,
    json_safe,
    make_base_meta,
    validate_file,
)

__all__ = [
    "CORE_INSTALL_HINT",
    "MAX_FILE_SIZE",
    "NO_TEXT_MARKER",
    "OCR_INSTALL_HINT",
    "SUPPORTED_EXTENSIONS",
    "ExtractionError",
    "build_result",
    "extract_file",
    "format_file_size",
    "json_safe",
    "make_base_meta",
    "validate_file",
]


def extract_file(
    path: str | Path,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """校验文件并按扩展名分派到 PDF 或 Word 提取器。

    这里采用延迟导入：即使用户暂时没有安装 pdfplumber / paddleocr，
    程序界面仍能正常启动，并在提取时给出友好的依赖提示。

    Args:
        path: 待提取的文件路径。
        progress: 可选的进度回调；目前 PDF 提取会使用，Word 提取忽略。
    """
    file_path = validate_file(path)
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        try:
            from .pdf_extractor import extract_pdf
        except ImportError as exc:
            raise ExtractionError(
                f"缺少 PDF 提取依赖，请执行：{CORE_INSTALL_HINT}"
            ) from exc

        return extract_pdf(file_path, progress=progress)

    if suffix == ".docx":
        try:
            from .docx_extractor import extract_docx
        except ImportError as exc:
            raise ExtractionError(
                f"缺少 Word 提取依赖，请执行：{CORE_INSTALL_HINT}"
            ) from exc

        return extract_docx(file_path)

    # validate_file 已经拦截未知类型，这里仅作为防御性兜底。
    raise ExtractionError(f"不支持的文件类型：{file_path.name}")