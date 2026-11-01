"""PaddleOCR 云端接口（PP-StructureV3）封装。

没有可用 NVIDIA GPU 时，程序会把整份 PDF 一次性提交到 PaddleOCR 云端，
由 PP-StructureV3 完成版面分析、表格结构识别和文字识别，再把结果映射回
本地统一结构。令牌只从环境变量或用户目录配置文件读取，不写进源码。
"""

from __future__ import annotations

import os
import re
import threading
from html import unescape
from pathlib import Path
from typing import Any

from .base import OCR_INSTALL_HINT
from .ocr_engine import parse_table_html

# 令牌优先级：环境变量 > 用户目录配置文件。
TOKEN_ENVS = ("PADDLEOCR_ACCESS_TOKEN", "PDF_WORD_OCR_TOKEN")
CONFIG_ENV = "PDF_WORD_OCR_CONFIG"
MODE_ENV = "PDF_WORD_OCR_MODE"
DEFAULT_CONFIG_PATH = Path.home() / ".pdf_word_extractor" / "config.json"
CLOUD_ENGINE_NAME = "PaddleOCR 云端（PP-StructureV3）"


class CloudOcrError(Exception):
    """云端 OCR 不可用或调用失败时抛出的、可直接展示给用户的错误。"""


def config_path() -> Path:
    """返回云端配置文件路径；可用 PDF_WORD_OCR_CONFIG 覆盖。"""
    override = os.environ.get(CONFIG_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return DEFAULT_CONFIG_PATH


def load_config() -> dict[str, Any]:
    """读取云端配置；文件不存在或损坏时返回空字典。"""
    path = config_path()
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_access_token(token: str) -> Path:
    """把 AI Studio 访问令牌保存到用户目录，并返回配置文件路径。"""
    token = (token or "").strip()
    if not token:
        raise ValueError("访问令牌不能为空。")
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = load_config()
    data["access_token"] = token
    import json

    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def resolve_token() -> str:
    """按环境变量、用户配置文件的顺序解析访问令牌。"""
    for name in TOKEN_ENVS:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    value = str(load_config().get("access_token") or "").strip()
    return value


def resolve_base_url() -> str:
    """读取可选的云端服务地址；空字符串表示使用官方默认地址。"""
    value = os.environ.get("PADDLEOCR_BASE_URL", "").strip()
    if value:
        return value
    return str(load_config().get("base_url") or "").strip()


def has_token() -> bool:
    """当前是否已配置云端访问令牌。"""
    return bool(resolve_token())


def local_gpu_available() -> bool:
    """轻量探测本机是否有 Paddle 可用的 CUDA 设备。"""
    try:
        from .ocr_engine import local_gpu_available as probe

        return bool(probe())
    except Exception:
        return False


def choose_backend() -> str:
    """选择 OCR 后端。

    返回 local 或 cloud：
    - PDF_WORD_OCR_MODE=local：强制本地；
    - PDF_WORD_OCR_MODE=cloud：强制云端；
    - auto（默认）：有 GPU 用本地 GPU；没有 GPU 但有令牌用云端；
      两者都没有时退回本地 CPU。
    """
    mode = os.environ.get(MODE_ENV, "auto").strip().lower()
    if mode in ("local", "cpu", "gpu"):
        return "local"
    if mode == "cloud":
        return "cloud"
    if local_gpu_available():
        return "local"
    if has_token():
        return "cloud"
    return "local"


def _clean_cloud_text(value: Any) -> str:
    """清理云端 OCR 文本中的多余空白。"""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()


def _text_from_blocks(pruned: dict[str, Any]) -> str:
    """从解析块中提取非表格文字，作为 overall_ocr_res 的兜底。"""
    parts: list[str] = []
    for block in pruned.get("parsing_res_list") or []:
        if not isinstance(block, dict):
            continue
        label = str(block.get("block_label") or "").lower()
        if label in ("table", "figure", "image", "formula", "chart", "seal"):
            continue
        content = str(block.get("block_content") or "").strip()
        if not content:
            continue
        content = re.sub(r"<br\s*/?>", "\n", content, flags=re.I)
        content = re.sub(r"<[^>]+>", " ", content)
        content = unescape(content)
        content = " ".join(content.split())
        if content:
            parts.append(content)
    return "\n".join(parts).strip()


def _tables_from_pruned(pruned: dict[str, Any]) -> list[list[list[str]]]:
    """把云端返回的表格 HTML 转成二维列表，并去重。"""
    tables: list[list[list[str]]] = []
    seen: set[tuple[tuple[str, ...], ...]] = set()

    candidates: list[str] = []
    for table in pruned.get("table_res_list") or []:
        if isinstance(table, dict):
            candidates.append(str(table.get("pred_html") or ""))
    # 个别页面 table_res_list 为空，但解析块里仍有 table HTML。
    if not any(candidates):
        for block in pruned.get("parsing_res_list") or []:
            if not isinstance(block, dict):
                continue
            if str(block.get("block_label") or "").lower() != "table":
                continue
            content = str(block.get("block_content") or "")
            if "<table" in content.lower():
                candidates.append(content)

    for html in candidates:
        rows = parse_table_html(html)
        if not rows:
            continue
        signature = tuple(tuple(row) for row in rows)
        if signature in seen:
            continue
        seen.add(signature)
        tables.append(rows)
    return tables


def _page_text(pruned: dict[str, Any]) -> str:
    """优先按文字框还原阅读顺序，失败时使用解析块文本。"""
    overall = pruned.get("overall_ocr_res") or {}
    try:
        from .ocr_engine import _ocr_text_from_result

        text = _clean_cloud_text(_ocr_text_from_result(overall))
        if text:
            return text
    except Exception:
        pass
    return _clean_cloud_text(_text_from_blocks(pruned))


class CloudOcrEngine:
    """惰性初始化的 PaddleOCR 云端客户端（进程内单例）。"""

    _client: Any = None
    _client_token: str = ""
    _lock = threading.Lock()

    def __init__(self, token: str | None = None) -> None:
        self._token_override = (token or "").strip()

    def _ensure_client(self) -> Any:
        """首次调用时创建客户端；令牌缺失时给出中文提示。"""
        token = self._token_override or resolve_token()
        if not token:
            raise CloudOcrError(
                "未配置云端 OCR 令牌。请设置环境变量 PADDLEOCR_ACCESS_TOKEN，"
                "或在「帮助 → 云端 OCR 设置」中填写 AI Studio 访问令牌。"
            )
        with self._lock:
            if (
                CloudOcrEngine._client is not None
                and CloudOcrEngine._client_token == token
            ):
                return CloudOcrEngine._client
            try:
                from paddleocr import PaddleOCRClient
            except Exception as exc:
                raise CloudOcrError(
                    f"PaddleOCR 云端 SDK 未安装或加载失败。请执行：{OCR_INSTALL_HINT}"
                ) from exc
            try:
                CloudOcrEngine._client = PaddleOCRClient(
                    token=token,
                    base_url=resolve_base_url() or None,
                    request_timeout=600.0,
                    poll_timeout=1800.0,
                )
                CloudOcrEngine._client_token = token
            except Exception as exc:
                raise CloudOcrError(
                    f"云端 OCR 客户端初始化失败：{exc}"
                ) from exc
            return CloudOcrEngine._client

    def parse_pdf(
        self, path: str | Path, total_pages: int | None = None
    ) -> dict[str, Any]:
        """把整份 PDF 提交到云端，返回逐页文字与表格。

        Returns:
            {"pages": [{"page": 1, "text": "...", "tables": [...]}],
             "page_count": 页数, "warning": 可选提示, "job_id": 任务号}
        """
        client = self._ensure_client()
        try:
            from paddleocr import Model, PPStructureV3Options
        except Exception as exc:
            raise CloudOcrError(
                "PaddleOCR 云端 SDK 缺少 PP-StructureV3 选项。"
            ) from exc

        page_ranges = None
        warning = ""
        if total_pages and total_pages > 100:
            page_ranges = "1-100"
            warning = (
                f"PDF 共 {total_pages} 页，云端单文件最多解析 100 页；"
                "其余页面将回退到本地识别。"
            )

        options = PPStructureV3Options(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_table_orientation_classify=False,
            use_table_recognition=True,
            use_formula_recognition=False,
            use_chart_recognition=False,
            # 注意：不要开启 use_ocr_results_with_table_cells。云端的这个选项
            # 会把跨列单元格展开成大量空列（复杂论文表格会变成 20 多列），
            # 默认的表格 HTML 反而是更紧凑的 6 列结构。
        )

        try:
            result = client.parse_document(
                model=Model.PP_STRUCTURE_V3,
                file_path=str(path),
                options=options,
                page_ranges=page_ranges,
            )
        except Exception as exc:
            raise CloudOcrError(self._friendly_error(exc)) from exc

        pages: list[dict[str, Any]] = []
        for index, page in enumerate(getattr(result, "pages", None) or [], start=1):
            pruned = getattr(page, "pruned_result", None) or {}
            if not isinstance(pruned, dict):
                pruned = {}
            text = _page_text(pruned)
            tables = _tables_from_pruned(pruned)
            pages.append({"page": index, "text": text, "tables": tables})

        if not pages:
            raise CloudOcrError("云端 OCR 返回了空结果，请稍后重试。")

        return {
            "pages": pages,
            "page_count": len(pages),
            "job_id": str(getattr(result, "job_id", "") or ""),
            "warning": warning,
        }

    @staticmethod
    def _friendly_error(exc: Exception) -> str:
        """把 SDK 异常翻译成适合界面显示的中文提示。"""
        name = type(exc).__name__
        detail = str(exc).strip()
        if name == "AuthError":
            return "云端 OCR 认证失败，请检查 AI Studio 访问令牌是否正确。"
        if name == "RateLimitError":
            return "云端 OCR 今日解析页数已达上限（429），请明天再试或申请更多页数。"
        if name in ("NetworkError", "RequestTimeoutError", "PollTimeoutError"):
            return f"云端 OCR 网络或等待超时：{detail[:300]}"
        if name == "InvalidRequestError":
            return f"云端 OCR 请求参数无效：{detail[:300]}"
        if name == "ServiceUnavailableError":
            return "云端 OCR 服务暂时不可用，请稍后重试。"
        return f"云端 OCR 调用失败：{detail[:500]}"
