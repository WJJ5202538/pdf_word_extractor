# OCR 安装与配置

OCR 是**可选能力**。不安装时，程序仍能提取 PDF 文本层、pdfplumber 能识别的表格，以及 Word 的全部内容。
安装后，扫描页和复杂表格会走 PaddleOCR。

## 1. 版本约束

| 组件 | 版本 | 说明 |
| --- | --- | --- |
| Python | 3.10–3.13 | PaddlePaddle 目前只发布到 cp313；推荐 3.13 |
| pdfplumber | 0.11.10 | 核心依赖 |
| python-docx | 1.2.0 | 核心依赖 |
| pypdfium2 | 5.14.0 | 把 PDF 页面渲染成 PNG |
| paddleocr | 3.7.0 | 表格识别流水线 |
| paddlex[ocr] | 3.7.2 | PaddleOCR 的推理后端 |
| paddlepaddle | 3.3.1 | CPU 版；GPU 版见下文 |

Python 3.14/3.15 没有对应的 PaddlePaddle 轮子，`pip install` 会报
`No matching distribution found for paddlepaddle`。这类环境请只用核心功能，或改用 3.13。

## 2. 安装 CPU 版（最简单）

在项目根目录、已激活虚拟环境的前提下：

```powershell
python -m pip install -e ".[ocr]"
```

这会安装 `pypdfium2`、`paddleocr`、`paddlex[ocr]` 和 CPU 版 `paddlepaddle`。
不需要 NVIDIA 显卡，速度较慢，适合偶尔处理几页扫描件。

## 3. 安装 GPU 版（NVIDIA）

GPU 版和 CPU 版的包名不同，**不能同时安装**。先卸载 CPU 版，再装 GPU wheel：

```powershell
python -m pip uninstall -y paddlepaddle

python -m pip install --no-cache-dir "https://paddle-whl.cdn.bcebos.com/stable/cu129/paddlepaddle-gpu/paddlepaddle_gpu-3.3.1-cp313-cp313-win_amd64.whl"

# 只装核心包与 OCR 依赖，注意不要加 [ocr] 额外项，
# 否则会再次安装 CPU 版 paddlepaddle 并覆盖 GPU 版。
python -m pip install -e .
python -m pip install pypdfium2==5.14.0 paddleocr==3.7.0 "paddlex[ocr]==3.7.2"
```

注意：

- 上面的 wheel 是 **Windows x64 + CPython 3.13 + CUDA 12.9** 构建；
- RTX 50 系（Blackwell / sm_120）需要 CUDA 12.8+ 的构建，cu129 可用；
- 其他 Python/平台请去 PaddlePaddle 官方安装页选择对应 wheel，不要混用；
- 装完 GPU 版后不要再执行 `pip install -e ".[ocr]"`，它会重装 CPU 版 `paddlepaddle` 并覆盖 GPU 版；需要补装依赖时用上面的显式包名。

检查当前是不是 GPU 版：

```powershell
python -c "import paddle; print(paddle.device.is_compiled_with_cuda(), paddle.device.cuda.device_count())"
```

输出 `True N` 表示检测到 N 张 CUDA 显卡；`False 0` 表示当前是 CPU 版。

## 4. 首次运行与模型缓存

第一次调用 OCR 时会自动下载模型。代码默认使用 **ModelScope** 源，避免 HuggingFace 在国内超时：

```python
os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "modelscope")
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
```

模型缓存在：

```text
%USERPROFILE%\.paddlex\official_models
```

常见模型约 0.6 GB（版面分析、表格结构、OCR 检测/识别）。下载完成后可离线运行。
删除该目录会触发重新下载；不要把该目录提交到 Git。

可以通过环境变量覆盖模型源，例如：

```powershell
$env:PADDLE_PDX_MODEL_SOURCE = "huggingface"
```

## 5. 后端选择

`pdf_extractor` 根据环境和令牌自动选择：

