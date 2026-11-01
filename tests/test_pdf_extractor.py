"""PDF 提取器测试（使用仓库内的固定样例，不依赖 OCR）。"""

from __future__ import annotations

import json
from pathlib import Path

from pdf_word_extractor.extractors import extract_file
from pdf_word_extractor.extractors.base import NO_TEXT_MARKER
from pdf_word_extractor.extractors.pdf_extractor import extract_pdf

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample.pdf"


def test_pdf_fixture_exists():
    assert FIXTURE.is_file()
    assert FIXTURE.stat().st_size > 0


def test_extract_pdf_text_tables_and_stats():
    result = extract_pdf(FIXTURE)

    assert result["filename"] == "sample.pdf"
    assert result["file_type"] == "pdf"
    assert len(result["pages"]) == 2
    assert "PDF First Page" in result["pages"][0]["text"]
    assert result["pages"][1]["text"] == NO_TEXT_MARKER

    assert len(result["tables"]) == 1
    table = result["tables"][0]
    assert table[0] == ["Name", "Dept", "Note"]
    assert table[1] == ["Alice", "R&D", "A|B"]
    assert table[2][0] == "Bob"

    assert result["meta"]["encrypted"] is False
    assert result["meta"]["file_name"] == "sample.pdf"
    assert result["stats"]["page_count"] == 2
    assert result["stats"]["char_count"] > 0
    assert result["stats"]["word_count"] > 0
    json.dumps(result, ensure_ascii=False)


def test_extract_file_dispatches_pdf():
    result = extract_file(FIXTURE)

    assert result["file_type"] == "pdf"
    assert result["stats"]["page_count"] == 2
