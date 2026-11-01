"""包元数据测试。"""

from __future__ import annotations

import pdf_word_extractor


def test_package_version_is_exposed():
    assert pdf_word_extractor.__version__ == "2.0.0"
