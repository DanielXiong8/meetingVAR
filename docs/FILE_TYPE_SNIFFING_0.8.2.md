# 0.8.2 按内容识别文件类型

日期：2026-10-20

## 问题

`相关体育活动链接.txt` 实际是 Word 文档（DOCX 本质上是 ZIP 容器，文件头为 `PK\x03\x04`）。0.8.1 只看扩展名，把它当成 `.txt` 读取；解不开后查看器显示“二进制文件”，项目树也只按 `.txt` 展示，用户看到的仍是错的类型。

## 修复

新增 `memory/files.py:detect_file_type()`：先读文件头，再按内容判断类型，扩展名只作兜底。

- ZIP 容器：检查内部成员，区分 DOCX（`word/document.xml`）、PPTX（`ppt/slides/*.xml`）、XLSX（`xl/workbook.xml`）和普通 ZIP
- PDF：`%PDF-`
- 图片：PNG/JPEG/GIF/WebP/BMP/TIFF/ICO
- 音视频：MP4/M4A/MKV/AVI/WAV/MP3/FLAC/OGG
- 旧版 Office：OLE 复合文档魔数
- 压缩包：ZIP/RAR/7Z/GZIP/BZIP2/XZ
- 文本：扩展名在支持列表内且没有命中二进制魔数

`read_text_safely()` 改为内容优先：伪装成 `.txt` 的 DOCX/PPTX 会正常提取正文；普通 ZIP 仍不会被当作文本，避免把压缩包内容误读进项目记忆。

## 查看器与文件树

- 文件树按真实类型显示图标，并在扩展名不符时显示 `DOCX` / `ZIP` 等橙色标记
- 查看器元信息显示“Word 文档（后缀 .txt 与内容不符）”
- DOCX/PPTX/PDF 即使改名也能预览提取文本
- 普通 ZIP 显示条目清单、数量与大小，不自动解压
- XLSX 明确提示暂不支持表格预览
- 真实文件 `相关体育活动链接.txt`（14,350 字节）现在识别为 DOCX，并提取出 4 条活动链接正文

## 验证

- 专项 23 项通过：内容识别、改名 DOCX、普通 ZIP 清单、文件树标记、路径越界
- 普通 ZIP 仍返回 `None` 给文本抽取层；DOCX 伪装文本可读
- 全量测试与安装包验证结果见 `docs/DEVELOPMENT_STATUS.md`

## 边界

- XLSX 只识别、不解析单元格（避免引入表格依赖）
- 旧版 `.doc/.ppt/.xls` 由纯 Python CFB/CLX、TextAtom、BIFF SST 解析；代码不再调用 LibreOffice
- 加密 ZIP 只列条目，不尝试解密
- 扫描件/图片型 PDF 没有文本层时仍提示用系统应用打开