"""界面与导出共用的中文标签定义。

集中放在这里，避免 GUI 与 Markdown 导出各维护一份翻译表。
"""

from __future__ import annotations

FILE_TYPE_LABELS = {"pdf": "PDF", "docx": "Word"}

STATUS_LABELS = {
    "pending": "等待",
    "processing": "提取中",
    "done": "完成",
    "error": "失败",
}

META_LABELS = {
    "file_name": "文件名",
    "file_type": "文件类型",
    "file_size_bytes": "文件大小（字节）",
    "file_size": "文件大小",
    "modified_time": "文件修改时间",
    "title": "标题",
    "author": "作者",
    "subject": "主题",
    "keywords": "关键词",
    "creator": "创建程序",
    "producer": "生成程序",
    "creation_date": "创建时间",
    "modification_date": "文档修改时间",
    "pdf_version": "PDF 版本",
    "encrypted": "是否加密",
    "table_source": "表格来源",
    "ocr_pages": "OCR 页码",
    "ocr_engine": "OCR 引擎",
    "ocr_device": "OCR 设备",
    "ocr_backend": "OCR 后端",
    "cloud_job_id": "云端任务号",
    "warnings": "提取警告",
    "last_modified_by": "最后修改者",
    "created": "创建时间（文档）",
    "modified": "修改时间（文档）",
    "revision": "修订号",
    "category": "类别",
    "comments": "备注",
    "language": "语言",
    "identifier": "标识符",
    "version": "版本",
    "content_status": "内容状态",
    "last_printed": "最后打印时间",
}

# 这些键已经在「文件路径 / 大小」等基础行中展示，元信息表里不再重复。
BASE_META_KEYS = {
    "file_name",
    "file_type",
    "file_size_bytes",
    "file_size",
    "modified_time",
}
