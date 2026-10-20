# 第三方组件与许可证清单（发布前复核）

本清单是商业发布准备材料，不替代各组件的完整许可证文本。发布前必须用实际锁定的版本重新生成并附上完整 NOTICE/源获取方式。

## 桌面壳

| 组件 | 用途 | 许可证 | 商业注意 |
|---|---|---|---|
| pywebview | 桌面 WebView 壳 | BSD-3-Clause | 可商用 |
| pythonnet / clr_loader | WinForms/WebView2 互操作 | MIT | 可商用 |
| Microsoft Edge WebView2 Runtime | 渲染引擎 | Microsoft 许可 | 随安装包分发需遵守 Microsoft 条款 |

## 文档/数据读取

| 组件 | 用途 | 许可证 | 商业注意 |
|---|---|---|---|
| pdfminer.six | PDF 文本 | MIT | 可商用；已替代 AGPL 的 PyMuPDF |
| pypdf | PDF 兜底 | BSD-3-Clause | 可商用 |
| Pillow | 图像处理 | HPND | 可商用 |
| imageio-ffmpeg | 自带 ffmpeg 二进制 | BSD-3-Clause（包）；内附 ffmpeg 为 GPL/LGPL 构建 | 分发 ffmpeg 需附其许可证与源码获取方式；如需更宽松条款改用 LGPL 构建 |
| jieba | 中文分词 | MIT | 可商用 |
| scikit-learn | 词表/相似度 | BSD-3-Clause | 可商用 |
| pypinyin | 拼音近似纠错 | MIT | 可商用 |

## 音频/模型

| 组件 | 用途 | 许可证 | 商业注意 |
|---|---|---|---|
| PyTorch | 推理 | BSD-3-Clause | 可商用 |
| FunASR | ASR/说话人 | MIT | 可商用；模型权重需单独核对 |
| ModelScope | 模型分发 | Apache-2.0 | 模型各自许可需逐项核对 |
| SenseVoiceSmall / CAM++ / VAD / Punc | 模型权重 | 以模型卡为准 | 商业分发前逐项确认 |
| Qwen3-4B GGUF | 本地 LLM | Apache-2.0（以模型卡为准） | 可商用；保留 NOTICE |
| llama.cpp | 本地推理服务 | MIT | 可商用 |
| PyAudioWPatch | 系统音频采集 | MIT | 可商用 |
| dxcam / mss | 屏幕采集 | MIT | 可商用 |
| OpenCV | 图像处理 | Apache-2.0 | 可商用 |
| cryptography | 许可证签名验证 | Apache-2.0 / BSD-3-Clause | 可商用 |

## 已主动移除的高风险依赖

- **PyMuPDF**：AGPL-3.0 或商业授权。商业闭源分发不应默认使用；0.9.x 已改为 pdfminer.six/pypdf/标准库链。
- **LibreOffice**：MPL/LGPL 且体积大；已改为纯 Python CFB 解析。
- **pdftotext / 系统 ffmpeg**：不再是运行前提；ffmpeg 由 imageio-ffmpeg 或应用 runtime 提供。

## 发布检查

- [ ] 锁定实际 `pip freeze` 版本并生成完整许可证文本
- [ ] 为 ffmpeg 附 GPL/LGPL 许可证和源码获取地址
- [ ] 逐项核对 ASR/LLM 模型卡许可
- [ ] 在“关于”页展示本清单和隐私/条款入口
- [ ] 法务复核后再对外发布