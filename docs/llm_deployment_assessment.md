# 语言模型部署评估（2026-10-11）

## 结论与架构边界

先用已经验证的 DeepSeek API 完成语言模型链路；本机适合尝试 4B 四位量化模型，暂不需要租服务器。
这是硬件和接口评估，不是本地推理性能验收：本轮没有下载权重、安装 GPU 推理引擎或测量本地 tokens/s。

“本地语言模型”指下载权重后在自己的 CPU/GPU 上推理，不是模拟回答。
Python 虚拟环境只隔离软件依赖，不是虚拟机，也不会增加显存。
当前仿真测试里的 ASR 桩更不是已部署的语音模型。

meetingVAR 已把可选语言模型收敛到 `src/meetingvar/llm.py` 的兼容接口：
云 API、本机服务、私有服务器可以替换地址/模型；不要因此重写采集、L1/L2/L3 触发、证据池和反馈闭环。
断网、额度不足和回答截断仍须保留离线模板/规则层。
模型不能替代音频证据，不能编造发言、负责人或时间点。

## 本机实际条件

PowerShell/CIM 和 `nvidia-smi` 检查结果：

| 项目 | 实际值 |
|---|---|
| 系统 | Windows，MECHREVO JIAOLONG Series |
| CPU | Ryzen 9 7845HX，12 核 / 24 线程 |
| 内存 | 31.20 GiB（标称 32GB） |
| 独立 GPU | RTX 5070 Ti Laptop，12227 MiB 显存，驱动 572.84 |
| 磁盘剩余快照 | C: 约 51 GiB，D: 约 200.6 GiB，E: 约 78.4 GiB |
| Python | 项目 `.venv` 为 3.10.22 |
| 本地推理现状 | 项目环境无 torch/transformers/llama_cpp；未找到 Ollama/Docker 命令；约定的 D:/env/models 不存在 |

显存以 `nvidia-smi` 为准；Windows 的 AdapterRAM 字段可能溢出为 4GB，不能据此判断显卡只有 4GB。
仓库旧脚本里的 RTX 5060 8GB、硬编码 D: 路径不是当前设备检测结果。

## Hugging Face 候选

首选评估 [Qwen 官方 Qwen3-4B-Instruct-2507](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507)：
4B 文本指令模型、Apache-2.0、仅非思考模式，符合当前文本抽取/纪要接口。
Transformers 路线至少要能识别 Qwen3（官方说明低于 4.51.0 会失败）。
选择它是针对当前任务和接入成本的工程判断，不代表已证明中文会议质量合格。

量化候选来自 [LM Studio 社区转换仓库](https://huggingface.co/lmstudio-community/Qwen3-4B-Instruct-2507-GGUF/tree/main)，
不是 Qwen 官方量化发布；仅下载 `Qwen3-4B-Instruct-2507-Q4_K_M.gguf`，不要下载所有量化版本。
本轮从 Hugging Face 元数据核对的固定版本：

```text
repo: lmstudio-community/Qwen3-4B-Instruct-2507-GGUF
revision: 4edb920b6f14e3b9284d4502a6485103d72cde05
filename: Qwen3-4B-Instruct-2507-Q4_K_M.gguf
bytes: 2497280448
sha256: 8cdb57cbb880d313736a9bc4e3d3d2485f145b5e19cf33783746e753e82641fc
```

单文件约 2.5GB（十进制），不是运行显存。
以 8K 上下文、单并发为起点，规划约 4–6 GiB 推理显存是**粗估而非实测**，还需为 ASR、桌面和缓存留余量。
4B BF16 的纯权重已约 8GB，算上缓存及 ASR 并行更紧张，不作为本机优先路线。
不要把模型标称长上下文当成本机能承载的上下文；长会议应分块且保留证据引用。

[Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B) 可作为后续图文候选，支持图像但默认思考。
当前客户端只发文本，换模型名不会自动读懂截图；需另做图文输入和质量验证，暂不替换基线。

## 实施路线：三种方式互不排斥

| 方式 | 适用场景 | 当前建议 |
|---|---|---|
| DeepSeek 云 API | 快速验证语言模型功能，无需下载权重 | 已完成短请求验证；真实会议文本会离机，使用前需遵守参会者隐私约定 |
| 本机 GGUF + llama.cpp | 数据不出机、单人试用、可断网 | 下一阶段优先；独立进程，通常无需 Python 推理虚拟环境 |
| 私有 GPU 服务器 | 多人共享、持续在线、较高并发 | 暂不采购；先有实测工作负载，再选显存和并发配置 |

本机后续部署步骤（本轮未执行）：

1. 从 [llama.cpp 官方发布](https://github.com/ggml-org/llama.cpp/releases) 选择兼容本机驱动的 Windows 后端包；核对来源和版本。CUDA 不兼容时先查兼容性或测试 CPU 后端，不自动升级驱动。
2. 下载上面的固定版本单文件到仓库已忽略的 `models/`，用 `Get-FileHash -Algorithm SHA256` 对照校验。
3. 用提供 `llama-server.exe` 的发行包启动；以下为待验证命令模板：

```powershell
.\llama-server.exe -m "<绝对路径>\Qwen3-4B-Instruct-2507-Q4_K_M.gguf" -c 8192 -ngl 99 --host 127.0.0.1 --port 11438 --alias meetingvar-qwen3-4b
```

4. 本地模式配置 `MEETINGVAR_LLM_BASE_URL=http://127.0.0.1:11438/v1`、`MEETINGVAR_LLM_MODEL=meetingvar-qwen3-4b`，将 `MEETINGVAR_LLM_THINKING` 留空。不要把 DeepSeek Key 发送给本地或其他服务；无鉴权回环服务用空值，开启鉴权时用独立密钥。不要覆盖当前云端配置而丢失凭据。
5. 使用相同合成样例比较 JSON 可解析率、负责人/时间点忠实度、长文分块质量、首字延迟、总耗时、峰值显存，并与真实 ASR 同时运行检查 OOM。

兼容接口、alias 和鉴权参数见 [llama.cpp 服务文档](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)。
若使用 Transformers，单建 `.venv-llm` 隔离推理依赖，不能直接把现有 BF16 脚本指向 GGUF。
服务器需独立鉴权、TLS/私网和访问控制；不将开发 HTTP 服务裸露公网。

## 仍缺少的验收

DeepSeek 接通的是文本语言模型，不会自动补齐 SenseVoice/FunASR、VAD、说话人模型和 Windows 音频设备。
当前真实语音模型栈尚未验收，合成音频 E2E 不能证明真实录音识别质量。
本地模型性能、实际会议纪要忠实度和云端完整会话费用也尚未测量；不要据此宣称整产品已全功能可交付。
