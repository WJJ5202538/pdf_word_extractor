# 架构说明

本文说明代码的分层、一次提取的完整调用链，以及后续扩展时应该改哪里。

## 1. 分层

```text
Tkinter 窗口（app.py）
   │  选择文件 / 显示结果 / 导出
   ▼
提取调度（app.ExtractionWorker）
   │  job_id + path，通过 queue 回传状态
   ▼
统一提取入口（extractors/__init__.py -> extract_file）
   ├─ docx_extractor.py
   └─ pdf_extractor.py
          ├─ ocr_engine.py（本地 PaddleOCR）
          └─ cloud_ocr.py（云端 PP-StructureV3）
   ▼
统一结果（extractors/base.py）
   │  build_result / stats / json_safe
   ▼
展示与导出（app.py / exporters.py / labels.py）
```

原则：

- `extractors` 不导入 Tkinter，可以单独在脚本、服务里调用；
- `exporters` 只接收统一结果字典，不关心文件类型；
- GUI 只做三件事：收集路径、消费队列、渲染字典；
- 可选依赖（PaddleOCR）只在真正需要 OCR 时导入，没装也不影响启动。

## 2. 模块职责

| 文件 | 职责 |
| --- | --- |
| `src/main.py` | 源码入口，调用 `app.main()` |
| `src/pdf_word_extractor/__main__.py` | 支持 `python -m pdf_word_extractor` |
| `src/pdf_word_extractor/app.py` | 窗口、菜单、列表、三个标签页、后台队列、导出 |
| `src/pdf_word_extractor/labels.py` | 文件类型、状态、元信息的中文标签 |
| `src/pdf_word_extractor/exporters.py` | 把统一结果转成 Markdown；管道符转义、表格对齐 |
| `extractors/base.py` | 文件校验、统一结构、统计、日期/集合 JSON 化、错误类型 |
| `extractors/docx_extractor.py` | Word 段落、表格、核心属性 |
| `extractors/pdf_extractor.py` | PDF 逐页文本、pdfplumber 表格、OCR 路由、元信息 |
| `extractors/ocr_engine.py` | PaddleOCR 表格流水线、页面 PNG 渲染、HTML 转二维表 |
| `extractors/cloud_ocr.py` | 云端客户端、令牌解析、云端结果映射 |

## 3. 一次 PDF 提取的调用链

```text
add_files(paths)
  -> validate_file(path)              # 存在、后缀、大小、空文件
  -> worker.submit(job_id, path)
  -> queue("start", job_id)
  -> extract_file(path, progress)
       -> extract_pdf(path, progress)
            -> pdfplumber.open(path)
            -> 逐页：
                 page.extract_text()
                 page.extract_tables()
                 判断是否需要 OCR
                 （可选）本地/云端 OCR
            -> make_base_meta() + PDF 文档属性
            -> build_result()
  -> queue("done", job_id, data) 或 queue("error", job_id, None, 中文原因)
  -> 主线程 _poll_worker() 每 100ms 消费一次
  -> 刷新 Listbox / 文本 / 表格 / 元信息 / 状态栏
```

### 文本与表格策略

1. 有文本层时，正文优先用 PDF 文本层，避免 OCR 把公式、上下标识别错；
2. 表格优先用 pdfplumber 的线条分析，速度快且保留原始字符；
3. 页面没有文本层，或「看起来有表格但 pdfplumber 没识别到」时，才调用 OCR；
4. OCR 失败不会丢掉整份文档：记录一条警告，继续处理其他页/其他文件。

### OCR 路由

`pdf_extractor` 通过 `cloud_ocr.choose_backend()` 选择：

- `PDF_WORD_OCR_MODE=local`：强制本地；
- `PDF_WORD_OCR_MODE=cloud`：强制云端；
- `auto`：有 CUDA 用本地 GPU；没有 GPU 但有令牌用云端；否则本地 CPU。

