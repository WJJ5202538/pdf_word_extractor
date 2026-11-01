"""PDF 提取器：逐页提取文本与表格，并读取 PDF 文档属性。

文本优先使用 PDF 自带的文本层（pdfplumber），表格优先使用 pdfplumber 的
线条分析；当页面没有文本层，或页面看起来有表格但 pdfplumber 没有识别出来时，
再调用 OCR：

- 有可用 NVIDIA GPU 时，使用本地 PaddleOCR（GPU 加速）；
- 没有 GPU 时，优先调用 PaddleOCR 云端 PP-StructureV3；
- 云端不可用或没有令牌时，回退到本地 CPU PaddleOCR。

这样既保留了本地 GPU 的速度，也能让没有独显的电脑通过云端完成表格识别。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pdfplumber

from .base import (
    NO_TEXT_MARKER,
    ExtractionError,
    build_result,
    json_safe,
    make_base_meta,
)

# PDF 标准信息字典中的键 -> 统一元信息中的键
_METADATA_FIELDS = {
    "title": "title",
    "author": "author",
    "subject": "subject",
    "keywords": "keywords",
    "creator": "creator",
    "producer": "producer",
    "creationdate": "creation_date",
    "moddate": "modification_date",
}

# 进度回调：参数是一条可直接显示在状态栏的中文消息。
ProgressCallback = Callable[[str], None]


def _clean_text(value: Any) -> str:
    """清理文本中的换行符与首尾空白，保留段落内部结构。"""
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()


def _clean_cell(value: Any) -> str:
    """把表格单元格规整为单行文本。"""
    return " ".join(_clean_text(value).split())


def _notify(progress: ProgressCallback | None, message: str) -> None:
    """安全地调用进度回调，界面异常不应影响提取。"""
    if progress is None:
        return
    try:
        progress(message)
    except Exception:
        pass


def _extract_page_tables(page: Any) -> list[list[list[str]]]:
    """用 pdfplumber 提取一页中的表格，并丢弃完全空白的行/表格。"""
    raw_tables = page.extract_tables() or []
    tables: list[list[list[str]]] = []

    for raw_table in raw_tables:
        rows: list[list[str]] = []
        for raw_row in raw_table or []:
            cells = [_clean_cell(cell) for cell in (raw_row or [])]
            if any(cells):
                rows.append(cells)
        if rows:
            tables.append(rows)

    return tables


def _count_horizontal_rules(page: Any) -> int:
    """统计页面中较宽的接近水平线条/细矩形，作为表格候选信号。

    只统计宽度至少为页宽 30% 的线，避免把公式分数线、页眉页脚横线
    误判成表格。这样第 3 页那种公式页不会触发昂贵的 OCR。
    """
    count = 0
    try:
        min_width = max(120.0, float(page.width) * 0.3)
        for line in page.lines:
            x0, x1 = float(line.get("x0", 0)), float(line.get("x1", 0))
            y0, y1 = float(line.get("y0", 0)), float(line.get("y1", 0))
            if abs(y1 - y0) <= 1.5 and abs(x1 - x0) >= min_width:
                count += 1
        for rect in page.rects:
            width = float(rect.get("width", 0))
            height = float(rect.get("height", 0))
            if width >= min_width and height <= 2:
                count += 1
    except Exception:
        # 某些畸形 PDF 的 lines/rects 读取可能失败，忽略即可。
        pass
    return count


def _page_is_blank(page: Any) -> bool:
    """判断没有文本层的页面是否基本空白，避免为空白页跑 OCR。

    扫描页通常带有整页图片；文字转曲的页面会有大量曲线。只有既没有图片、
    也没有字符、图形对象又很少时，才认为是空白页（例如示例里带一个矩形框
    的第二页）。
    """
    try:
        if page.images:
            return False
        if getattr(page, "chars", None):
            return False
        drawings = (
            len(page.lines)
            + len(page.rects)
            + len(getattr(page, "curves", []) or [])
        )
        return drawings <= 2
    except Exception:
        return False


def _page_looks_like_table(page: Any, text: str) -> bool:
    """判断一页是否值得调用 OCR 做表格检测。

    论文里的三线表往往只有横线，pdfplumber 的 extract_tables() 会返回空；
    这里用较宽横线数量和“多列文本行”两个信号做保守判断，避免每页都跑
    OCR（表格模型单页约需数秒到几十秒）。
    """
    if _count_horizontal_rules(page) >= 4:
        return True

    # 没有横线但正文明显呈多列排列时，也可能是无边框表格。
    column_rows = 0
    for line in text.splitlines():
        if re.search(r"\S\s{2,}\S+\s{2,}\S", line):
            column_rows += 1
            if column_rows >= 3:
                return True
    return False


def _build_meta(pdf: Any, path: Path) -> dict[str, Any]:
    """合并基础文件信息与 PDF 文档属性。"""
    meta = make_base_meta(path, "pdf")
    raw_metadata = getattr(pdf, "metadata", None) or {}

    # pdfplumber 返回的键可能是 Title / CreationDate 等大小写形式，
    # 统一转成小写后再匹配，兼容不同版本。
    normalized: dict[str, Any] = {}
    if hasattr(raw_metadata, "items"):
        normalized = {
            str(key).lower(): value for key, value in raw_metadata.items()
        }

    for source_key, target_key in _METADATA_FIELDS.items():
        value = normalized.get(source_key)
        if value is not None and str(value).strip():
            meta[target_key] = _clean_text(value)

    # 不同 pdfplumber 版本暴露 PDF 版本的位置略有差异，做兼容读取。
    document = getattr(pdf, "doc", None)
    version = getattr(pdf, "pdf_version", None) or getattr(
        document, "pdf_version", None
    )
    if version:
        meta["pdf_version"] = str(version)

    # doc.encryption 非空表示文件带有加密字典（即使能正常打开也如实记录）。
    encryption = getattr(document, "encryption", None)
    meta["encrypted"] = bool(encryption)

    return json_safe(meta)


def _ocr_page(
    renderer: Any,
    engine: Any,
    page_index: int,
    page_number: int,
    total_pages: int,
    progress: ProgressCallback | None,
) -> dict[str, Any]:
    """渲染并识别一页，返回 PaddleOCR 的文本与表格。"""
    _notify(
        progress,
        f"PaddleOCR 正在识别第 {page_number}/{total_pages} 页（首次加载模型较慢）…",
    )
    image_path = renderer.render(page_index)
    return engine.recognize(image_path)


def _cloud_pages_map(cloud_result: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """把云端逐页结果按页码建索引。"""
    mapping: dict[int, dict[str, Any]] = {}
    for item in cloud_result.get("pages") or []:
        try:
            page_number = int(item.get("page"))
        except (TypeError, ValueError):
            continue
        mapping[page_number] = item
    return mapping


def _apply_local_ocr(
    record: dict[str, Any],
    renderer: Any,
    engine: Any,
    total_pages: int,
    progress: ProgressCallback | None,
    warnings: list[str],
    meta: dict[str, Any],
    state: dict[str, Any],
) -> bool:
    """对单页执行本地 OCR，把结果写回 record。

    Returns:
        是否真正使用了 OCR 结果。OCR 失败时返回 False，并设置 state["disabled"]。
    """
    if state.get("disabled"):
        return False
    page_number = int(record["page"])
    try:
        if renderer is None:
            from .ocr_engine import PdfPageRenderer

            renderer = PdfPageRenderer(record["path"])
        if engine is None:
            from .ocr_engine import PaddleOcrEngine

            engine = PaddleOcrEngine()
        result = _ocr_page(
            renderer,
            engine,
            page_number - 1,
            page_number,
            total_pages,
            progress,
        )
        used = False
        if not record["text"] and result.get("text"):
            record["text"] = _clean_text(result["text"])
            used = True
        if not record["pdf_tables"] and result.get("tables"):
            record["ocr_tables"] = result["tables"]
            record["table_source"] = "PaddleOCR"
            used = True
        device = str(result.get("device") or "").strip()
        if device:
            meta["ocr_device"] = device
            meta["ocr_backend"] = (
                "local-gpu" if device.startswith("gpu") else "local-cpu"
            )
        note = str(result.get("device_note") or "").strip()
        if note and not state.get("note_added"):
            warnings.append(f"OCR 设备提示：{note}")
            state["note_added"] = True
        if used:
            state["used"] = True
            state["renderer"] = renderer
            state["engine"] = engine
        return used
    except Exception as exc:
        warnings.append(f"第 {page_number} 页 PaddleOCR 识别失败：{exc}")
        state["disabled"] = True
        return False


def extract_pdf(
    path: Path, progress: ProgressCallback | None = None
) -> dict[str, Any]:
    """提取 PDF 的逐页文本、表格和元信息。

    有 GPU 时本地识别；没有 GPU 时整份 PDF 调用云端 PP-StructureV3；
    云端失败时自动回退到本地 CPU。所有 OCR 失败都会降级为 pdfplumber
    结果，不会让单个文件拖垮整个批量任务。

    Args:
        path: 已通过基础校验的 PDF 路径。
        progress: 可选进度回调，用于在界面上显示当前页/OCR 状态。

    Returns:
        统一结构的提取结果。

    Raises:
        ExtractionError: PDF 损坏、加密或无法解析时抛出。
    """
    pages: list[dict[str, Any]] = []
    tables: list[list[list[str]]] = []
    warnings: list[str] = []
    stats_parts: list[str] = []
    table_sources: set[str] = set()
    ocr_pages: list[int] = []
    renderer = None
    ocr_engine = None
    backend = ""

    try:
        with pdfplumber.open(str(path)) as pdf:
            meta = _build_meta(pdf, path)
            total_pages = len(pdf.pages)

            # 第一遍：只做 pdfplumber 的快速提取，同时判断哪些页面需要 OCR。
            records: list[dict[str, Any]] = []
            for page_number, page in enumerate(pdf.pages, start=1):
                _notify(
                    progress,
                    f"正在读取第 {page_number}/{total_pages} 页…",
                )

                try:
                    text = _clean_text(
                        page.extract_text(x_tolerance=3, y_tolerance=3)
                    )
                except Exception as exc:  # 单页文本失败不应中断整个文件
                    warnings.append(f"第 {page_number} 页文本提取失败：{exc}")
                    text = ""

                try:
                    page_tables = _extract_page_tables(page)
                except Exception as exc:  # 表格识别失败时保留已提取的文本
                    page_tables = []
                    warnings.append(f"第 {page_number} 页表格提取失败：{exc}")

                # 有文本层且 pdfplumber 已识别出表格时，不再重复跑 OCR。
                # 无文本层时先排除空白页，避免在空白页上白等一次 OCR。
                need_ocr = False
                if not page_tables:
                    if not text:
                        need_ocr = not _page_is_blank(page)
                    else:
                        need_ocr = _page_looks_like_table(page, text)

                records.append(
                    {
                        "page": page_number,
                        "path": path,
                        "text": text,
                        "pdf_tables": page_tables,
                        "ocr_tables": [],
                        "table_source": "",
                        "need_ocr": need_ocr,
                    }
                )

            need_records = [item for item in records if item["need_ocr"]]
            if need_records:
                from .cloud_ocr import (
                    CLOUD_ENGINE_NAME,
                    CloudOcrEngine,
                    choose_backend,
                )

                backend = choose_backend()
                cloud_map: dict[int, dict[str, Any]] = {}
                cloud_ok = False

                if backend == "cloud":
                    _notify(
                        progress,
                        "正在调用云端 OCR（PP-StructureV3）解析整份 PDF，请稍候…",
                    )
                    try:
                        cloud_result = CloudOcrEngine().parse_pdf(
                            path, total_pages=total_pages
                        )
                        cloud_map = _cloud_pages_map(cloud_result)
                        cloud_ok = bool(cloud_map)
                        cloud_warning = str(
                            cloud_result.get("warning") or ""
                        ).strip()
                        if cloud_warning:
                            warnings.append(cloud_warning)
                        meta["ocr_backend"] = "cloud"
                        meta["ocr_device"] = "cloud"
                        meta["ocr_engine"] = CLOUD_ENGINE_NAME
                        meta["cloud_job_id"] = str(
                            cloud_result.get("job_id") or ""
                        )
                        _notify(progress, "云端 OCR 解析完成，正在整理结果…")
                    except Exception as exc:
                        # 异常文案通常自带句号，去掉尾部标点避免出现「。；」。
                        reason = str(exc).strip().rstrip("。.;； ")
                        warnings.append(
                            f"云端 OCR 失败：{reason or '未知错误'}；已回退到本地 OCR。"
                        )
                        backend = "local"
                        cloud_ok = False

                # 云端成功时，优先用云端结果补齐没有文本层/没有表格的页面。
                if cloud_ok:
                    missing_cloud_pages: list[int] = []
                    for record in need_records:
                        page_number = int(record["page"])
                        cloud_page = cloud_map.get(page_number)
                        if cloud_page is None:
                            missing_cloud_pages.append(page_number)
                            continue
                        used = False
                        if not record["text"] and cloud_page.get("text"):
                            record["text"] = _clean_text(cloud_page["text"])
                            used = True
                        if not record["pdf_tables"] and cloud_page.get("tables"):
                            record["ocr_tables"] = cloud_page["tables"]
                            record["table_source"] = "PaddleOCR 云端"
                            used = True
                        if used:
                            ocr_pages.append(page_number)
                    if missing_cloud_pages:
                        warnings.append(
                            "云端结果缺少第 "
                            + "、".join(str(n) for n in missing_cloud_pages)
                            + " 页，已回退到本地识别。"
                        )
                        backend = "local"
                else:
                    # 没有云端结果时所有需要 OCR 的页面都走本地。
                    backend = "local"

                if backend == "local":
                    from .ocr_engine import PaddleOcrEngine, PdfPageRenderer

                    if not any(item["ocr_tables"] for item in records):
                        renderer = PdfPageRenderer(path)
                        ocr_engine = PaddleOcrEngine()
                    state: dict[str, Any] = {
                        "disabled": False,
                        "note_added": bool(meta.get("ocr_device")),
                        "used": False,
                        "renderer": renderer,
                        "engine": ocr_engine,
                    }
                    local_targets = [
                        item
                        for item in need_records
                        if not item["ocr_tables"]
                        and not (item["text"] and item["pdf_tables"])
                    ]
                    for record in local_targets:
                        used = _apply_local_ocr(
                            record,
                            state.get("renderer"),
                            state.get("engine"),
                            total_pages,
                            progress,
                            warnings,
                            meta,
                            state,
                        )
                        if used:
                            ocr_pages.append(int(record["page"]))
                            state["used"] = True
                    renderer = state.get("renderer")
                    ocr_engine = state.get("engine")

            # 第二遍：汇总文本、表格与统计；保持页码顺序。
            for record in records:
                page_number = int(record["page"])
                page_text = _clean_text(record["text"])
                if page_text:
                    stats_parts.append(page_text)
                    pages.append({"page": page_number, "text": page_text})
                else:
                    pages.append({"page": page_number, "text": NO_TEXT_MARKER})

                if record["pdf_tables"]:
                    tables.extend(record["pdf_tables"])
                    table_sources.add("pdfplumber")
                if record["ocr_tables"]:
                    tables.extend(record["ocr_tables"])
                    if record["table_source"]:
                        table_sources.add(record["table_source"])

            page_count = total_pages

    except ExtractionError:
        raise
    except Exception as exc:
        message = str(exc).lower()
        if "password" in message or "encrypted" in message:
            raise ExtractionError(
                "PDF 已加密或受密码保护，无法提取内容。"
            ) from exc
        raise ExtractionError(f"PDF 解析失败：{exc}") from exc
    finally:
        if renderer is not None:
            try:
                renderer.close()
            except Exception:
                pass

    if warnings:
        meta["warnings"] = warnings
    if tables:
        meta["table_source"] = " + ".join(sorted(table_sources))
    if ocr_pages:
        meta["ocr_pages"] = sorted(set(ocr_pages))
        if backend == "cloud":
            meta["ocr_backend"] = "cloud"
            meta["ocr_device"] = "cloud"
            meta["ocr_engine"] = CLOUD_ENGINE_NAME
        elif "ocr_engine" not in meta:
            meta["ocr_engine"] = "PaddleOCR（版面分析 + 表格结构识别）"
        meta.setdefault("ocr_device", "cpu")
        if meta.get("ocr_backend") not in ("cloud", "local-gpu", "local-cpu"):
            device = str(meta.get("ocr_device") or "")
            meta["ocr_backend"] = (
                "local-gpu" if device.startswith("gpu") else "local-cpu"
            )

    return build_result(
        path=path,
        file_type="pdf",
        meta=meta,
        pages=pages,
        paragraphs=[],
        tables=tables,
        stats_text="\n".join(stats_parts),
        page_count=page_count,
    )
