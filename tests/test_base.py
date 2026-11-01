"""文件校验、统一结构与统计的单元测试。"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from pdf_word_extractor.extractors.base import (
    MAX_FILE_SIZE,
    NO_TEXT_MARKER,
    ExtractionError,
    build_result,
    format_file_size,
    json_safe,
    validate_file,
)


def test_validate_file_returns_absolute_path(tmp_path):
    target = tmp_path / "demo.pdf"
    target.write_bytes(b"%PDF-1.4 tiny")

    assert validate_file(target) == target.resolve()
    assert validate_file(str(target)) == target.resolve()


def test_validate_file_rejects_missing_file(tmp_path):
    with pytest.raises(ExtractionError, match="文件不存在"):
        validate_file(tmp_path / "missing.pdf")


def test_validate_file_rejects_directory(tmp_path):
    with pytest.raises(ExtractionError, match="不是普通文件"):
        validate_file(tmp_path)


def test_validate_file_rejects_unsupported_suffix(tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("hello", encoding="utf-8")

    with pytest.raises(ExtractionError, match="不支持的文件类型"):
        validate_file(target)


def test_validate_file_rejects_empty_file(tmp_path):
    target = tmp_path / "empty.pdf"
    target.write_bytes(b"")

    with pytest.raises(ExtractionError, match="文件为空"):
        validate_file(target)


def test_validate_file_rejects_oversized_file(tmp_path):
    target = tmp_path / "big.pdf"
    with target.open("wb") as handle:
        handle.truncate(MAX_FILE_SIZE + 1)

    with pytest.raises(ExtractionError, match="20 MB"):
        validate_file(target)


def test_build_result_cleans_pages_and_tables(tmp_path):
    target = tmp_path / "demo.pdf"
    target.write_bytes(b"x")

    result = build_result(
        path=target,
        file_type="pdf",
        meta={"title": "示例"},
        pages=[{"page": 1, "text": "你好 world"}, {"page": 2, "text": ""}],
        tables=[[[" a ", ""], ["", ""]], [["", ""]]],
        stats_text="你好 world",
        page_count=2,
    )

    assert result["filename"] == "demo.pdf"
    assert result["file_type"] == "pdf"
    assert result["pages"] == [
        {"page": 1, "text": "你好 world"},
        {"page": 2, "text": ""},
    ]
    assert result["tables"] == [[["a", ""]]]
    assert result["stats"] == {
        "char_count": 8,
        "word_count": 3,  # 中文按字计数：你、好、world
        "page_count": 2,
    }
    assert json.loads(json.dumps(result, ensure_ascii=False)) == result


def test_build_result_defaults_page_count_to_pages_length(tmp_path):
    target = tmp_path / "demo.pdf"
    target.write_bytes(b"x")

    result = build_result(
        path=target,
        file_type="pdf",
        meta={},
        pages=[{"page": 1, "text": NO_TEXT_MARKER}],
        stats_text="",
    )

    assert result["stats"]["page_count"] == 1
    assert result["stats"]["char_count"] == 0


def test_json_safe_serializes_dates_and_sets():
    value = {
        "created": datetime(2026, 1, 2, 3, 4, 5),
        "tags": {"b", "a"},
        "nested": [{"ok": True}],
    }

    converted = json_safe(value)
    assert converted["created"] == "2026-01-02 03:04:05"
    assert set(converted["tags"]) == {"a", "b"}
    assert converted["nested"] == [{"ok": True}]
    json.dumps(converted, ensure_ascii=False)


def test_format_file_size():
    assert format_file_size(0) == "0 B"
    assert format_file_size(1536) == "1.5 KB"
    assert format_file_size(2 * 1024 * 1024) == "2.0 MB"
