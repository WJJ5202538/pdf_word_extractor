"""本地环境自检：核心依赖、可选 OCR、GPU 与云端配置。

用法：
    python tools/check_env.py

只读检查，不会安装包、不会下载模型、不会打印令牌内容。
"""

from __future__ import annotations

import importlib
import os
import platform
import sys
from importlib import metadata
from pathlib import Path

CORE_MODULES = ("pdfplumber", "docx")
OCR_MODULES = ("pypdfium2", "paddleocr", "paddle", "paddlex")
PACKAGE_NAMES = {"docx": "python-docx", "paddle": "paddlepaddle"}


def _version(package: str) -> str:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return "未安装"


def _check_import(module: str) -> tuple[bool, str]:
    try:
        imported = importlib.import_module(module)
    except Exception as exc:  # 依赖缺失或二进制不兼容
        return False, f"{type(exc).__name__}: {exc}"
    version = getattr(imported, "__version__", "")
    return True, str(version or "已安装")


def _line(status: str, label: str, detail: str = "") -> None:
    suffix = f"  {detail}" if detail else ""
    print(f"[{status:<4}] {label}{suffix}")


def main() -> int:
    print("PDF / Word 信息提取器 - 环境自检")
    print("=" * 58)
    _line("INFO", "Python", f"{platform.python_version()} ({sys.executable})")
    _line("INFO", "平台", platform.platform())

    try:
        import tkinter

        _line("OK", "Tkinter", f"Tk {tkinter.TkVersion}")
    except Exception as exc:
        _line("FAIL", "Tkinter", str(exc))

    print("\n核心依赖（文本 / Word / pdfplumber 表格）")
    core_ok = True
    for module in CORE_MODULES:
        ok, detail = _check_import(module)
        if not ok:
            core_ok = False
        package = PACKAGE_NAMES.get(module, module)
        _line("OK" if ok else "FAIL", package, detail if not ok else _version(package))

    print("\n可选 OCR 依赖（扫描页与复杂表格）")
    ocr_ok = True
    for module in OCR_MODULES:
        ok, detail = _check_import(module)
        if not ok:
            ocr_ok = False
        package = PACKAGE_NAMES.get(module, module)
        _line("OK" if ok else "WARN", package, detail if not ok else _version(package))

    print("\n设备与配置")
    if ocr_ok:
        try:
            import paddle

            if paddle.device.is_compiled_with_cuda():
                count = paddle.device.cuda.device_count()
                _line("OK" if count else "WARN", "Paddle CUDA", f"检测到 {count} 个 GPU")
            else:
                _line("WARN", "Paddle CUDA", "当前为 CPU 版 PaddlePaddle")
        except Exception as exc:
            _line("WARN", "Paddle CUDA", f"探测失败：{exc}")
    else:
        _line("WARN", "Paddle CUDA", "跳过（OCR 依赖不完整）")

    try:
        from pdf_word_extractor.extractors.cloud_ocr import config_path, resolve_token

        token_ready = bool(resolve_token())
        _line("OK" if token_ready else "INFO", "云端令牌", "已配置" if token_ready else "未配置")
        _line("INFO", "配置文件", str(config_path()))
    except Exception as exc:
        _line("WARN", "云端配置", f"读取失败：{exc}")

    model_dir = Path.home() / ".paddlex" / "official_models"
    if model_dir.is_dir():
        models = [item for item in model_dir.iterdir() if item.is_dir()]
        _line("INFO", "模型缓存", f"{len(models)} 个模型，目录 {model_dir}")
    else:
        _line("INFO", "模型缓存", "尚未下载（首次 OCR 会自动下载）")

    mode = os.environ.get("PDF_WORD_OCR_MODE", "auto")
    _line("INFO", "OCR 后端模式", mode)

    print("\n结论")
    if core_ok:
        _line("OK", "核心功能可用", "可以直接运行 GUI 与导出")
    else:
        _line("FAIL", "核心依赖缺失", '请执行：python -m pip install -e .')

    if not ocr_ok:
        _line(
            "WARN",
            "OCR 未就绪",
            '需要时执行：python -m pip install -e ".[ocr]"（Python 3.10-3.13）',
        )

    return 0 if core_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
