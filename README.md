# PDF / Word 信息提取器

一个用 **Python + Tkinter** 写的桌面小工具：选择（可多选）`.pdf` / `.docx` 文件，自动提取正文、表格和元信息，并在界面上分标签页查看，最后导出为 **JSON** 或 **Markdown**。

- 核心依赖只有 `pdfplumber` 与 `python-docx`，不引入额外 GUI 库。
- PDF 文本层、表格、Word 段落、表格、核心属性一次读全。
- 扫描 PDF 或复杂表格可以按需启用 **PaddleOCR**（可选依赖，不装也能用核心功能）。
- 提取在后台线程执行，界面不卡；单个文件失败不会影响其他文件。
- 提供测试、环境自检、wheel/sdist 构建脚本与 GitHub Actions。

## 功能

| 能力 | PDF | Word（.docx） |
| --- | --- | --- |
| 逐页 / 逐段正文 | 按页提取；无文本层时标记「无可提取文本」 | 提取非空段落 |
| 表格 | pdfplumber 线条分析；识别不到时可由 OCR 兜底 | python-docx 表格，支持合并单元格展开 |
| 元信息 | 标题、作者、主题、关键词、创建/修改时间、加密状态等 | 标题、作者、主题、备注、修订号等核心属性 |
| 导出 | JSON / Markdown | JSON / Markdown |
| 多文件 | 左侧列表，逐个查看；单文件失败只弹提示 | 同左 |

界面右侧固定为三个标签页：

- **文本**：PDF 按页显示，Word 按段落显示；
- **表格**：下拉框切换多个表格，Treeview 渲染，可横向/纵向滚动；
- **元信息**：文件路径、大小、修改时间，以及文档自身的属性。

底部状态栏显示字符数、词数、页数（Word 显示「页数 —」）和表格数量。

## 环境要求

- Python **3.10+**；推荐 **3.13**（OCR 依赖目前只提供到 CPython 3.13）。
- Windows 官方 Python 安装包自带 Tkinter；Linux/macOS 需要系统安装 Tk。
- 核心功能不需要联网；OCR 首次运行会下载模型，或使用云端接口。

> 只装核心依赖时，Python 3.15 也能运行；但 `paddlepaddle` 尚无 cp315 轮子，
> 扫描件和 OCR 表格识别需要 Python 3.10–3.13。

## 快速开始

```powershell
git clone https://github.com/<your-name>/pdf-word-extractor.git
cd pdf-word-extractor

py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[ocr]"

python -m pdf_word_extractor
```

如果只想用文本层和 pdfplumber 的表格识别，不安装 OCR：

```powershell
python -m pip install -e .
python -m pdf_word_extractor
```

