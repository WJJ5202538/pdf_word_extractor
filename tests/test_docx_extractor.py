"""Word 提取器测试。"""

from __future__ import annotations

import json

from docx import Document

from pdf_word_extractor.extractors import extract_file
from pdf_word_extractor.extractors.docx_extractor import extract_docx


def _make_docx(path):
    document = Document()
    document.core_properties.title = "测试标题"
    document.core_properties.author = "测试作者"
    document.core_properties.subject = "单元测试"
    document.core_properties.comments = "由 pytest 生成"

    document.add_paragraph("第一段：中文内容，包含 English words 和数字 2026。")
    document.add_paragraph("第二段：用于验证段落提取。")
    document.add_paragraph("")

    table = document.add_table(rows=2, cols=3)
    values = [["姓名", "部门", "备注"], ["张三", "研发部", "A|B"]]
    for row, row_values in zip(table.rows, values, strict=True):
        for cell, value in zip(row.cells, row_values, strict=True):
            cell.text = value

    document.save(str(path))
    return path


def test_extract_docx_paragraphs_tables_and_metadata(tmp_path):
    path = _make_docx(tmp_path / "sample.docx")

    result = extract_docx(path)

    assert result["filename"] == "sample.docx"
    assert result["file_type"] == "docx"
    assert result["pages"] == []
    assert result["paragraphs"] == [
        "第一段：中文内容，包含 English words 和数字 2026。",
        "第二段：用于验证段落提取。",
    ]
    assert result["tables"] == [
        [
            ["姓名", "部门", "备注"],
            ["张三", "研发部", "A|B"],
        ]
    ]
    assert result["meta"]["title"] == "测试标题"
    assert result["meta"]["author"] == "测试作者"
    assert result["meta"]["file_name"] == "sample.docx"
    assert result["stats"]["page_count"] == 0
    assert result["stats"]["char_count"] > 0
    assert result["stats"]["word_count"] > 0
    json.dumps(result, ensure_ascii=False)


def test_extract_file_dispatches_docx(tmp_path):
    path = _make_docx(tmp_path / "dispatch.docx")

    result = extract_file(path)

    assert result["file_type"] == "docx"
    assert result["paragraphs"][0].startswith("第一段")
