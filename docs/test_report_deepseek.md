# DeepSeek 接入与配置分享验证（2026-10-11）

## 范围与结果

实际项目客户端接入成功；不是桩回答，也没有运行完整会议付费总结。
API Key 未输出到命令日志或报告；测试仅发送英文合成行动项，没有发送真实会议或私人项目文件。

| 检查 | 结果 |
|---|---|
| 鉴权后的 `/models` | 成功；返回 `deepseek-flash`、`deepseek-v4-pro` |
| 配置 | `https://api.deepseek.com`、`deepseek-flash`、非思考模式 |
| 真实调用路径 | `.env` → config → `meetingvar.llm.chat` → 官方 `/chat/completions` |
| 合成任务 | 抽取负责人/行动项，返回可解析 JSON 且包含两个非空字段 |
| 本次输出上限 | 128 tokens |
| 服务端 usage | 输入 34、输出 17、合计 51 tokens |
| 耗时 | 1.99 秒，一次样本，不代表稳定性能 |
| 结束原因 | `stop`，未截断 |
| 真实配置加密 | Fernet 解密内容逐字节等于原 `.env` |
| 凭据保护 | 密文不含明文 API Key/解密密钥；两种明文文件被 Git 忽略且未跟踪 |

没有查询实际扣款；token 使用量不是人民币费用。供应商侧额度需用户在控制台确认。

## 实现边界

- 默认单次输出上限 2048 tokens，提供 `MEETINGVAR_LLM_MAX_TOKENS`。
- DeepSeek 显式设置 `MEETINGVAR_LLM_THINKING=disabled`；其他服务默认不发送该扩展。
- 无效配置不发请求；达到输出上限、空最终回答、格式异常均回落规则/模板。
- 不改采集、触发、证据池、反馈校准架构。输出上限不是输入长度或整场金额上限。

## 回归复现

从仓库根目录用 PowerShell 执行。清空云端设置仅作用于测试进程；不修改用户 `.env`。

```powershell
$env:MEETINGVAR_LLM_BASE_URL=''
$env:MEETINGVAR_LLM_MODEL=''
$env:MEETINGVAR_LLM_API_KEY=''
$env:MEETINGVAR_LLM_THINKING=''
$env:MEETINGVAR_LLM_MAX_TOKENS='2048'
.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py' -v
.venv\Scripts\python.exe -m compileall -q src scripts tests
```

真实付费短请求只在本轮手动执行一次，不加入默认自动测试。
新增 7 项请求契约测试全部模拟网络，覆盖输出上限、DeepSeek 开关、错误配置、截断、空回答和异常 choice。
完整环境 44 项通过；无可选依赖的隔离目录 26 项通过、18 项跳过。编译检查通过。

## 未验证项

本地 Qwen 实际推理、真实语音模型/设备、完整会议文本生成质量及整场费用未验收。
部署判断和后续验收条件见 [语言模型部署评估](llm_deployment_assessment.md)。
当前密文可交给协作者，但解密密钥必须由用户另行私发；不得上传或写入 PR。
