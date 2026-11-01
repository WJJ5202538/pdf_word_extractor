"""Markdown 渲染测试（不导入 Tkinter）。"""

from __future__ import annotations

from pdf_word_extractor.exporters import build_markdown, display_meta_value


def _sample_data():
    return {
        "filename": "report.pdf",
        "file_type": "pdf",
        "meta": {"title": "年度报告", "author": "张三", "encrypted": False},
        "pages": [
            {"page": 1, "text": "第一页正文"},
            {"page": 2, "text": "无可提取文本"},
        ],
        "paragraphs": [],
        "tables": [[["名称", "值"], ["A|B", ""]]],
        "stats": {"char_count": 5, "word_count": 3, "page_count": 2},
    }


def test_build_markdown_contains_sections_and_page_headings():
    markdown = build_markdown(_sample_data())

    assert markdown.startswith("# report.pdf")
    assert "- **文件类型**：PDF" in markdown
    assert "- **标题**：年度报告" in markdown
    assert "### 第 1 页" in markdown
    assert "### 第 2 页" in markdown
    assert "_（本页无可提取文本）_" in markdown
    assert "## 表格" in markdown
    assert "| 列 1 | 列 2 |" in markdown
    assert "| 名称 | 值 |" in markdown
    assert "A\\|B" in markdown
    assert "- 页数：2" in markdown


def test_build_markdown_word_uses_dash_for_page_count():
    data = _sample_data()
    data["filename"] = "report.docx"
    data["file_type"] = "docx"
    data["pages"] = []
    data["paragraphs"] = ["第一段", "第二段"]
    data["tables"] = []
    data["stats"] = {"char_count": 6, "word_count": 2, "page_count": 0}

    markdown = build_markdown(data)

    assert "第一段" in markdown
    assert "第二段" in markdown
    assert "- 页数：—" in markdown
    assert "## 表格" not in markdown


def test_display_meta_value_variants():
    assert display_meta_value(None) == "—"
    assert display_meta_value(True) == "是"
    assert display_meta_value(False) == "否"
    assert display_meta_value(1234567) == "1,234,567"
    assert display_meta_value(["a", "b"]) == "a；b"
    assert display_meta_value({"k": "v"}) == '{"k": "v"}'