本地引擎是进程内单例，第一次调用才初始化；模型和 CUDA 上下文在多次提取之间复用。
云端按整份 PDF 提交，返回的 `parsing_res_list` / `table_res_list` 会映射回统一结构。

## 4. 统一结果

`build_result()` 负责把不同来源的数据收口：

```python
{
    "filename": "...",
    "file_type": "pdf" | "docx",
    "meta": {...},
    "pages": [{"page": int, "text": str}],
    "paragraphs": [str],
    "tables": [[[str]]],
    "stats": {"char_count": int, "word_count": int, "page_count": int},
}
```

约定：

- PDF 用 `pages`，Word 用 `paragraphs`，另一个固定为空列表；
- 表格统一展开成矩形：合并单元格按行列复制，保证每行等长；
- 无文本层页面写 `NO_TEXT_MARKER = "无可提取文本"`；
- `page_count` 对 Word 固定为 0，因为 .docx 没有可靠页数；
- `json_safe()` 把 datetime、set 等转成可 JSON 序列化的值。

统计口径在 `base.count_stats()`：中文按字符计数，英文按单词计数。

## 5. 线程模型

- 一个常驻 daemon 线程 `ExtractionWorker`，内部是 FIFO 任务队列；
- 提交任务只放 `(job_id, path)`，不把 Tk 控件传进线程；
- 工作线程只往 `result_queue` 放四种消息：

| kind | data | 含义 |
| --- | --- | --- |
| `start` | `None` | 开始处理 |
| `progress` | 中文进度字符串 | 当前页 / OCR 阶段 |
| `done` | 结果字典 | 成功 |
| `error` | `None` + error 文本 | 失败原因 |

- 主线程用 `root.after(100, _poll_worker)` 轮询队列；所有控件更新都发生在主线程；
- 单文件异常在 worker 内被捕获并转成 `error` 消息，所以不会中断后续文件；
- 关闭窗口时 `worker.stop()` 投递哨兵，线程自然退出。

## 6. 界面渲染

- 左侧 `Listbox`：显示 `[PDF] name · 完成` 之类的状态，按状态着色；
- 右侧 `Notebook`：
  - 文本：`ScrolledText`，PDF 插页标题，Word 插段落；
  - 表格：`ttk.Treeview` 动态列，行号 + 每列表头；多表格用下拉框切换；
  - 元信息：两列 Treeview，基础文件信息 + 文档属性；
- 状态栏：提取进度、成功/失败数量、字符/词/页/表格统计；
- 导出按钮只在当前文件 `done` 时可用。

## 7. 导出

`exporters.build_markdown(result)` 的输出顺序：

1. `# 文件名`
2. 文件类型
3. `## 元信息`（中文标签 + 值）
4. `## 正文`（PDF 按 `### 第 N 页`，Word 按段落）
5. `## 表格`（`### 表格 N` + Markdown 表格）
6. `## 统计`

JSON 导出直接 `json.dumps(result, ensure_ascii=False, indent=2)`。

## 8. 扩展点

- **新增文件类型**：在 `extractors` 增加模块，在 `extract_file` 分派，并补 `FILE_TYPE_LABELS`；
- **替换 OCR 引擎**：实现与 `PaddleOcrEngine.recognize()` 相同的返回结构即可；
- **新增导出格式**：在 `exporters.py` 增加纯函数，GUI 只加按钮；
- **新增元信息**：在 `base.make_base_meta()` 或对应 extractor 写入，再补 `META_LABELS`。

## 9. 测试边界

`tests/` 不启动完整主循环，覆盖：

- 文件校验（缺失、目录、后缀、空文件、20 MB 上限）；
- Word 段落/表格/核心属性；
- PDF 文本层、无文本层标记、pdfplumber 表格、元信息；
- Markdown 结构、管道符转义、Word 页数显示；
- OCR HTML 的 rowspan/colspan 展开；
- 包版本号。

GUI 的冒烟测试可以单独构造 `PDFWordExtractorApp`，用 `root.update()` 驱动队列；
CI 只跑无界面测试，避免依赖桌面环境。