macOS / Linux 把激活命令换成：

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[ocr]"
python -m pdf_word_extractor
```

### Windows 双击启动

首次先按上面的命令创建 `.venv` 并安装依赖，之后可以直接双击 `启动程序.bat`。
脚本会优先使用 `.venv\Scripts\pythonw.exe`，不会写死任何本机路径。

### 其他启动方式

```powershell
python src/main.py                 # 从源码目录运行
pdf-word-extractor                 # 安装后生成的控制台入口
pdf-word-extractor-gui             # 安装后生成的无控制台窗口入口
python -m pdf_word_extractor --version
```

## 使用步骤

1. 点击「选择文件」，或按 `Ctrl+O`，一次选择多个 `.pdf` / `.docx`；
2. 在左侧列表点选文件，右侧查看文本、表格、元信息；
3. 点「导出 JSON」或「导出 Markdown」，选择保存位置；
4. 需要云端 OCR 时，打开「帮助 → 云端 OCR 设置…」填入 AI Studio 访问令牌。

快捷键：

| 快捷键 | 功能 |
| --- | --- |
| `Ctrl+O` | 选择文件 |
| `Ctrl+E` | 导出 JSON |
| `Ctrl+Shift+E` | 导出 Markdown |
| `F1` | 使用说明 |

文件限制：单个文件不超过 **20 MB**；不存在、后缀不支持、空文件、加密 PDF 都会给出中文提示，不会让程序退出。

## 项目结构

```text
pdf-word-extractor/
├─ .github/workflows/ci.yml       # Windows 上的测试与构建
├─ docs/
│  ├─ architecture.md             # 模块划分、线程模型、数据结构
│  └─ ocr.md                      # PaddleOCR / GPU / 云端的安装与配置
├─ src/
│  ├─ main.py                     # 源码运行入口
│  └─ pdf_word_extractor/
│     ├─ __init__.py              # 版本号
│     ├─ __main__.py              # python -m 入口
│     ├─ app.py                   # Tkinter 界面、文件队列、导出
│     ├─ exporters.py             # Markdown 生成（不依赖 Tkinter）
│     ├─ labels.py                # 中文标签表
│     └─ extractors/
│        ├─ base.py               # 校验、统一结构、统计、JSON 安全转换
│        ├─ docx_extractor.py     # Word 段落 / 表格 / 核心属性
│        ├─ pdf_extractor.py      # PDF 逐页文本 / 表格 / 元信息
│        ├─ ocr_engine.py         # 本地 PaddleOCR 与页面渲染
│        └─ cloud_ocr.py          # 云端 PP-StructureV3 与令牌配置
├─ tests/
│  ├─ fixtures/sample.pdf
│  ├─ conftest.py
│  └─ test_*.py
├─ tools/check_env.py             # 只读环境自检
├─ build.ps1                      # 构建 wheel / sdist（Windows）
├─ MANIFEST.in                    # sdist 附带文档、测试与启动脚本
├─ pyproject.toml
├─ requirements.txt               # 仅核心依赖
├─ 启动程序.bat
├─ .gitignore
└─ README.md
```

## 统一结果结构

无论 PDF 还是 Word，提取结果都遵循同一份结构，方便直接写数据库或二次处理：

```json
{
  "filename": "example.pdf",
  "file_type": "pdf",
  "meta": {
    "file_name": "example.pdf",
    "file_type": "pdf",
    "file_size_bytes": 3988,
    "file_size": "3.9 KB",
    "modified_time": "2026-11-01T17:08:46",
    "title": "示例文档",
    "author": "张三",
    "encrypted": false,
    "table_source": "pdfplumber"
  },
  "pages": [
    { "page": 1, "text": "第一页正文……" },
    { "page": 2, "text": "无可提取文本" }
  ],
  "paragraphs": [],
  "tables": [
    [
      ["姓名", "部门", "备注"],
      ["张三", "研发部", "A|B"]
    ]
  ],
  "stats": {
    "char_count": 62,
    "word_count": 21,
    "page_count": 2
  }
}
```

说明：

- `pages`：PDF 使用；无文本层的页面文本固定为 `无可提取文本`。Word 固定为 `[]`。
- `paragraphs`：Word 使用；PDF 固定为 `[]`。
- `tables`：三层数组，第一层是表格，第二层是行，第三层是单元格字符串。
- `stats.page_count`：Word 没有可靠页数，固定为 `0`，界面显示「页数 —」。
- `char_count` / `word_count`：中文按字计数，英文按单词计数。
- OCR 参与的页面会在 `meta.ocr_pages` 中记录页码，`meta.table_source` 标明表格来源。

## 导出格式

- **JSON**：UTF-8、缩进 2 空格，字段与上面的结构一致。
- **Markdown**：一级标题是文件名，随后是元信息、正文（PDF 按「### 第 N 页」分节）和表格；
  表格输出为 `| a | b |`，单元格中的 `|` 会转义成 `\|`。

## OCR（可选）

安装 `.[ocr]` 后，PDF 提取会在以下情况调用 OCR：

1. 页面没有文本层（扫描页）；
2. 页面看起来有表格，但 pdfplumber 没有识别出表格。

后端选择顺序：

- 有可用 NVIDIA GPU：本地 PaddleOCR（GPU）；
- 没有 GPU 且配置了令牌：PaddleOCR 云端 PP-StructureV3；
- 都没有：本地 CPU PaddleOCR。

用 `PDF_WORD_OCR_MODE` 可以强制 `auto` / `local` / `cloud`。
云端会把整份 PDF（最多前 100 页）上传到百度 AI Studio，敏感文档请使用本地模式或不要配置令牌。
安装、GPU 轮子、模型缓存和令牌配置见 **[docs/ocr.md](docs/ocr.md)**。

## 配置项

| 环境变量 | 作用 |
| --- | --- |
| `PDF_WORD_OCR_MODE` | `auto`（默认）/ `local` / `cloud` |
| `PADDLEOCR_ACCESS_TOKEN` | 云端访问令牌（优先级最高） |
| `PDF_WORD_OCR_TOKEN` | 云端访问令牌（备用变量名） |
| `PDF_WORD_OCR_CONFIG` | 自定义云端配置文件路径 |
| `PADDLEOCR_BASE_URL` | 自定义云端服务地址（一般不用） |
| `PADDLE_PDX_MODEL_SOURCE` | 模型源，默认 `modelscope` |

令牌也可以保存在用户目录的 `.pdf_word_extractor/config.json` 中，程序不会把令牌写进源码或日志。
更完整的配置说明见 [docs/ocr.md](docs/ocr.md)。

## 开发与测试

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check src tests tools
python -m compileall -q src tests tools
python tools/check_env.py
```

`check_env.py` 只读检查 Python、Tkinter、核心依赖、OCR 依赖、Paddle CUDA、令牌是否配置和模型缓存，
不会打印令牌内容；核心依赖缺失时返回退出码 1。

## 构建

Windows：

```powershell
.\build.ps1            # 生成 dist/*.whl 与 dist/*.tar.gz
.\build.ps1 -Clean     # 先清理 build/、dist/、*.egg-info
```

任意平台：

```bash
python -m pip install build
python -m build
```

安装构建产物：

```powershell
python -m pip install dist\pdf_word_extractor-2.0.0-py3-none-any.whl
pdf-word-extractor
```

## 已知限制

- 单文件上限 20 MB；超大文件请先拆分。
- 只支持 `.pdf` 与 `.docx`；旧版 `.doc`、加密 PDF 不支持。
- Word 不存储可靠页数，`page_count` 固定为 0；需要页数时请先转成 PDF。
- 扫描 PDF 依赖 OCR；OCR 未安装时页面会显示「无可提取文本」。
- 复杂合并单元格、无线框表格、跨页表格可能被拆开或合并，导出后建议人工抽查。
- 未集成拖拽：为了保持依赖只有 `pdfplumber` / `python-docx`（OCR 另算），没有引入 `tkinterdnd2`。
- 界面主要针对 Windows 验证；macOS/Linux 需要自行确认 Tk 和中文字体。
- OCR 首次运行需要下载模型，耗时取决于网络；模型缓存默认在 `~/.paddlex/official_models`。

## 文档

- [架构说明](docs/architecture.md)
- [OCR 安装与配置](docs/ocr.md)
- [环境自检](tools/check_env.py)

## 参与贡献

欢迎提交 Issue 和 Pull Request。提交前请至少运行：

```powershell
python -m pytest
python -m ruff check src tests tools
```

请勿提交 `.venv/`、模型缓存、`config.json` 或任何访问令牌。
