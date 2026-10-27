# meetingVAR UI 改版：合并前交接

## 上传与版本位置

这份交接针对 `codex/ui-hierarchy` 上的 UI 改版。它最初基于 `a9abea5`（桌面壳 0.1.2），并没有直接覆盖仓库较新的桌面实现。

截至 2026-10-27，远端默认分支 `main` 位于 0.8.0（`91aa4dc`），开发分支 `feat/agent-runtime` 位于 0.17.0（`00605df`）。新版本仍有项目资料、全库理解和预算/试跑操作，但整体已改成 Agent 工作区、抽屉和审批卡片。因此，合并到新版本前要把本次的视觉层级与交互行为移植到对应新界面，不能整组覆盖新分支的 `desktop/web/index.html`、`app.js`、`style.css`。

本次草稿 PR：[PR #35 · meetingVAR desktop UI hierarchy and handoff skill](https://github.com/DanielXiong8/meetingVAR/pull/35)。GitHub 当前标记它与 `feat/agent-runtime` 有冲突；这份 PR 用来审查原实现与交接材料，移植到最新界面并解决冲突后再进入可合并状态。

本分支待审内容包括：

- `desktop/web/index.html`、`desktop/web/app.js`、`desktop/web/style.css`：简化项目理解区域，增加清晰的操作层级、状态提示、交互反馈与蓝紫配色。
- `desktop/preview_ui.py`、`desktop/preview/mock.js`：只在普通浏览器预览时注入的合成桥数据，不调用真实引擎、凭证、模型、麦克风或 API。
- `.agents/skills/meetingvar-desktop-ui/`：给后续 Codex/项目 agent 使用的 UI 交接 skill。
- `docs/UI_REFACTOR_0.1.2.md`：本次详细改动与 macOS 浏览器模拟记录。

## 接手建议

1. 先读 `.agents/skills/meetingvar-desktop-ui/SKILL.md` 和本说明。
2. 确认要集成的最新分支与提交。若目标是 `feat/agent-runtime`，保留现有聊天工作区、抽屉与审批动作卡，只把本次信息架构、色彩、提示、徽章和重试交互适配到当前项目面板。
3. 保留真实操作边界：浏览步骤只定位并聚焦控件，不自动触发预算/API/模型调用；小样失败后的重试维持小样范围；部分阅读结果不能显示成全库完成；错误不能被显示成成功。
4. 核对 HTML 元素 ID 与 JavaScript 事件绑定、pywebview 桥参数和异步状态。需要改 Python 桥、模型、存储或会议流程时，先在 PR 里写清接口变化与理由。
5. 在 Windows pywebview/WebView2 下完成 `docs/UI_HANDOFF_0.1.2.md` 中适用于当前代码的验收项；较新分支以当前源码和文档为准。特别检查原生目录选择、中文/空格/长路径、键盘操作、窗口缩放和安装包。

## 已做检查及边界

本次实现记录于 `docs/UI_REFACTOR_0.1.2.md`：检查了 JS 语法、HTML ID/绑定、差异空白；在 macOS 普通浏览器用合成数据检查 1280×800 与 900×600 布局、输入组、错误重试、项目理解状态、选词写入确认、Tooltip 键盘操作、减少动态效果和模拟字幕。该记录证明的是当时的本地浏览器预览行为，不是 Windows 实机验收，也不是实时语音识别准确率或真实 API/模型效果测试。

Windows 验收仍需在目标运行环境完成。真实讯飞、实时兴趣 API、音频设备、屏幕采集、完整模型环境及安装包均未由合成预览验证。不要把本地 mock 的凭证检测、理解、写词或会议保存当成真实服务成功。

## Codex agent 接手入口

- 先打开 [草稿 PR #35](https://github.com/DanielXiong8/meetingVAR/pull/35) 查看原始实现、设计记录和冲突状态。
- 按 `.agents/skills/meetingvar-desktop-ui/SKILL.md` 在 `feat/agent-runtime` 当前提交上移植适用的界面层级与交互；保留新版 Agent 工作区、抽屉和审批卡片。
- PR 合并前完成 `.agents/skills/meetingvar-desktop-ui/references/windows-review.md` 所列 Windows WebView2 验收；补充目标分支上的实际验证结果。

### 可直接交给 Codex agent 的任务

请先读本交接文档及 `.agents/skills/meetingvar-desktop-ui/SKILL.md`，再审查 PR #35。以当前 `feat/agent-runtime` 最新提交为代码基线，把 PR 中仍适用的信息层级、颜色、状态提示和键盘交互移植到新版项目面板；保留新版 Agent 工作区、抽屉和审批卡片，不要把 0.1.2 的旧页面整体覆盖到新分支。解决移植产生的冲突，运行适用于当前分支的检查，并明确列出 macOS 模拟和 Windows WebView2 实测的区别。若无法进行 Windows 实测，在 PR 中标成待验收，不要宣称通过。完成后更新交接文档中的目标提交、改动文件和验证结果，并提交一个供审查的 PR；不要直接合并。
