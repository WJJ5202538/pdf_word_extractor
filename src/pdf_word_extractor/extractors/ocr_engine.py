"""PaddleOCR 封装：识别 PDF 页面中的表格与扫描页文字。

PaddleOCR 3.x 的表格识别流水线会同时完成版面分析、表格结构还原和文字识别，
因此同一个页面只需要推理一次，就能拿到表格 HTML 与整页 OCR 文本。
"""

from __future__ import annotations

import os
import tempfile
import threading
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .base import OCR_INSTALL_HINT

# PaddleX 默认从 HuggingFace 下载模型；国内网络经常超时。
# 在导入 paddleocr 之前设置默认值，用户也可以用同名环境变量覆盖。
os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "modelscope")
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


class OcrError(Exception):
    """PaddleOCR 不可用或识别失败时抛出的、可直接展示给用户的错误。"""


class _TableHTMLParser(HTMLParser):
    """把 PaddleOCR 输出的表格 HTML 拆成带 rowspan/colspan 的单元格。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[dict[str, Any]]] = []
        self._row: list[dict[str, Any]] | None = None
        self._cell: dict[str, Any] | None = None

    @staticmethod
    def _span(attrs: list[tuple[str, str | None]], name: str) -> int:
        """读取并限制 rowspan / colspan，防止异常 HTML 撑爆内存。"""
        for key, value in attrs:
            if key.lower() != name:
                continue
            try:
                return max(1, min(20, int(value or 1)))
            except (TypeError, ValueError):
                return 1
        return 1

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        tag = tag.lower()
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = {
                "parts": [],
                "rowspan": self._span(attrs, "rowspan"),
                "colspan": self._span(attrs, "colspan"),
            }
        elif tag == "br" and self._cell is not None:
            self._cell["parts"].append(" ")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell["parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in ("td", "th") and self._cell is not None:
            if self._row is None:
                self._row = []
            self._row.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def _clean_cell_text(value: Any) -> str:
    """把单元格内容压成一行，保留中文和英文之间的可读间距。"""
    text = " ".join(str(value or "").split())
    return text.strip()


def parse_table_html(html: str) -> list[list[str]]:
    """把 PaddleOCR 的 pred_html 转成二维字符串列表。

    支持 rowspan / colspan：合并单元格会在其覆盖的每个格子里保留空位，
    保证输出始终是矩形，方便 Treeview 与 Markdown 渲染。
    """
    parser = _TableHTMLParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        return []

    if not parser.rows:
        return []

    # 先按 rowspan/colspan 计算真实网格，再把稀疏网格补成矩形。
    grid: list[list[str | None]] = []
    for row_index, cells in enumerate(parser.rows):
        while len(grid) <= row_index:
            grid.append([])
        column = 0
        for cell in cells:
            row = grid[row_index]
            while column < len(row) and row[column] is not None:
                column += 1
            text = _clean_cell_text("".join(cell["parts"]))
            rowspan = int(cell["rowspan"])
            colspan = int(cell["colspan"])
            for offset_row in range(rowspan):
                target_row = row_index + offset_row
                while len(grid) <= target_row:
                    grid.append([])
                target = grid[target_row]
                needed = column + colspan
                if len(target) < needed:
                    target.extend([None] * (needed - len(target)))
                for offset_col in range(colspan):
                    # 合并区域只在左上角保留文字，其余位置留空，
                    # 避免同一段文字在相邻格子里重复出现。
                    target[column + offset_col] = (
                        text if offset_row == 0 and offset_col == 0 else ""
                    )
            column += colspan

    width = max((len(row) for row in grid), default=0)
    if width == 0:
        return []

    rows: list[list[str]] = []
    for row in grid:
        padded = [str(cell or "").strip() for cell in row]
        padded.extend([""] * (width - len(padded)))
        if any(padded):
            rows.append(padded)
    return rows


def _is_ascii_word(char: str) -> bool:
    """判断字符是否适合用空格连接（英文/数字）。"""
    return bool(char) and char.isascii() and (char.isalnum() or char in "'-")


def _join_line(words: list[str]) -> str:
    """把同一 OCR 行里的文本块拼起来，中文之间不额外加空格。"""
    result = ""
    for word in words:
        word = word.strip()
        if not word:
            continue
        if not result:
            result = word
        elif _is_ascii_word(result[-1]) and _is_ascii_word(word[0]):
            result += " " + word
        else:
            result += word
    return result


def _ocr_text_from_result(overall: dict[str, Any]) -> str:
    """按文字框的纵坐标把 OCR 结果还原成多行文本。"""
    texts = [str(item).strip() for item in (overall.get("rec_texts") or [])]
    boxes = overall.get("rec_boxes") or []
    entries: list[tuple[float, float, str]] = []
    for index, text in enumerate(texts):
        if not text:
            continue
        box = boxes[index] if index < len(boxes) else None
        try:
            x0, y0, x1, y1 = (float(value) for value in box[:4])
        except (TypeError, ValueError, IndexError):
            # 没有坐标时退化为一行一个文本块，至少不丢内容。
            entries.append((float(index), 0.0, text))
            continue
        entries.append(((y0 + y1) / 2, x0, text))

    if not entries:
        return ""

    entries.sort(key=lambda item: (item[0], item[1]))
    # 论文正文行高在 150 DPI 下约为 20-30px，12px 的容差足以合并同一行。
    tolerance = 12.0
    lines: list[list[tuple[float, float, str]]] = []
    for entry in entries:
        if not lines or abs(entry[0] - lines[-1][0][0]) > tolerance:
            lines.append([entry])
        else:
            lines[-1].append(entry)

    output: list[str] = []
    for line in lines:
        line.sort(key=lambda item: item[1])
        joined = _join_line([item[2] for item in line])
        if joined:
            output.append(joined)
    return "\n".join(output)


# 用户可以用环境变量覆盖 OCR 设备：auto（默认）/ cpu / gpu / gpu:0
DEVICE_ENV = "PDF_WORD_OCR_DEVICE"


def _normalize_device(value: str | None) -> str:
    """把用户输入统一成 PaddleOCR 能识别的设备字符串。"""
    text = (value or "auto").strip().lower()
    if text in ("", "auto"):
        return "auto"
    if text == "gpu":
        return "gpu:0"
    if text == "cpu":
        return "cpu"
    if text.startswith("gpu:") and text[4:].isdigit():
        return text
    return "auto"


def _cuda_device_count() -> int:
    """探测当前 Paddle 是否为 GPU 版、以及可见的 CUDA 设备数。

    这里只做轻量查询；如果驱动不兼容或 CUDA 初始化失败，按“没有 GPU”
    处理，让上层回退到 CPU，而不是让整个提取流程崩掉。
    """
    try:
        import paddle

        if not paddle.device.is_compiled_with_cuda():
            return 0
        return int(paddle.device.cuda.device_count())
    except Exception:
        return 0


def local_gpu_available() -> bool:
    """公开的 GPU 探测入口：供云端/本地后端路由使用。"""
    return _cuda_device_count() > 0


def _device_candidates(preferred: str) -> list[str]:
    """按优先级返回要尝试的设备列表，最后一项通常是 CPU 兜底。"""
    if preferred == "cpu":
        return ["cpu"]
    if preferred == "auto":
        return ["gpu:0", "cpu"] if _cuda_device_count() > 0 else ["cpu"]
    # 用户显式指定了 gpu:N：即使探测失败也先试一次，失败后再回退 CPU。
    return [preferred, "cpu"] if preferred.startswith("gpu:") else [preferred]


class PaddleOcrEngine:
    """惰性初始化的 PaddleOCR 表格流水线（进程内单例）。

    GPU 版 PaddlePaddle 默认会使用 CUDA；CPU 版则必须显式指定 cpu。
    本类在初始化时按 auto -> gpu:0 -> cpu 的顺序尝试，并在 GPU 不可用时
    自动回退，保证同一套程序在两种环境里都能运行。
    """

    _pipeline: Any = None
    _active_device: str | None = None
    _device_warning: str = ""
    _lock = threading.Lock()

    def __init__(self, device: str | None = None) -> None:
        self._local_lock = self._lock
        self._requested_device = _normalize_device(
            device or os.environ.get(DEVICE_ENV) or "auto"
        )

    @property
    def active_device(self) -> str:
        """当前真正使用的设备；尚未初始化时返回请求的设备。"""
        return PaddleOcrEngine._active_device or self._requested_device

    @property
    def device_note(self) -> str:
        """给界面/元信息展示的设备说明，包含回退原因。"""
        return PaddleOcrEngine._device_warning

    def _ensure_pipeline(self) -> Any:
        """首次调用时加载模型；模型加载可能需要十几秒。"""
        with self._local_lock:
            if PaddleOcrEngine._pipeline is not None:
                return PaddleOcrEngine._pipeline
            try:
                from paddleocr import TableRecognitionPipelineV2
            except Exception as exc:  # pragma: no cover - 取决于本机依赖
                raise OcrError(
                    f"PaddleOCR 未安装或加载失败。请执行：{OCR_INSTALL_HINT}"
                ) from exc

            candidates = _device_candidates(self._requested_device)
            errors: list[str] = []
            for device in candidates:
                try:
                    pipeline = TableRecognitionPipelineV2(
                        device=device,
                        # 关闭方向分类与去扭曲：论文/文档页面通常是正向的，
                        # 这两个模型会额外增加初始化时间。
                        use_doc_orientation_classify=False,
                        use_doc_unwarping=False,
                        # Paddle 3.3.1 在 CPU 上默认启用 oneDNN，部分模型会报
                        # ConvertPirAttribute2RuntimeAttribute 错误，必须关闭。
                        # GPU 路径同样传入，PaddleOCR 会忽略该 CPU 选项。
                        enable_mkldnn=False,
                        layout_detection_model_name="PP-DocLayout-L",
                        wired_table_structure_recognition_model_name="SLANet_plus",
                        wireless_table_structure_recognition_model_name="SLANet_plus",
                        text_detection_model_name="PP-OCRv5_mobile_det",
                        text_recognition_model_name="PP-OCRv5_mobile_rec",
                    )
                except Exception as exc:
                    errors.append(f"{device}: {exc}")
                    continue

                PaddleOcrEngine._pipeline = pipeline
                PaddleOcrEngine._active_device = device
                if device != candidates[0]:
                    PaddleOcrEngine._device_warning = (
                        f"已从 {candidates[0]} 自动回退到 {device}；"
                        f"原因：{errors[-1] if errors else '设备不可用'}"
                    )
                else:
                    PaddleOcrEngine._device_warning = ""
                return pipeline

            raise OcrError(
                "PaddleOCR 初始化失败：" + "；".join(errors or ["未知错误"])
            )

    def _fallback_to_cpu(self) -> bool:
        """GPU 推理失败时丢弃流水线，下一次调用改用 CPU 重新初始化。"""
        with self._local_lock:
            if PaddleOcrEngine._active_device == "cpu":
                return False
            PaddleOcrEngine._pipeline = None
            PaddleOcrEngine._active_device = None
            self._requested_device = "cpu"
            return True

    def recognize(self, image_path: str | Path) -> dict[str, Any]:
        """识别一张页面图片。

        Returns:
            {"text": 整页文本, "tables": 表格二维列表, "table_count": 表格数,
             "device": 实际设备, "device_note": 设备回退说明}。
        """
        pipeline = self._ensure_pipeline()
        device = self.active_device
        try:
            results = pipeline.predict(str(image_path))
        except Exception as exc:
            # GPU 显存不足、驱动异常等情况下自动切回 CPU 再试一次。
            if device.startswith("gpu") and self._fallback_to_cpu():
                warning = f"GPU 识别失败，已自动回退 CPU：{exc}"
                pipeline = self._ensure_pipeline()
                try:
                    results = pipeline.predict(str(image_path))
                except Exception as exc2:
                    raise OcrError(f"PaddleOCR 识别失败（GPU/CPU 均失败）：{exc2}") from exc2
                PaddleOcrEngine._device_warning = warning
            else:
                raise OcrError(f"PaddleOCR 识别失败：{exc}") from exc

        texts: list[str] = []
        tables: list[list[list[str]]] = []
        seen_tables: set[tuple[tuple[str, ...], ...]] = set()

        for item in results or []:
            payload = getattr(item, "json", None)
            if isinstance(payload, dict) and "res" in payload:
                payload = payload["res"]
            if not isinstance(payload, dict):
                continue

            overall = payload.get("overall_ocr_res") or {}
            page_text = _ocr_text_from_result(overall)
            if page_text:
                texts.append(page_text)

            for table in payload.get("table_res_list") or []:
                rows = parse_table_html(str(table.get("pred_html", "")))
                if not rows:
                    continue
                signature = tuple(tuple(row) for row in rows)
                if signature in seen_tables:
                    continue
                seen_tables.add(signature)
                tables.append(rows)

        return {
            "text": "\n".join(texts).strip(),
            "tables": tables,
            "table_count": len(tables),
            "device": self.active_device,
            "device_note": self.device_note,
        }


class PdfPageRenderer:
    """用 pypdfium2 把 PDF 页面渲染成临时 PNG，供 PaddleOCR 读取。"""

    def __init__(self, path: str | Path, dpi: int = 150) -> None:
        try:
            import pypdfium2 as pdfium
        except Exception as exc:  # pragma: no cover
            raise OcrError("缺少 pypdfium2，无法渲染 PDF 页面。") from exc

        self._pdfium = pdfium
        self._dpi = dpi
        self._doc = pdfium.PdfDocument(str(path))
        self._tempdir = tempfile.TemporaryDirectory(prefix="pdf_ocr_")

    def render(self, page_index: int) -> Path:
        """渲染指定页（从 0 开始），返回临时 PNG 路径。"""
        try:
            page = self._doc[page_index]
            bitmap = page.render(scale=self._dpi / 72.0)
            image = bitmap.to_pil()
            output = Path(self._tempdir.name) / f"page_{page_index + 1}.png"
            image.save(output, format="PNG")
            bitmap.close()
            page.close()
            return output
        except Exception as exc:
            raise OcrError(f"PDF 第 {page_index + 1} 页渲染失败：{exc}") from exc

    def close(self) -> None:
        """释放 PDF 句柄和临时目录。"""
        try:
            self._doc.close()
        except Exception:
            pass
        self._tempdir.cleanup()

    def __enter__(self) -> PdfPageRenderer:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