| 条件 | 后端 |
| --- | --- |
| `PDF_WORD_OCR_MODE=local` | 本地 PaddleOCR |
| `PDF_WORD_OCR_MODE=cloud` | 云端 PP-StructureV3 |
| `auto` + 有 CUDA | 本地 GPU |
| `auto` + 无 GPU + 有令牌 | 云端 |
| `auto` + 无 GPU + 无令牌 | 本地 CPU |

强制模式：

```powershell
$env:PDF_WORD_OCR_MODE = "local"   # 或 cloud / auto
```

本地模式不会上传任何内容；云端模式会把整份 PDF（最多前 100 页）发送到百度 AI Studio。

## 6. 云端令牌

### 方式 A：界面配置（推荐）

打开程序菜单「帮助 → 云端 OCR 设置…」，粘贴 AI Studio 访问令牌并保存。
令牌写入：

```text
%USERPROFILE%\.pdf_word_extractor\config.json
```

文件内容形如：

```json
{
  "access_token": "你的令牌",
  "base_url": ""
}
```

### 方式 B：环境变量

```powershell
$env:PADDLEOCR_ACCESS_TOKEN = "你的令牌"
```

备用变量名是 `PDF_WORD_OCR_TOKEN`。环境变量优先级高于配置文件。

### 方式 C：自定义配置文件路径

```powershell
$env:PDF_WORD_OCR_CONFIG = "D:\secrets\paddleocr.json"
```

### 安全提醒

- 不要把令牌写进源码、README、Issue 或截图；
- 不要把 `config.json` 提交到 Git（`.gitignore` 已忽略 `.pdf_word_extractor/`）；
- 云端是第三方服务，会收到整份 PDF，机密/个人信息文档请使用 `local` 模式；
- 云端有每日页数、单文件页数等配额，超限会返回 429，具体以 AI Studio 控制台为准。

## 7. 为什么默认关闭 MKLDNN

PaddlePaddle 3.3.1 的 CPU 默认启用 oneDNN/MKLDNN，在部分 Windows CPU 上会抛：

```text
NotImplementedError: (Unimplemented) ConvertPirAttribute2RuntimeAttribute not support
[pir::ArrayAttribute<pir::DoubleAttribute>] ...
```

因此 `ocr_engine.py` 初始化流水线时显式传入 `enable_mkldnn=False`。
如果你要改初始化参数，请保留这个设置，除非确认当前 Paddle 版本已修复。

## 8. 性能与进度

- 页面渲染默认 150 DPI，兼顾速度与识别率；
- 本地 GPU 首次初始化约十几秒（加载模型），之后每页通常 1–3 秒；
- CPU 会明显更慢，复杂表格可能每页十几秒；
- 界面状态栏会显示「正在 OCR 第 N 页」之类的进度，不会卡死窗口；
- 一份 PDF 中只有需要 OCR 的页面才会调用模型，文本层完整的页面直接走 pdfplumber。

## 9. 常见问题

| 现象 | 原因 / 处理 |
| --- | --- |
| `No matching distribution found for paddlepaddle` | 当前 Python 不是 3.10–3.13；换 3.13 或只装核心依赖 |
| `缺少依赖：paddleocr` | 执行 `python -m pip install -e ".[ocr]"` |
| 首次 OCR 卡在下载 | 检查网络；确认 `PADDLE_PDX_MODEL_SOURCE=modelscope` |
| `CUDA out of memory` | 降低并发/页数，或改用云端；本程序一次只处理一页 |
| 云端 401/403 | 令牌错误或过期，重新在 AI Studio 获取 |
| 云端 429 | 超出配额，等次日或改用本地 |
| 识别结果乱码 | 扫描分辨率太低、旋转/倾斜严重；先做图像预处理 |
| GPU 版装完仍是 CPU | 误装了 `paddlepaddle`，卸载后重装 `paddlepaddle-gpu` |

## 10. 进一步阅读

- PaddleOCR 仓库：<https://github.com/PaddlePaddle/PaddleOCR>
- PaddlePaddle 安装页：<https://www.paddlepaddle.org.cn/install/quick>
- 百度 AI Studio：<https://aistudio.baidu.com/>
