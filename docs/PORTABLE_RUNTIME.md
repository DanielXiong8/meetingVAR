# 干净 Windows 运行策略

目标：不假设目标电脑预装 LibreOffice、pdftotext、ffmpeg、Python、llama.cpp 或任何办公软件。所有能力要么由 Python 轮子提供，要么由应用自己的 `runtime/` 目录提供。

## 已移除的宿主应用依赖

| 能力 | 旧实现 | 现在 |
|---|---|---|
| PDF 文本 | PyMuPDF → `pdftotext` 外部程序 | PyMuPDF → `pypdf`（纯 Python）→ 标准库 Flate/文本流兜底；不调用 pdftotext |
| Word 97-2003 `.doc` | LibreOffice headless | 纯 Python CFB + Word piece table（CLX）解析，保留正文顺序 |
| PowerPoint 97-2003 `.ppt` | LibreOffice headless | 纯 Python CFB + TextCharsAtom/TextBytesAtom 扫描 |
| Excel 97-2003 `.xls` | LibreOffice headless | 纯 Python CFB + BIFF SST/LABEL 解析 |
| 视频合成/抽音 | 系统 `ffmpeg` | `imageio-ffmpeg` 轮子自带 ffmpeg；应用 `runtime/bin/ffmpeg.exe` 优先；系统 PATH 仅最后兜底 |

`soffice` 和 `pdftotext` 的调用已从文件读取代码中移除；`.doc/.ppt/.xls` 完全由纯 Python 解析。

## `runtime/` 布局（可携带发行版）

安装目录或源码根目录下的 `runtime/`（已被 `.gitignore` 忽略）按以下布局放置运行资产：

```
runtime/
  python/python.exe          # 独立 Python 3.10（引擎）
  repo/                      # meetingvar 源码（含 src/）
  models/                    # ASR / 说话人 / 本地 LLM 权重
  llama.cpp/                 # llama-server.exe（可选，本地模型）
  bin/ffmpeg.exe             # 可选；没有时用 imageio-ffmpeg 轮子
```

桌面壳会自动发现：

- `runtime/python/python.exe` 作为引擎 Python
- `runtime/repo` 作为引擎源码目录
- `runtime/models` 作为 `MEETINGVAR_MODELS`
- `runtime/llama.cpp` 下的 `llama-server.exe`
- `runtime/bin/ffmpeg.exe` 作为 `MEETINGVAR_FFMPEG`
- `runtime/models/**/*.gguf` 作为默认本地模型

安装包当前仍是 UI 壳；若要让一台干净机器直接运行完整会议能力，需要把上述 `runtime/` 随安装目录一起分发（体积由 Python/torch/ASR 模型决定）。文件读取、图片/音视频预览、旧版 Office 解析已经不再依赖外部应用。

## 验证

```powershell
python -m unittest tests.test_standalone_runtime tests.test_read_text_safely tests.test_file_preview -v
```

三项契约：

1. 旧版 `.doc/.ppt/.xls` 在 `_soffice` 被禁用时仍能提取正文；
2. PDF 在无外部工具时仍有 `pypdf`/标准库路径；
3. ffmpeg 来自 Python 轮子或应用自带目录，系统 PATH 不是必需。

## 仍属于发行资产、不是宿主应用依赖

- Python 引擎与 torch/FunASR 等轮子；
- ASR、说话人、VAD、标点和本地 LLM 权重；
- llama.cpp 原生服务（可选，使用自定义 API 时不需要）；
- WebView2 Runtime（Windows 11 通常自带；Windows 10 需随安装包安装）；
- 讯飞凭证（用户自己的服务凭据，不属于运行时）。