# 0.8.0：多模态文件读取与文件查看器

日期：2026-10-20。

## 1. 模型可读取的格式

`src/meetingvar/memory/files.py` 统一文本抽取：

| 类型 | 扩展名 | 方式 |
|---|---|---|
| 纯文本/Markdown | `.md/.markdown/.txt/.rst/.log` | UTF-8/BOM/GBK 解码 |
| PDF | `.pdf` | PyMuPDF `page.get_text`；回退 `pdftotext -layout` |
| Word | `.docx` | OOXML `word/document.xml` 解析（零第三方依赖） |
| PowerPoint | `.pptx` | OOXML 幻灯片 + 备注 `<a:t>` 文本解析 |
| 旧版 Office | `.ppt/.doc` | LibreOffice headless 转 txt，按 mtime/size 缓存 |
| XML/HTML/SVG | `.xml/.html/.htm/.xhtml/.svg` | 文本读取 |
| 表格 | `.tsv/.csv` | 文本读取 |
| 代码 | `.py/.js/.ts/.tsx/.jsx/.c/.cpp/.h/.hpp/.java/.go/.rs/.rb/.php/.cs/.swift/.sh/.ps1/.sql/.css/.scss/.vue/.svelte` 等 | 文本读取 |
| 配置 | `.json/.jsonl/.yaml/.yml/.toml/.ini/.cfg` | 文本读取 |

项目扫描、会前关注、全库理解、词表抽取统一使用 `SUPPORTED_TEXT_SUFFIXES`；项目扫描的“原始词表”使用 `QUICK_TEXT_SUFFIXES`（排除 PDF/旧版 Office）并限制 80 个文件，避免大文件夹扫描被 PDF 解析拖到超时。

## 2. 不支持/受限

- **图片（PNG/JPG/WebP/BMP/TIFF）**：不作为模型文本输入；查看器可直接显示。
- **视频/音频**：不作为模型输入；查看器可播放小文件，其他用系统应用打开。
- **扫描版 PDF**：若没有文本层，提取为空；未接 OCR（用户可另用 OCR 工具）。
- **加密 PDF/旧版 Office**：无法提取时返回空/不支持，查看器提示用系统应用打开。
- **PPT/DOC**：需要机器安装 LibreOffice；未安装时只能元数据/系统打开。
- 二进制未知格式：查看器显示元数据 + 系统打开。

## 3. VS Code 式文件查看器

- 项目页文件树每个文件可点击（也支持 Enter/Space）。
- 右侧打开查看器（覆盖在对话/抽屉上）：
  - 文本/代码：行号 + 等宽字体，最多显示 6000 行 / 200,000 字符；
  - 图片：棋盘背景居中等比缩放（15MB 内内嵌）；
  - PDF/Office：显示提取文本 + “完整排版请用系统应用打开”；
  - 视频/音频：15MB 内内嵌播放，超出则提示系统打开；
  - 二进制：元数据 + 系统打开。
- “用系统应用打开”调用本机默认程序。
- 路径安全：只允许项目根目录内、非符号链接、非 `..` 路径；`read_project_file` 与 `open_project_file` 均校验。

## 4. 验证

- 469 项测试通过（新增 PDF/PPTX/XML/TSV/代码/图片路径安全/预览 API 测试）。
- 实测：
  - `01.算法概论.pdf` 提取 24,938 字；
  - `CST LAB1 Embrace DSA report.docx` 提取 21,996 字；
  - PPTX 提取幻灯片 + 备注；
  - `program.txt` 提取 11,906 字；
  - `C:\Users\Lenovo\Documents` 834 个可读文件扫描约 33 秒（原先后台扫描超 180 秒）。
- 项目树节点带相对路径与类型标记；文件查看器通过 pywebview API 读取，不直接把项目目录暴露为 HTTP 静态目录。

## 5. 边界

- PDF 文本质量取决于文本层；扫描件没有 OCR。
- PPT/DOC 依赖 LibreOffice，分发安装包不包含 LibreOffice。
- 15MB 以上图片/音视频不内嵌；查看器只显示元数据。
- 模型读代码/XML/TSV 时仍受 16K 本地上下文和分块预算限制，不能保证一次读完超大仓库。
- 真实 UI 文件树点击仍需用户在安装版窗口验收（当前自动化受前台其他窗口影响）。