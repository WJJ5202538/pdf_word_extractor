"""表格 HTML 解析测试；不需要安装 PaddleOCR。"""

from __future__ import annotations

from pdf_word_extractor.extractors.ocr_engine import parse_table_html


def test_parse_simple_table():
    html = (
        "<html><body><table>"
        "<tr><td>Name</td><td>Dept</td><td>Note</td></tr>"
        "<tr><td>Alice</td><td>R&amp;D</td><td>A|B</td></tr>"
        "</table></body></html>"
    )

    assert parse_table_html(html) == [
        ["Name", "Dept", "Note"],
        ["Alice", "R&D", "A|B"],
    ]


def test_parse_rowspan_and_colspan_produces_rectangle():
    html = (
        "<table>"
        '<tr><th rowspan="2">A</th><th colspan="2">B</th></tr>'
        "<tr><td>C</td><td>D</td></tr>"
        '<tr><td colspan="3">E<br>F</td></tr>'
        "</table>"
    )

    assert parse_table_html(html) == [
        ["A", "B", ""],
        ["", "C", "D"],
        ["E F", "", ""],
    ]


def test_parse_table_html_tolerates_empty_and_broken_input():
    assert parse_table_html("") == []
    assert parse_table_html("<table><tr><td>only one</td></tr>") == [["only one"]]
