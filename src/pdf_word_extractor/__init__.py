"""PDF / Word 信息提取器。

GUI 在 :mod:`pdf_word_extractor.app`，提取逻辑在
:mod:`pdf_word_extractor.extractors`。核心依赖只有 pdfplumber 与
python-docx；OCR 是可选能力，按需安装。
"""

__version__ = "2.0.0"

__all__ = ["__version__"]
