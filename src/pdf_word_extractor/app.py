"""PDF / Word 信息提取器 —— Tkinter 桌面程序入口。

功能：
1. 通过文件对话框一次选择多个 .pdf / .docx 文件；
2. 在后台线程逐页/逐段提取文本、表格与元信息；
3. 右侧用「文本 / 表格 / 元信息」三个标签页展示结果；
4. 把当前文件的结果导出为 JSON 或 Markdown。

依赖：pdfplumber / PaddleOCR（PDF）、python-docx（Word）。
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import threading
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter import font as tkfont
from tkinter.scrolledtext import ScrolledText
from typing import Any

from . import __version__
from .exporters import build_markdown, display_meta_value
from .extractors import (
    CORE_INSTALL_HINT,
    MAX_FILE_SIZE,
    NO_TEXT_MARKER,
    ExtractionError,
    extract_file,
    format_file_size,
    validate_file,
)
from .labels import BASE_META_KEYS, FILE_TYPE_LABELS, META_LABELS, STATUS_LABELS

APP_TITLE = "PDF / Word 信息提取器"
APP_VERSION = __version__
NEWLINE = "\n"

# 界面配色
BG = "#eef2f7"
CARD = "#ffffff"
TEXT = "#1f2937"
MUTED = "#6b7280"
ACCENT = "#2563eb"
ACCENT_DARK = "#1d4ed8"
ERROR = "#b91c1c"
SUCCESS = "#15803d"
WARNING = "#b45309"
BORDER = "#dbe2ea"


@dataclass
class FileRecord:
    """文件列表中的一项及其最新提取状态。"""

    job_id: int
    path: Path
    status: str = "pending"  # pending / processing / done / error
    data: dict[str, Any] | None = None
    error: str = ""
    progress_message: str = ""


def choose_font_family(root: tk.Misc) -> str:
    """优先选择一个能正常显示中文的系统字体。"""
    preferred = (
        "Microsoft YaHei UI",
        "Microsoft YaHei",
        "PingFang SC",
        "Noto Sans CJK SC",
        "Source Han Sans SC",
        "Segoe UI",
    )
    available = set(tkfont.families(root))
    for family in preferred:
        if family in available:
            return family
    return tkfont.nametofont("TkDefaultFont").actual("family")


class ExtractionWorker:
    """单个后台线程，按提交顺序提取文件，结果用 queue 回传主线程。"""

    def __init__(self, results: queue.Queue[tuple[str, int, Any, str]]) -> None:
        self._results = results
        self._tasks: queue.Queue[tuple[int, Path] | None] = queue.Queue()
        self._thread = threading.Thread(
            target=self._run,
            name="extraction-worker",
            daemon=True,
        )
        self._thread.start()

    def submit(self, job_id: int, path: Path) -> None:
        """加入一个提取任务。"""
        self._tasks.put((job_id, path))

    def stop(self) -> None:
        """通知工作线程退出；正在执行的任务会自然结束。"""
        self._tasks.put(None)

    def _run(self) -> None:
        while True:
            task = self._tasks.get()
            if task is None:
                return
            job_id, path = task
            self._results.put(("start", job_id, None, ""))

            def on_progress(message: str, job_id: int = job_id) -> None:
                """把提取器内部的页码/OCR 进度转交给主线程。"""
                self._results.put(("progress", job_id, message, ""))

            try:
                data = extract_file(path, progress=on_progress)
            except ExtractionError as exc:
                self._results.put(("error", job_id, None, str(exc)))
            except ModuleNotFoundError as exc:
                missing = exc.name or "未知依赖"
                message = (
                    f"缺少依赖：{missing}。{NEWLINE}{NEWLINE}"
                    "请先在项目目录执行："
                    f"{NEWLINE}{CORE_INSTALL_HINT}"
                )
                self._results.put(("error", job_id, None, message))
            except Exception as exc:  # 单个文件的任何异常都不应终止后台线程
                self._results.put(
                    ("error", job_id, None, f"未预期的提取错误：{exc}")
                )
            else:
                self._results.put(("done", job_id, data, ""))


class PDFWordExtractorApp:
    """主窗口控制器：负责界面、文件队列与结果展示。"""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.font_family = choose_font_family(root)
        self.records: list[FileRecord] = []
        self._record_by_job: dict[int, FileRecord] = {}
        self._path_keys: set[str] = set()
        self._next_job_id = 1
        self.current_index: int | None = None
        self._tables: list[list[list[str]]] = []
        self._progress_on = False
        self._closed = False

        self.result_queue: queue.Queue[tuple[str, int, Any, str]] = queue.Queue()
        self.worker = ExtractionWorker(self.result_queue)

        self._configure_window()
        self._configure_styles()
        self._build_menu()
        self._build_ui()
        self._bind_events()

        self._show_empty()
        self._refresh_list()
        self._refresh_activity()
        self.root.after(100, self._poll_worker)
        self.root.after(60, self._set_initial_sash)

    # ------------------------------------------------------------------
    # 窗口、样式和菜单
    # ------------------------------------------------------------------
    def _configure_window(self) -> None:
        """设置标题、最小尺寸并让窗口在屏幕上居中。"""
        self.root.title(f"{APP_TITLE} · v{APP_VERSION}")
        self.root.configure(bg=BG)
        self.root.minsize(980, 640)
        self.root.option_add("*tearOff", False)

        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        width = min(1220, max(980, screen_width - 120))
        height = min(820, max(660, screen_height - 140))
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 3)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _configure_styles(self) -> None:
        """统一 ttk 字体、按钮、标签页和表格样式。"""
        family = self.font_family
        for named_font in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
            try:
                tkfont.nametofont(named_font).configure(family=family, size=10)
            except tk.TclError:
                pass

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", font=(family, 10), background=BG, foreground=TEXT)
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure("Card.TLabel", background=CARD, foreground=TEXT)
        style.configure("CardMuted.TLabel", background=CARD, foreground=MUTED)
        style.configure(
            "Title.TLabel",
            background=BG,
            foreground=TEXT,
            font=(family, 20, "bold"),
        )
        style.configure(
            "Subtitle.TLabel",
            background=BG,
            foreground=MUTED,
            font=(family, 10),
        )
        style.configure(
            "Badge.TLabel",
            background="#e8f0fe",
            foreground=ACCENT,
            font=(family, 9, "bold"),
            padding=(10, 5),
        )
        style.configure(
            "CardTitle.TLabel",
            background=CARD,
            foreground=TEXT,
            font=(family, 12, "bold"),
        )
        # 左侧列表标题直接放在页面底色上，避免出现白色文字块。
        style.configure(
            "SideTitle.TLabel",
            background=BG,
            foreground=TEXT,
            font=(family, 11, "bold"),
        )
        style.configure("TButton", padding=(12, 6))
        style.configure(
            "Accent.TButton",
            background=ACCENT,
            foreground="#ffffff",
            font=(family, 10, "bold"),
            padding=(16, 8),
            borderwidth=0,
        )
        style.map(
            "Accent.TButton",
            background=[
                ("disabled", "#93c5fd"),
                ("pressed", ACCENT_DARK),
                ("active", ACCENT_DARK),
            ],
            foreground=[("disabled", "#eff6ff")],
        )
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            padding=(20, 9),
            font=(family, 10, "bold"),
            background="#e3e9f1",
            foreground=MUTED,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", CARD), ("active", "#eef4ff")],
            foreground=[("selected", ACCENT), ("active", TEXT)],
        )
        style.configure(
            "Treeview",
            background=CARD,
            fieldbackground=CARD,
            foreground=TEXT,
            rowheight=29,
            borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            background="#f3f6fa",
            foreground="#374151",
            relief="flat",
            padding=(6, 8),
            font=(family, 10, "bold"),
        )
        style.map(
            "Treeview",
            background=[("selected", "#dbeafe")],
            foreground=[("selected", TEXT)],
        )
        style.configure("Status.TFrame", background=CARD)
        style.configure("Status.TLabel", background=CARD, foreground=TEXT)
        style.configure("StatusMuted.TLabel", background=CARD, foreground=MUTED)

    def _build_menu(self) -> None:
        """创建文件菜单和帮助菜单。"""
        menubar = tk.Menu(self.root)
        self.file_menu = tk.Menu(menubar)
        self.file_menu.add_command(
            label="选择文件…",
            accelerator="Ctrl+O",
            command=self.choose_files,
        )
        self.file_menu.add_separator()
        self.file_menu.add_command(
            label="导出 JSON…",
            accelerator="Ctrl+E",
            command=self.export_json,
        )
        self.file_menu.add_command(
            label="导出 Markdown…",
            accelerator="Ctrl+Shift+E",
            command=self.export_markdown,
        )
        self.file_menu.add_separator()
        self.file_menu.add_command(label="清空列表", command=self.clear_files)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="退出", command=self._on_close)
        menubar.add_cascade(label="文件", menu=self.file_menu)

        help_menu = tk.Menu(menubar)
        help_menu.add_command(label="云端 OCR 设置…", command=self._configure_cloud_ocr)
        help_menu.add_command(label="使用说明", command=self._show_usage)
        help_menu.add_command(label="关于", command=self._show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)
        self.root.config(menu=menubar)

    # ------------------------------------------------------------------
    # 界面布局
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        """搭建标题、工具栏、左右分栏与状态栏。"""
        # 状态栏先占住底部，避免窗口缩小时被内容挤出。
        status = ttk.Frame(self.root, style="Status.TFrame", padding=(18, 8))
        status.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_label = ttk.Label(
            status,
            text="就绪",
            style="Status.TLabel",
            anchor="w",
        )
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.stats_label = ttk.Label(
            status,
            text="—",
            style="StatusMuted.TLabel",
        )
        self.stats_label.pack(side=tk.RIGHT)
        self.progress = ttk.Progressbar(
            status,
            mode="indeterminate",
            length=90,
        )
        # 空闲时不显示进度条，避免状态栏出现灰色空槽。
        self.progress.pack_forget()
        ttk.Separator(self.root).pack(side=tk.BOTTOM, fill=tk.X)

        outer = ttk.Frame(self.root, padding=(24, 18, 24, 0))
        outer.pack(fill=tk.BOTH, expand=True)

        # 标题区
        header = ttk.Frame(outer)
        header.pack(fill=tk.X)
        title_box = ttk.Frame(header)
        title_box.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(title_box, text=APP_TITLE, style="Title.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            title_box,
            text="选择 PDF 或 Word 文件，自动提取正文、表格和元信息",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(3, 0))
        ttk.Label(
            header,
            text="单文件 ≤ 20 MB",
            style="Badge.TLabel",
        ).pack(side=tk.RIGHT, anchor="n")

        # 工具栏
        toolbar = ttk.Frame(outer)
        toolbar.pack(fill=tk.X, pady=(14, 12))
        self.choose_button = ttk.Button(
            toolbar,
            text="选择文件",
            style="Accent.TButton",
            command=self.choose_files,
        )
        self.choose_button.pack(side=tk.LEFT)
        ttk.Button(
            toolbar,
            text="清空列表",
            command=self.clear_files,
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(
            side=tk.LEFT, fill=tk.Y, padx=12, pady=2
        )
        self.export_json_button = ttk.Button(
            toolbar,
            text="导出 JSON",
            command=self.export_json,
        )
        self.export_json_button.pack(side=tk.LEFT, padx=(0, 8))
        self.export_md_button = ttk.Button(
            toolbar,
            text="导出 Markdown",
            command=self.export_markdown,
        )
        self.export_md_button.pack(side=tk.LEFT)
        ttk.Label(
            toolbar,
            text="支持 .pdf / .docx · 可一次多选",
            style="Muted.TLabel",
        ).pack(side=tk.RIGHT)

        # 左右分栏
        self.paned = ttk.PanedWindow(outer, orient=tk.HORIZONTAL)
        self.paned.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(self.paned, width=300)
        left.pack_propagate(False)
        self.paned.add(left, weight=0)

        left_header = ttk.Frame(left)
        left_header.pack(fill=tk.X, padx=2, pady=(0, 8))
        ttk.Label(
            left_header,
            text="文件列表",
            style="SideTitle.TLabel",
        ).pack(side=tk.LEFT)
        self.file_count_label = ttk.Label(
            left_header,
            text="0 个文件",
            style="Muted.TLabel",
        )
        self.file_count_label.pack(side=tk.RIGHT)

        list_card = tk.Frame(
            left,
            bg=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        list_card.pack(fill=tk.BOTH, expand=True)
        list_card.grid_rowconfigure(0, weight=1)
        list_card.grid_columnconfigure(0, weight=1)
        self.file_list = tk.Listbox(
            list_card,
            selectmode=tk.BROWSE,
            exportselection=False,
            activestyle="none",
            font=(self.font_family, 10),
            bg=CARD,
            fg=TEXT,
            selectbackground="#dbeafe",
            selectforeground=TEXT,
            highlightthickness=0,
            borderwidth=0,
            relief=tk.FLAT,
        )
        self.file_list.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        list_y = ttk.Scrollbar(
            list_card,
            orient=tk.VERTICAL,
            command=self.file_list.yview,
        )
        list_y.grid(row=0, column=1, sticky="ns")
        list_x = ttk.Scrollbar(
            list_card,
            orient=tk.HORIZONTAL,
            command=self.file_list.xview,
        )
        list_x.grid(row=1, column=0, sticky="ew")
        self.file_list.configure(
            yscrollcommand=list_y.set,
            xscrollcommand=list_x.set,
        )

        right = ttk.Frame(self.paned)
        self.paned.add(right, weight=1)
        self.notebook = ttk.Notebook(right)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self._build_text_tab()
        self._build_table_tab()
        self._build_meta_tab()

    def _build_text_tab(self) -> None:
        """文本标签页：可滚动正文。"""
        tab = ttk.Frame(self.notebook, style="Card.TFrame")
        self.notebook.add(tab, text="文本")

        head = ttk.Frame(tab, style="Card.TFrame", padding=(14, 12, 14, 6))
        head.pack(fill=tk.X)
        ttk.Label(
            head,
            text="正文内容",
            style="CardTitle.TLabel",
        ).pack(side=tk.LEFT)
        self.text_hint = ttk.Label(
            head,
            text="",
            style="CardMuted.TLabel",
        )
        self.text_hint.pack(side=tk.RIGHT)

        self.text = ScrolledText(
            tab,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=(self.font_family, 10),
            bg=CARD,
            fg=TEXT,
            insertbackground=TEXT,
            relief=tk.FLAT,
            borderwidth=0,
            highlightthickness=0,
            padx=14,
            pady=6,
        )
        self.text.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 12))
        # 兼容旧版/测试脚本使用的属性名。
        self.text_area = self.text
        self.text.tag_configure(
            "h1",
            font=(self.font_family, 14, "bold"),
            foreground=TEXT,
            spacing1=6,
            spacing3=6,
        )
        self.text.tag_configure(
            "h2",
            font=(self.font_family, 11, "bold"),
            foreground=TEXT,
            spacing1=10,
            spacing3=4,
        )
        self.text.tag_configure(
            "body",
            font=(self.font_family, 10),
            foreground=TEXT,
            spacing3=6,
            lmargin1=2,
            lmargin2=2,
        )
        self.text.tag_configure(
            "muted",
            font=(self.font_family, 10),
            foreground=MUTED,
            spacing3=6,
        )
        self.text.tag_configure(
            "warning",
            font=(self.font_family, 10),
            foreground=WARNING,
            spacing3=6,
        )
        self.text.tag_configure(
            "error",
            font=(self.font_family, 10),
            foreground=ERROR,
            spacing3=6,
        )

    def _build_table_tab(self) -> None:
        """表格标签页：下拉框选择表格，Treeview 渲染。"""
        tab = ttk.Frame(self.notebook, style="Card.TFrame")
        self.notebook.add(tab, text="表格")

        head = ttk.Frame(tab, style="Card.TFrame", padding=(14, 12, 14, 8))
        head.pack(fill=tk.X)
        ttk.Label(
            head,
            text="表格内容",
            style="CardTitle.TLabel",
        ).pack(side=tk.LEFT)
        self.table_combo = ttk.Combobox(
            head,
            state="disabled",
            width=30,
            font=(self.font_family, 10),
        )
        self.table_combo.pack(side=tk.LEFT, padx=(12, 0))
        self.table_combo.bind("<<ComboboxSelected>>", self._on_table_selected)
        self.table_info = ttk.Label(
            head,
            text="—",
            style="CardMuted.TLabel",
        )
        self.table_info.pack(side=tk.RIGHT)

        self.table_host = ttk.Frame(
            tab,
            style="Card.TFrame",
            padding=(14, 0, 14, 12),
        )
        self.table_host.pack(fill=tk.BOTH, expand=True)
        self.table_tree: ttk.Treeview | None = None

    def _build_meta_tab(self) -> None:
        """元信息标签页：属性 / 值两列表格。"""
        tab = ttk.Frame(self.notebook, style="Card.TFrame")
        self.notebook.add(tab, text="元信息")

        head = ttk.Frame(tab, style="Card.TFrame", padding=(14, 12, 14, 8))
        head.pack(fill=tk.X)
        ttk.Label(
            head,
            text="文件与文档属性",
            style="CardTitle.TLabel",
        ).pack(side=tk.LEFT)
        self.meta_count = ttk.Label(
            head,
            text="—",
            style="CardMuted.TLabel",
        )
        self.meta_count.pack(side=tk.RIGHT)

        wrap = ttk.Frame(tab, style="Card.TFrame", padding=(14, 0, 14, 12))
        wrap.pack(fill=tk.BOTH, expand=True)
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        self.meta_tree = ttk.Treeview(
            wrap,
            columns=("name", "value"),
            show="headings",
            selectmode=tk.BROWSE,
        )
        self.meta_tree.heading("name", text="属性")
        self.meta_tree.heading("value", text="值")
        self.meta_tree.column(
            "name", width=190, minwidth=120, stretch=False, anchor="w"
        )
        self.meta_tree.column("value", width=560, minwidth=220, anchor="w")
        self.meta_tree.grid(row=0, column=0, sticky="nsew")
        meta_y = ttk.Scrollbar(
            wrap,
            orient=tk.VERTICAL,
            command=self.meta_tree.yview,
        )
        meta_y.grid(row=0, column=1, sticky="ns")
        meta_x = ttk.Scrollbar(
            wrap,
            orient=tk.HORIZONTAL,
            command=self.meta_tree.xview,
        )
        meta_x.grid(row=1, column=0, sticky="ew")
        self.meta_tree.configure(
            yscrollcommand=meta_y.set,
            xscrollcommand=meta_x.set,
        )
        self.meta_tree.tag_configure("warning", foreground=WARNING)

    def _bind_events(self) -> None:
        """绑定选择、快捷键和关闭事件。"""
        self.file_list.bind("<<ListboxSelect>>", self._on_select)
        self.root.bind_all("<Control-o>", self._on_ctrl_o)
        self.root.bind_all("<Control-e>", self._on_ctrl_e)
        self.root.bind_all("<Control-Shift-E>", self._on_ctrl_shift_e)
        self.root.bind_all("<Control-Shift-e>", self._on_ctrl_shift_e)
        self.root.bind_all("<F1>", lambda _event: self._show_usage())
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _set_initial_sash(self) -> None:
        """窗口映射后把左侧列表固定为约 300 像素。"""
        try:
            if self.paned.winfo_width() > 500:
                self.paned.sashpos(0, 300)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # 文件选择与校验
    # ------------------------------------------------------------------
    def choose_files(self) -> None:
        """打开文件对话框，允许多选 PDF / DOCX。"""
        paths = filedialog.askopenfilenames(
            parent=self.root,
            title="选择 PDF 或 Word 文件",
            filetypes=[
                ("支持的文件", "*.pdf *.docx"),
                ("PDF 文件", "*.pdf"),
                ("Word 文件", "*.docx"),
                ("所有文件", "*.*"),
            ],
        )
        if paths:
            # 某些 Tk 版本返回字符串而不是元组，统一交给 splitlist。
            self.add_files(self.root.tk.splitlist(paths))

    def add_files(self, paths: list[str] | tuple[str, ...]) -> None:
        """校验并加入文件；不合法的文件会汇总提示，但不影响其他文件。"""
        added: list[FileRecord] = []
        errors: list[str] = []

        for raw_path in paths:
            try:
                path = validate_file(raw_path)
            except ExtractionError as exc:
                errors.append(str(exc))
                continue
            except OSError as exc:
                errors.append(f"无法读取文件：{raw_path}（{exc}）")
                continue

            key = os.path.normcase(str(path))
            if key in self._path_keys:
                continue

            record = FileRecord(job_id=self._next_job_id, path=path)
            self._next_job_id += 1
            self._path_keys.add(key)
            self.records.append(record)
            self._record_by_job[record.job_id] = record
            added.append(record)
            self.worker.submit(record.job_id, path)

        self._refresh_list()
        self._refresh_activity()

        if added:
            self._select_record(self.records.index(added[0]))

        if errors:
            title = "部分文件未添加" if added else "无法添加文件"
            messagebox.showwarning(
                title,
                NEWLINE.join(errors),
                parent=self.root,
            )
        elif not added:
            messagebox.showinfo(
                "提示",
                "所选文件已经在列表中。",
                parent=self.root,
            )

    def clear_files(self) -> None:
        """清空文件列表；提取中的文件需等待完成后再清空。"""
        if not self.records:
            return
        if any(
            record.status in ("pending", "processing")
            for record in self.records
        ):
            messagebox.showinfo(
                "请稍候",
                "仍有文件正在提取，请等待完成后再清空列表。",
                parent=self.root,
            )
            return
        if not messagebox.askyesno(
            "清空列表",
            "确定要清空文件列表吗？",
            parent=self.root,
        ):
            return

        self.records.clear()
        self._record_by_job.clear()
        self._path_keys.clear()
        self.current_index = None
        self._refresh_list()
        self._show_empty()
        self._refresh_activity()

    # ------------------------------------------------------------------
    # 列表与结果展示
    # ------------------------------------------------------------------
    def _refresh_list(self) -> None:
        """按最新状态重绘左侧 Listbox，并保持当前选中项。"""
        self.file_list.delete(0, tk.END)
        colors = {
            "pending": MUTED,
            "processing": ACCENT,
            "done": TEXT,
            "error": ERROR,
        }
        for index, record in enumerate(self.records):
            type_label = FILE_TYPE_LABELS.get(
                record.path.suffix.lower().lstrip("."), "文件"
            )
            text = (
                f"[{type_label}] {record.path.name}"
                f"  ·  {STATUS_LABELS.get(record.status, record.status)}"
            )
            self.file_list.insert(tk.END, text)
            self.file_list.itemconfig(
                index, foreground=colors.get(record.status, TEXT)
            )

        self.file_count_label.config(text=f"{len(self.records)} 个文件")

        if self.current_index is not None and 0 <= self.current_index < len(
            self.records
        ):
            self.file_list.selection_clear(0, tk.END)
            self.file_list.selection_set(self.current_index)
            self.file_list.activate(self.current_index)
            self.file_list.see(self.current_index)
        elif self.records:
            self.current_index = 0
            self.file_list.selection_set(0)
            self.file_list.activate(0)
            self.file_list.see(0)
        else:
            self.current_index = None

    def _select_record(self, index: int) -> None:
        """选中指定索引的文件并刷新右侧内容。"""
        if not 0 <= index < len(self.records):
            return
        self.current_index = index
        self.file_list.selection_clear(0, tk.END)
        self.file_list.selection_set(index)
        self.file_list.activate(index)
        self.file_list.see(index)
        self._render_current()

    def _on_select(self, _event: tk.Event | None = None) -> None:
        """Listbox 选中项变化时切换展示。"""
        selection = self.file_list.curselection()
        if not selection:
            return
        self.current_index = int(selection[0])
        self._render_current()

    def _current_record(self) -> FileRecord | None:
        """返回当前选中的文件记录。"""
        if self.current_index is None:
            return None
        if not 0 <= self.current_index < len(self.records):
            return None
        return self.records[self.current_index]

    def _render_current(self) -> None:
        """把当前记录渲染到文本、表格和元信息三个标签页。"""
        record = self._current_record()
        if record is None:
            self._show_empty()
            return

        self._render_text(record)
        self._render_tables(record)
        self._render_meta(record)
        self._update_stats(record)
        self._update_export_buttons()

    def _show_empty(self) -> None:
        """没有选中文件时的占位内容。"""
        self._set_text_message(
            "尚未选择文件",
            "点击上方「选择文件」，可以一次选择多个 PDF 或 DOCX 文件。"
            f"{NEWLINE}单个文件大小不能超过 20 MB。",
        )
        self.text_hint.config(text="")
        self._tables = []
        self.table_combo.set("")
        self.table_combo.config(state=tk.DISABLED)
        self.table_info.config(text="—")
        self._show_table_placeholder("尚未选择文件")
        self.meta_tree.delete(*self.meta_tree.get_children())
        self.meta_count.config(text="—")
        self.stats_label.config(text="—")
        self._update_export_buttons()

    def _set_text_message(self, title: str, body: str, tag: str = "muted") -> None:
        """用标题 + 说明文本替换正文区域。"""
        self.text.config(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", title + NEWLINE + NEWLINE, "h1")
        self.text.insert(tk.END, body, tag)
        self.text.config(state=tk.DISABLED)
        self.text.yview_moveto(0)

    def _render_text(self, record: FileRecord) -> None:
        """渲染正文；PDF 按页展示，Word 按段落展示。"""
        if record.status in ("pending", "processing"):
            state = "等待提取" if record.status == "pending" else "正在提取"
            self._set_text_message(
                f"{state}…",
                f"文件：{record.path.name}{NEWLINE}{NEWLINE}"
                "提取完成后，这里会显示正文内容。",
            )
            self.text_hint.config(text="")
            return

        if record.status == "error":
            self._set_text_message("提取失败", record.error, "error")
            self.text_hint.config(text="")
            return

        data = record.data or {}
        pages = data.get("pages") or []
        paragraphs = data.get("paragraphs") or []
        stats = data.get("stats") or {}

        self.text.config(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)

        if pages:
            for page in pages:
                page_number = page.get("page", "")
                self.text.insert(
                    tk.END,
                    f"第 {page_number} 页{NEWLINE}",
                    "h2",
                )
                page_text = str(page.get("text", "")).strip()
                if page_text == NO_TEXT_MARKER:
                    self.text.insert(tk.END, NO_TEXT_MARKER, "warning")
                elif page_text:
                    self.text.insert(tk.END, page_text, "body")
                else:
                    self.text.insert(tk.END, "（本页为空）", "muted")
                self.text.insert(tk.END, NEWLINE + NEWLINE)
        elif paragraphs:
            self.text.insert(tk.END, "正文内容" + NEWLINE, "h2")
            for paragraph in paragraphs:
                self.text.insert(tk.END, str(paragraph) + NEWLINE + NEWLINE, "body")
        else:
            self.text.insert(tk.END, "没有可提取的正文。", "muted")

        self.text.config(state=tk.DISABLED)
        self.text.yview_moveto(0)

        char_count = stats.get("char_count", 0)
        word_count = stats.get("word_count", 0)
        if data.get("file_type") == "pdf":
            hint = f"{len(pages)} 页 · {char_count:,} 字符 · {word_count:,} 词"
        else:
            hint = (
                f"{len(paragraphs)} 段 · {char_count:,} 字符"
                f" · {word_count:,} 词"
            )
        self.text_hint.config(text=hint)

    def _render_tables(self, record: FileRecord) -> None:
        """刷新表格下拉框，并显示第一个表格。"""
        if record.status != "done" or not record.data:
            tables: list[list[list[str]]] = []
        else:
            tables = record.data.get("tables") or []

        self._tables = tables
        if not tables:
            self.table_combo.set("")
            self.table_combo.config(values=(), state=tk.DISABLED)
            self.table_info.config(text="—")
            if record.status in ("pending", "processing"):
                message = "提取完成后显示表格"
            elif record.status == "error":
                message = "无法显示表格"
            else:
                message = "未发现表格"
            self._show_table_placeholder(message)
            return

        values = []
        for index, table in enumerate(tables, start=1):
            column_count = max((len(row) for row in table), default=0)
            values.append(
                f"表格 {index} · {len(table)} 行 × {column_count} 列"
            )
        self.table_combo.config(values=values, state="readonly")
        self.table_combo.current(0)
        self._show_table(0)

    def _on_table_selected(self, _event: tk.Event | None = None) -> None:
        """表格下拉框切换。"""
        index = self.table_combo.current()
        if index >= 0:
            self._show_table(index)

    def _show_table_placeholder(self, message: str) -> None:
        """清空表格区域并显示占位提示。"""
        self.table_tree = None
        for child in self.table_host.winfo_children():
            child.destroy()
        ttk.Label(
            self.table_host,
            text=message,
            style="CardMuted.TLabel",
            anchor="center",
        ).pack(fill=tk.BOTH, expand=True)

    def _show_table(self, index: int) -> None:
        """在 Treeview 中渲染指定表格。"""
        if not 0 <= index < len(self._tables):
            return
        table = self._tables[index]
        column_count = max((len(row) for row in table), default=0)
        if column_count == 0:
            self._show_table_placeholder("空表格")
            return

        for child in self.table_host.winfo_children():
            child.destroy()
        self.table_host.grid_rowconfigure(0, weight=1)
        self.table_host.grid_columnconfigure(0, weight=1)

        columns = ["row"] + [f"c{number}" for number in range(1, column_count + 1)]
        tree = ttk.Treeview(
            self.table_host,
            columns=columns,
            show="headings",
            selectmode=tk.BROWSE,
        )
        self.table_tree = tree
        tree.heading("row", text="行")
        tree.column("row", width=55, minwidth=45, stretch=False, anchor="center")
        for number in range(1, column_count + 1):
            column = f"c{number}"
            tree.heading(column, text=f"列 {number}")
            tree.column(column, width=160, minwidth=90, stretch=True, anchor="w")

        for row_index, row in enumerate(table, start=1):
            values = [str(row_index)]
            for cell_index in range(column_count):
                values.append(
                    str(row[cell_index]) if cell_index < len(row) else ""
                )
            tags = ("odd",) if row_index % 2 == 0 else ()
            tree.insert("", tk.END, values=values, tags=tags)
        tree.tag_configure("odd", background="#f8fafc")

        tree.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(
            self.table_host,
            orient=tk.VERTICAL,
            command=tree.yview,
        )
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(
            self.table_host,
            orient=tk.HORIZONTAL,
            command=tree.xview,
        )
        x_scroll.grid(row=1, column=0, sticky="ew")
        tree.configure(
            yscrollcommand=y_scroll.set,
            xscrollcommand=x_scroll.set,
        )

        self.table_info.config(
            text=(
                f"第 {index + 1}/{len(self._tables)} 个表格"
                f" · {len(table)} 行 × {column_count} 列"
            )
        )

    def _render_meta(self, record: FileRecord) -> None:
        """显示基础文件信息与提取到的文档属性。"""
        pairs: list[tuple[str, str]] = []

        try:
            stat = record.path.stat()
            modified = datetime.fromtimestamp(stat.st_mtime).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            size = format_file_size(stat.st_size)
        except OSError:
            modified = "—"
            size = "—"

        file_type = record.path.suffix.lower().lstrip(".")
        pairs += [
            ("文件名", record.path.name),
            ("文件类型", FILE_TYPE_LABELS.get(file_type, file_type)),
            ("文件路径", str(record.path)),
            ("文件大小", size),
            ("文件修改时间", modified),
        ]

        if record.data:
            for key, value in (record.data.get("meta") or {}).items():
                if key in BASE_META_KEYS:
                    continue
                label = META_LABELS.get(str(key), str(key))
                pairs.append((label, display_meta_value(value)))

        if record.status in ("pending", "processing"):
            pairs.append(("状态", STATUS_LABELS.get(record.status, record.status)))
        if record.error:
            pairs.append(("错误信息", record.error))

        self.meta_tree.delete(*self.meta_tree.get_children())
        for label, value in pairs:
            tags = ("warning",) if label in ("提取警告", "错误信息") else ()
            self.meta_tree.insert(
                "",
                tk.END,
                values=(label, value),
                tags=tags,
            )
        self.meta_count.config(text=f"{len(pairs)} 项属性")

    def _update_stats(self, record: FileRecord | None) -> None:
        """更新右下角统计信息。"""
        if record is None or record.status != "done" or not record.data:
            self.stats_label.config(text="—")
            return
        stats = record.data.get("stats") or {}
        page_count = stats.get("page_count", 0) or 0
        if record.data.get("file_type") == "pdf":
            page_text = f"{page_count} 页"
        else:
            # Word 没有固定分页概念，python-docx 也不计算页数。
            page_text = "页数 —"
        self.stats_label.config(
            text=(
                f"{stats.get('char_count', 0):,} 字符"
                f" · {stats.get('word_count', 0):,} 词"
                f" · {page_text}"
                f" · {len(record.data.get('tables') or [])} 个表格"
            )
        )

    def _update_export_buttons(self) -> None:
        """只有当前文件提取成功后才允许导出。"""
        record = self._current_record()
        state = tk.NORMAL if record and record.status == "done" else tk.DISABLED
        self.export_json_button.config(state=state)
        self.export_md_button.config(state=state)

    # ------------------------------------------------------------------
    # 状态、进度和后台消息
    # ------------------------------------------------------------------
    def _set_status(self, text: str, color: str = TEXT) -> None:
        """设置左侧状态栏文字和颜色。"""
        self.status_label.config(text=text, foreground=color)

    def _refresh_activity(self) -> None:
        """根据待处理数量更新进度条与汇总状态。"""
        pending = sum(
            record.status in ("pending", "processing")
            for record in self.records
        )
        if pending:
            if not self._progress_on:
                # 先显示再启动，避免隐藏状态下的动画占用 CPU。
                self.progress.pack(side=tk.RIGHT, padx=(0, 14))
                self.progress.start(12)
                self._progress_on = True
            # 正在处理的文件可能来自 PDF 提取器：显示具体页码 / OCR 状态。
            current = next(
                (
                    record
                    for record in self.records
                    if record.status == "processing" and record.progress_message
                ),
                None,
            )
            if current is not None:
                self._set_status(current.progress_message, ACCENT)
            else:
                self._set_status(f"正在提取… 剩余 {pending} 个文件", ACCENT)
            return

        if self._progress_on:
            self.progress.stop()
            self.progress.pack_forget()
            self._progress_on = False

        if not self.records:
            self._set_status("就绪", MUTED)
            return

        success = sum(record.status == "done" for record in self.records)
        failed = sum(record.status == "error" for record in self.records)
        if failed:
            self._set_status(
                f"提取完成：成功 {success} 个，失败 {failed} 个。",
                ERROR,
            )
        else:
            self._set_status(f"提取完成：成功 {success} 个。", SUCCESS)

    def _poll_worker(self) -> None:
        """在主线程消费后台队列；Tkinter 控件只能在这里更新。"""
        if self._closed:
            return

        changed = False
        errors: list[FileRecord] = []

        while True:
            try:
                kind, job_id, data, error = self.result_queue.get_nowait()
            except queue.Empty:
                break

            record = self._record_by_job.get(job_id)
            if record is None:
                continue

            if kind == "start":
                record.status = "processing"
                record.progress_message = ""
            elif kind == "progress":
                record.progress_message = str(data or "")
            elif kind == "done":
                record.status = "done"
                record.data = data
                record.error = ""
                record.progress_message = ""
            elif kind == "error":
                record.status = "error"
                record.error = error
                record.progress_message = ""
                errors.append(record)
            changed = True

        if changed:
            self._refresh_list()
            self._render_current()
            self._refresh_activity()

        for record in errors:
            messagebox.showwarning(
                "提取失败",
                f"{record.path.name}{NEWLINE}{NEWLINE}{record.error}",
                parent=self.root,
            )

        self.root.after(100, self._poll_worker)

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------
    def _ask_export_path(
        self,
        record: FileRecord,
        extension: str,
        file_type_label: str,
    ) -> Path | None:
        """询问导出路径，取消时返回 None。"""
        default_name = f"{record.path.stem}_提取结果{extension}"
        selected = filedialog.asksaveasfilename(
            parent=self.root,
            title=f"导出 {file_type_label}",
            defaultextension=extension,
            initialdir=str(record.path.parent),
            initialfile=default_name,
            filetypes=[(file_type_label, f"*{extension}")],
        )
        return Path(selected) if selected else None

    def export_json(self) -> None:
        """把当前结果导出为 UTF-8 JSON。"""
        record = self._current_record()
        if record is None or record.status != "done" or not record.data:
            messagebox.showinfo(
                "尚未就绪",
                "请先选择一个已提取完成的文件。",
                parent=self.root,
            )
            return

        path = self._ask_export_path(record, ".json", "JSON 文件")
        if path is None:
            return
        try:
            text = json.dumps(record.data, ensure_ascii=False, indent=2)
            path.write_text(text + NEWLINE, encoding="utf-8")
        except (OSError, TypeError) as exc:
            messagebox.showerror(
                "导出失败",
                f"无法写入文件：{NEWLINE}{exc}",
                parent=self.root,
            )
            return

        self._set_status(f"已导出 JSON：{path}", SUCCESS)

    def export_markdown(self) -> None:
        """把当前结果导出为 Markdown。"""
        record = self._current_record()
        if record is None or record.status != "done" or not record.data:
            messagebox.showinfo(
                "尚未就绪",
                "请先选择一个已提取完成的文件。",
                parent=self.root,
            )
            return

        path = self._ask_export_path(record, ".md", "Markdown 文件")
        if path is None:
            return
        try:
            path.write_text(build_markdown(record.data), encoding="utf-8")
        except OSError as exc:
            messagebox.showerror(
                "导出失败",
                f"无法写入文件：{NEWLINE}{exc}",
                parent=self.root,
            )
            return

        self._set_status(f"已导出 Markdown：{path}", SUCCESS)

    # ------------------------------------------------------------------
    # 菜单、快捷键与关闭
    # ------------------------------------------------------------------
    def _on_ctrl_o(self, _event: tk.Event) -> str:
        self.choose_files()
        return "break"

    def _on_ctrl_e(self, _event: tk.Event) -> str:
        self.export_json()
        return "break"

    def _on_ctrl_shift_e(self, _event: tk.Event) -> str:
        self.export_markdown()
        return "break"

    def _configure_cloud_ocr(self) -> None:
        """让用户填写并保存 AI Studio 访问令牌（只写入用户目录）。"""
        try:
            from .extractors.cloud_ocr import has_token, save_access_token
        except Exception as exc:
            messagebox.showerror(
                "云端 OCR 设置",
                f"云端模块加载失败：{exc}",
                parent=self.root,
            )
            return

        status = "已配置" if has_token() else "未配置"
        token = simpledialog.askstring(
            "云端 OCR 设置",
            "未检测到 NVIDIA GPU 时，程序会使用 PaddleOCR 云端 PP-StructureV3。"
            f"{NEWLINE}当前令牌：{status}"
            f"{NEWLINE}{NEWLINE}请粘贴 AI Studio 访问令牌（保存在本机用户目录，不写入源码）：",
            show="*",
            parent=self.root,
        )
        if token is None:
            return
        token = token.strip()
        if not token:
            messagebox.showwarning(
                "云端 OCR 设置", "访问令牌不能为空。", parent=self.root
            )
            return
        try:
            path = save_access_token(token)
        except Exception as exc:
            messagebox.showerror(
                "云端 OCR 设置",
                f"保存失败：{exc}",
                parent=self.root,
            )
            return
        self._set_status(f"云端 OCR 令牌已保存：{path}", SUCCESS)

    def _show_usage(self) -> None:
        """帮助菜单中的简要使用说明。"""
        limit_mb = MAX_FILE_SIZE // (1024 * 1024)
        messagebox.showinfo(
            "使用说明",
            "1. 点击「选择文件」或按 Ctrl+O，可一次多选 PDF / DOCX。"
            f"{NEWLINE}2. 提取在后台线程进行，界面不会卡死。"
            f"{NEWLINE}3. 单文件不能超过 {limit_mb} MB。"
            f"{NEWLINE}4. 在「文本 / 表格 / 元信息」标签页查看结果。"
            f"{NEWLINE}5. 提取完成后可导出 JSON 或 Markdown。"
            f"{NEWLINE}6. 安装 OCR 依赖后，扫描版 PDF 会自动调用 PaddleOCR；"
            "首次识别需加载模型，请耐心等待。"
            f"{NEWLINE}7. 检测到 NVIDIA GPU 时自动使用本地 GPU；没有 GPU 时自动改用"
            f" 云端 PP-StructureV3（需在「云端 OCR 设置」中填写令牌）。"
            f"{NEWLINE}   云端会把整份 PDF 上传到百度 AI Studio，敏感文档请先设置"
            f" PDF_WORD_OCR_MODE=local。"
            f"{NEWLINE}8. 可用环境变量 PDF_WORD_OCR_MODE=auto/local/cloud 控制后端，"
            f" PDF_WORD_OCR_DEVICE 控制本地设备（auto / cpu / gpu:0）。",
            parent=self.root,
        )

    def _show_about(self) -> None:
        """关于对话框。"""
        messagebox.showinfo(
            f"关于 {APP_TITLE}",
            f"{APP_TITLE} v{APP_VERSION}{NEWLINE}{NEWLINE}"
            "使用 Tkinter + pdfplumber + PaddleOCR + python-docx 构建。"
            f"{NEWLINE}支持 PDF 逐页文本/表格、扫描页 OCR 与 Word 段落/表格/核心属性提取。",
            parent=self.root,
        )

    def _on_close(self) -> None:
        """关闭窗口前确认是否有未完成任务。"""
        pending = sum(
            record.status in ("pending", "processing")
            for record in self.records
        )
        if pending and not messagebox.askyesno(
            "退出",
            f"仍有 {pending} 个文件正在提取，确定退出吗？",
            parent=self.root,
        ):
            return

        self._closed = True
        self.worker.stop()
        self.root.destroy()


def main() -> None:
    """程序入口；支持 ``--version``，默认启动图形界面。"""
    parser = argparse.ArgumentParser(
        prog="pdf-word-extractor",
        description="PDF / Word 信息提取器（Tkinter 图形界面）",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {APP_VERSION}",
    )
    parser.parse_args()

    root = tk.Tk()
    PDFWordExtractorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
