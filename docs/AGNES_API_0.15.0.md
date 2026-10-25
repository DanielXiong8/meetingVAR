# Agnes 3.0 Flash 接入记录（0.15.0，2026-10-25）

## 结论

`C:\Users\Lenovo\OneDrive\Desktop\keys\agnes ai\.env` 里的 key **可以调用 Agnes 3.0 Flash**。

- 端点：`https://apihub.agnes-ai.com/v1`
- 模型：`agnes-3.0-flash`、`agnes-2.5-flash`（`/v1/models` 均可见）
- 协议：OpenAI `chat/completions`
- 简单请求：HTTP 200，约 6.8 秒
- 不需要代理；本机测试用无代理 opener，避免 10808 代理放大延迟

## 重要：3.0 是推理模型

Agnes 3.0 Flash 会把推理 token 计入 `max_tokens`。KG 抽取的 2 句话测试：

| 配置 | 耗时 | completion | reasoning | 结果 |
|---|---:|---:|---:|---|
| 默认/4k，thinking disabled | 41–81 秒 | 1800–4096 | 1800–4096 | 经常 `finish_reason=length`，无最终 JSON |
| `reasoning_effort=none`，4k | **51.5 秒** | 879 | 0 | 合法 JSON，质量好 |
| `reasoning_effort=none`，2k | 93 秒 | 958 | 无 | 合法 JSON |

Agnes 2.5 Flash：

| 配置 | 耗时 | completion | 结果 |
|---|---:|---:|---|
| `reasoning_effort=none` | **4.7 秒** | 419 | 合法 JSON |
| `thinking=disabled` | 8.7–42.7 秒 | 391–4096 | 行为不稳定，可能全是 reasoning、无最终内容 |

**给 meetingVAR 的实际建议：知识图谱抽取用 `agnes-2.5-flash` + `reasoning_effort=none`；需要更高质量、且能接受约 1 分钟/块时再用 3.0 Flash + none。** 本机安装版已按 2.5 Flash + none 配置；想切 3.0 可在设置页点预设。

## 实测抽取质量

同一段两句话：

```text
我们决定采用第二方案，由小林负责，周五交付第一版。
测试时间不足是一个风险，可能影响交付。
```

Agnes 2.5 Flash + none，约 **6.6 秒**：

- 6 个实体：小林、小王、第二方案、交付、周五、测试时间不足
- 5 条关系：
  - 交付 --assigned_to--> 小林
  - 交付 --due_on--> 周五
  - 第一版 --owned_by--> 小林
  - 第二方案 --owned_by--> 小林
  - 测试时间不足 --blocks--> 交付

对比本地 Qwen3-4B：4–5 实体、0–1 条关系，且经常把类型名当实体名。Agnes 2.5 已明显可用。

## 产品接入

0.15.0 新增：

- `MEETINGVAR_LLM_REASONING_EFFORT` 配置，支持 `none/low/medium/high`；
- 设置页“推理强度”下拉；
- “Agnes 3.0 Flash 预设”和“Agnes 2.5 Flash（快速）”按钮，自动填 Base URL/模型/推理强度；
- API Key 仍需用户在设置页粘贴，**不写进源码或仓库**；
- 模型设置页新增“保存模型设置”按钮。

## 使用方法

1. 设置 → 模型设置 → 厂商选“自定义 API”；
2. 点“Agnes 2.5 Flash（快速）”或“Agnes 3.0 Flash 预设”；
3. 粘贴 key（从 `.env` 复制，不要提交到 git）；
4. 推理强度选 `none`；
5. 保存模型设置；
6. 运行知识图谱抽取：

```bash
python -m meetingvar kg-ingest --project "D:/项目/X" --session s1
```

## 注意

- 3.0 Flash 的延迟约 50–90 秒/块，不适合默认对整场会议逐块抽取；
- 2.5 Flash 的 `reasoning_effort` 只接受 `none` 等少数值，传 `minimal` 会 HTTP 400；
- 推理模型可能在 content 外单独返回 reasoning；`llm.py` 只把最终 content 当答案，截断时不采用；
- API key 只在本机配置/环境变量中，文档、日志、Trace 不记录密钥。