# meetingVAR 项目交接说明

> 给新加入的同学，以及他们的 AI 助手。  
> 最后更新：2026-10-27 · 当前基线：`feat/agent-runtime` / `v0.17.0`  
> 先读本文，再读 [5 分钟冲刺方案](FINAL_SPRINT_5MIN.md)。

---

## 0. 给 AI 的启动提示

如果你是接手这个项目的 AI，先确认你被分配的是哪条线：

- **UI 线**：只改 `desktop/web/`，必要时由负责人改 `desktop/app.py` 的桥接 API。
- **边缘/QA 线**：只改 `scripts/`、`tests/`、`docs/`，以及准备 demo 数据和测试报告。

无论哪条线，遵守这些规则：

1. **不要读、打印、复制或提交任何密钥**：`%APPDATA%\meetingVAR\ui_config.json`、`.env`、`meetingvar.env.enc`、`OneDrive\Desktop\keys\` 都不行。
2. **不要提交、上传或删除 `data/`**：里面有真实会议音频、屏幕帧和转写。
3. **不要改核心引擎**：`src/meetingvar/agent/`、`src/meetingvar/kg/`、`src/meetingvar/rag.py` 默认不属于你；要改先问负责人。
4. **不要改版本号、tag、安装包、发布目录**。
5. **不要编造指标**：没有人工逐字稿就不报 CER/WER；没有人工标注就不报实体/关系 P/R；没有真人评测就不报“用户满意度”。
6. **不要用“重写”解决问题**：先读现有实现，做最小改动，保持现有风格。
7. **每次改动必须说明**：改了哪些文件、为什么、跑了什么测试、结果是什么。
8. **不确定就问**，不要猜产品决策。

---

## 1. 这个项目是什么

**meetingVAR 不是“又一个会议纪要 App”。**

它是一层装在 Windows 电脑上的**会议证据 Agent**：

> 平时不落盘，只在会议内容命中你的项目关注点时，保存前后一段音频 + 屏幕 + 转写；  
> 会后你能找回原话、看到谁改了口，并导出一份可校验的证据包。

一句话对外话术：

> **别人帮你记下来，meetingVAR 帮你证出来。**

它解决的不是“怎么把会总结得更好”，而是：

- AI 纪要漏掉了最重要的一句，怎么找回那 30 秒？
- 上周说周五交付，这周谁改成了下周三？原话是什么？
- 会上做出的决定，能不能拿出一份带时间、来源、哈希的证据？

比赛背景：SDG Open Hack 2026 · Hack16 · 联想分赛场。  
赛场里“AI 办公助手/会议纪要”很拥挤，所以产品必须站在**证据层**，不要站在“纪要工具”红海里。

### 三个签名能力

| 能力 | 做什么 | 现在状态 |
|---|---|---|
| **Evidence Pack** | 把命中片段打包成 ZIP：音频、屏幕帧、转写、manifest、SHA256 | 后端完成，前端缺查看/校验页 |
| **Commitment Ledger** | 承诺账本：谁承诺了什么、谁改了口、哪场会议、原话 | 后端完成，前端未渲染表格 |
| **Find Moment** | 自然语言找回具体 30–90 秒片段 + 时间戳 + 屏幕帧 | 后端完成，前端未渲染片段卡 |

---

## 2. 当前状态：不要误判

### 已经有的

- 版本 `0.17.0`，543 项测试通过（约 72 秒）。
- Windows 桌面壳：pywebview 5.4 + WebView2，深色三栏 UI。
- 本地采集：WASAPI 系统环回 + 麦克风、DXGI/mss 录屏。
- 本地/讯飞 ASR、VAD、说话人聚类、术语纠错。
- 三层触发：热词 / 语义 / 手动 VAR；命中后保存片段。
- 项目记忆：Markdown 事实源 + SQLite FTS5 + BGE 向量。
- RAG：项目文档 + 已保存会议转写。
- 知识图谱：SQLite 属性图、时序边、GraphRAG、Cytoscape 页面。
- Agent Runtime：工具调用、审批、Trace、引用校验、无固定步数主循环。
- 证据三技能后端 + CLI。
- 安装包/静默安装/自检。

### 还没有的 / 不能声称的

- **没有人工逐字稿 CER/WER**。跨引擎字符差异只能叫“分歧率”，不能叫准确率。
- **没有 20 场人工实体/关系 P/R**。知识图谱质量不能报“准确率 95%”。
- **没有真人 30 天记忆评测**。合成基准通过不等于真人可用。
- **Evidence Pack 前端查看/校验页没有**。
- **Commitment Ledger / Find Moment 的富 UI 没有**：后端返回 `ledger`/`segments`，`desktop/web/app.js` 目前只显示文本。
- **HUD / Magic Bay 没有真机验证**。
- **干净 Windows 新机仍要手动配置引擎 Python**：安装包只带 UI 壳，不带 Python/模型。
- **“法律级取证”不能讲**：SHA256 只能证明文件完整性，不是司法鉴定。

### 关于 Magic Bay

官方《Lenovo MagicBay 接口开放指南 V0.4.3》明确：Magic Bay 是笔记本 A 面顶盖中央的磁吸 Pogo-Pin 接口，模块端走 USB2.0/USB3.0，5V/3A/15W；可做影像、显示、感知、音频、AI 处理等模块，指南配图里的在售模组包含**摄像头形态**和**小屏形态**。

但指南开放的是**模块端**规范；主机端协议/IP 不开放，禁止反向工程，也没有“第三方 App 直接控制现有模组”的万能 SDK。

所以我们的接入策略是：

> 如果 Magic Bay 摄像头是标准 **UVC**、小屏是标准 **Windows 显示器**，meetingVAR 直接用系统接口接入；  
> 摄像头 = 证据摄像头，小屏 = 证据 HUD。  
> 不碰主机私有协议，不等厂商 SDK；拿到主机侧 API 再加 `LenovoAdapter`。

比赛期只做“标准设备接入”，不做硬件模块、不逆向、不声称已集成。没有真机也能用任意第二块屏 + 普通摄像头演示。详见 [MAGIC_BAY_INTEGRATION.md](MAGIC_BAY_INTEGRATION.md)。

---

## 3. 系统架构（够你改 UI 的版本）

```text
系统声音/麦克风/屏幕
        │
        ▼
  capture/  ──►  live.py / pipeline.py  ──►  data/clips/、data/pool/
                                                   │
                                                   ▼
                                    ASR / 纠错 / 说话人 / 三层触发
                                                   │
                                                   ▼
                              RAG + 项目记忆 + 知识图谱 + 证据技能
                                                   │
                                                   ▼
                              ChatAgent / AgentLoop（工具、审批、引用）
                                                   │
                                                   ▼
                              desktop/app.py（pywebview 桥）
                                                   │
                                                   ▼
                              desktop/web/（HTML/CSS/JS 界面）
```

关键边界：

- **桌面壳不 import 引擎核心**。它启动子进程：`python -m meetingvar go/record ...`。
- 引擎通过 stdout 发结构化事件 `@@MEETINGVAR_EVENT`；桌面通过 stdin 发 `:stop` / `:var`。
- UI 只消费 `window.pywebview.api.*` 返回的 JSON。
- 没有 React/Vue，没有前端构建链；就是原生 HTML/CSS/JS。

### 主要模块

| 路径 | 作用 | 谁能改 |
|---|---|---|
| `src/meetingvar/capture/` | 音频/屏幕采集 | 负责人 |
| `src/meetingvar/transcribe/` | ASR、纠错、说话人、讯飞 | 负责人 |
| `src/meetingvar/live.py`、`pipeline.py` | 实时管线、触发、片段 | 负责人 |
| `src/meetingvar/pool/` | 片段池 | 负责人 |
| `src/meetingvar/report/` | 纪要生成 | 负责人 |
| `src/meetingvar/rag.py` | 项目/会议 RAG | 负责人 |
| `src/meetingvar/agent/` | Agent、记忆、证据技能 | 负责人 |
| `src/meetingvar/kg/` | 知识图谱 | 负责人 |
| `desktop/app.py` | 桌面后端/桥接 API | 负责人（UI 同学可提需求） |
| `desktop/web/` | 全部前端 | UI 同学 |
| `scripts/` | 评测、demo、工具脚本 | QA 同学 |
| `tests/` | 单元/集成测试 | QA 同学 + 负责人 |
| `docs/` | 文档 | 所有人，但不要改历史记录 |

### 数据放在哪

- 片段/转写/录屏：`data/clips/`、`data/pool/`、`data/reports/`
- 项目记忆：`<项目目录>/meetingvar/agent_memory/`
- 证据包：`<项目目录>/meetingvar/evidence_packs/`
- 桌面配置：`%APPDATA%\meetingVAR\ui_config.json`（**含 API key，不能外传**）
- 本地模型：`D:\env\models\models`（SenseVoice、VAD、CAM++、标点、BGE、Qwen 等）

`data/` 不是测试数据，是真实会议材料。**不要提交、不要上传、不要删除。**

---

## 4. 本地环境与命令

### 路径

| 资源 | 路径 |
|---|---|
| 工作仓库 | `C:\Users\Lenovo\projects\meetingVAR` |
| 引擎 Python | `D:\miniconda3\envs\meetingvar\python.exe` |
| UI Python | `D:\env\ui-venv\python.exe` |
| 本地模型 | `D:\env\models\models` |
| 已安装版 | `C:\Users\Lenovo\AppData\Local\Programs\meetingVAR` |

### 跑 UI（开发模式）

```bat
cd /d C:\Users\Lenovo\projects\meetingVAR
D:\env\ui-venv\python.exe desktop\app.py
```

### 测引擎

```bat
D:\miniconda3\envs\meetingvar\python.exe desktop\engine_bridge.py check
```

预期输出一行 JSON：`{"ok": true, "python": "3.10.x", "meetingvar": "..."}`。

### 跑测试

```bat
D:\miniconda3\envs\meetingvar\python.exe -m unittest discover -s tests -v
```

当前基线：543 项通过，0 跳过，约 72 秒。  
不要用 Python 3.13；项目锁定 `>=3.10,<3.11`，`audioop` 在 3.13 已移除。

### 打包（只在负责人要求时做）

```bat
desktop\build\build_exe.bat
desktop\build\build_setup.bat
```

输出：`dist\meetingVAR.exe`、`dist_installer\meetingVAR-setup-0.17.0.exe`。  
**不要自己改版本号或覆盖发布目录。**

### 当前配置坑

安装版首次打开可能提示“未配置引擎 Python”。设置 → 常规：

- 引擎 Python：`D:\miniconda3\envs\meetingvar\python.exe`
- 仓库目录：`C:\Users\Lenovo\projects\meetingVAR`

保存后点“测试引擎”。  
如果切到“仅手动 VAR”，现场说到关键句时要按“手动 VAR”，否则不会保存片段。

---

## 5. UI 线：你要做什么

### 你的边界

- 只改 `desktop/web/index.html`、`app.js`、`style.css`，以及新增的 `hud.html` / `hud.js` / 对应 CSS。
- 需要新后端 API 时，先告诉负责人，不要直接改 `src/`。
- 不要引入 React/Vue/构建链；保持原生 JS。
- 不要改现有中文文案风格；不要用大面积高饱和色。
- 不要为了演示造假数据写进生产路径；mock 只放在开发模式或单独文件。

### P0：证据包查看/校验页

现在聊天只显示 ZIP 路径。你要做：

- 左栏：音频播放器 + 转写文本；
- 右栏：屏幕关键帧缩略图；
- 底部：文件列表 + 每个文件 SHA256 + “校验”按钮；
- 顶部：session、clip、时间窗、来源设备；
- 空状态/失败状态要清楚。

后端返回形状（先按这个做 mock）：

```json
{
  "ok": true,
  "zip_path": "...",
  "directory": "...",
  "manifest": {
    "session_id": "...",
    "clip_id": "...",
    "window": {"start": 0, "end": 60},
    "files": [{"path": "audio.wav", "sha256": "..."}]
  },
  "files": 9,
  "quotes": 6
}
```

验收：点 3 下能看到证据包内容；点“校验”显示全部通过；没有网络也能打开。

### P0：承诺账本表格

现在后端返回 `msg.ledger`，前端没渲染。你要做：

- 表格列：承诺/行动、负责人、截止、状态、首次出现、最后更新；
- 变更 diff：`due: 周五 → 下周三`、`owner: 小王 → 小李`；
- 每行“原句”按钮，跳到对应会议/时间戳；
- 空状态：“还没有可追踪的承诺”。

```json
{
  "title": "周五交付第一版",
  "owner": "小王",
  "due": "周五",
  "status": "active",
  "changes": [{"field": "due", "from": "周五", "to": "下周三"}],
  "evidence": [{"kind": "turn", "session_id": "...", "turn_ids": ["..."]}]
}
```

### P1：Find Moment 片段卡

现在后端返回 `msg.segments`，前端没渲染。你要做：

- 每张卡：时间、说话人、原句、匹配原因、屏幕帧；
- 播放/跳转按钮；
- 多个结果按分数排序，去重；
- 空状态：“没有找到匹配片段，换个说法试试”。

```json
{
  "session_id": "go_...",
  "start": 123.4,
  "end": 156.7,
  "text": "...",
  "clip_id": "...",
  "score": 0.82,
  "why": ["预算", "三十万"]
}
```

### P1：HUD / 第二屏

- 新增 `hud.html` + `hud.js` + `hud.css`；
- 8 英寸屏只放 4 行：`REC 00:47`、`本地`、最近一句、当前承诺/证据数；
- 大字号，1.5 米可读；
- 先做静态页 + mock 状态；第二窗口由负责人加 `desktop/app.py`；
- 任意 Windows 第二屏都能用，不依赖 Magic Bay SDK。

### UI 验收

- 1280×800 和 1920×1080 都不溢出；
- 深色主题对比度可读；
- 键盘 Tab 能到主要按钮；
- 没有 console error；
- 不加载外部 CDN（离线可用）；
- 证据包/账本/片段卡都能用 mock 数据独立预览。

---

## 6. 边缘/QA 线：你要做什么

### 你的边界

- 只改 `scripts/`、`tests/`、`docs/`，以及新建 demo 数据目录；
- 不改 `src/`、`desktop/web/`、`desktop/app.py`；
- 不删除 `data/`、不重装、不改注册表、不打包发布；
- 所有“验证通过”的结论必须附命令、时间、输出摘要。

### P0：Demo 数据

准备两场预置会议 + 一段现场脚本：

- 会议 A：`第一版周五交，小王负责。`
- 会议 B：`周五来不及，改到下周三，小李来盯。`
- 现场会议 C：`预算先按三十万算。这个风险会影响下周三的联调。`

目标：

- A/B 能生成承诺账本，出现 `周五 → 下周三` 的 diff；
- C 能产生至少 1 个 clip，供现场生成证据包；
- 如果 C 不命中，改用手动 VAR 标记。

### P0：设备与现场清单

- 麦克风/扬声器/第二屏枚举；
- 录屏权限、WebView2 版本；
- 断网/联网两种状态；
- 安装版配置是否指向正确引擎；
- 输出一份“现场设备清单 + 风险”。

### P0：Fallback

- 录一份 5 分钟完整 fallback 视频；
- 准备 60 秒会议音频，可在麦克风失败时走系统环回；
- 确认视频在演示电脑上能播放（不要依赖在线链接）。

### P1：测试矩阵

至少覆盖：

- 在线 / 断网；
- 单屏 / 双屏；
- 有麦克风 / 无麦克风；
- 本地 ASR / 讯飞；
- 零命中 / 有命中；
- 证据包 SHA256 全过；
- 账本 diff 正确；
- Find Moment 返回正确时间戳；
- 连续 3 次 5 分钟内完成。

### QA 报告格式

```text
测试项：
环境：
命令：
结果：通过/失败
证据：日志路径/截图路径/输出摘要
备注：
```

不要写“应该可以”“理论上没问题”。没跑就是没跑。

---

## 7. 5 分钟 Demo 是什么样

详细逐秒脚本在 [FINAL_SPRINT_5MIN.md](FINAL_SPRINT_5MIN.md)。这里只给分工版：

| 时间 | 内容 | 谁操作 |
|---|---|---|
| 0:00–0:25 | 钩子：AI 纪要漏掉最重要的一句 | 负责人讲 |
| 0:25–1:20 | 现场 60 秒采集 | QA 操作，负责人讲 |
| 1:20–2:15 | 预置承诺账本：周五 → 下周三 | 负责人讲，UI 保证表格可读 |
| 2:15–3:05 | 现场生成证据包 + SHA256 校验 | 负责人讲，UI 保证校验页 |
| 3:05–3:50 | 找回预算 30 秒 | 负责人讲，UI 保证片段卡可播放 |
| 3:50–4:35 | HUD / 第二屏 | QA 拖屏，负责人讲 |
| 4:35–5:00 | 三列对比 + 收尾 | 负责人讲 |

原则：

- 账本用预置数据，现场只演示“采集 + 证据包”；
- 现场采集必须提前验证“说这几句话一定命中”；
- 全场点击不超过 6 次；
- 必须有 fallback 视频。

---

## 8. AI 协作规则（给同学的 AI）

### 分支与提交

- 不要直接改 `main`，不要直接 push `main`。
- 从 `feat/agent-runtime` 开分支：
  - UI：`feat/ui-evidence-viewer`
  - QA：`test/demo-readiness`
- 一个 PR 只做一件事；提交信息用中文或英文都可以，但要说明“改了什么/为什么”。
- 不要在 PR 里提交 `data/`、模型、安装包、`ui_config.json`、`.env`、密钥。

### 改代码前

1. 先读相关文件，不要凭印象改。
2. 先确认这个文件属于你的线。
3. 先写清楚假设；不确定就问。
4. 先做最小改动，不要顺手重构。

### 改代码后

1. 跑相关测试；UI 改动至少跑 `unittest` 里 desktop 相关测试。
2. 在真实 app 里点一遍，不只看看代码。
3. 报告：文件、行为变化、测试命令、结果、截图/日志。
4. 如果失败，贴完整错误，不要只说“失败”。

### 绝对不要

- `git reset --hard`、`git clean -fd`、删除 `data/`；
- 改版本号、tag、发布目录；
- 把真实会议音频/转写发到外部服务；
- 把 API key 写进代码、文档、截图或提交；
- 为了“让测试过”而改测试断言；
- 编造 CER/WER/P/R/用户满意度；
- 把“未验证”写成“已完成”。

---

## 9. 术语表

| 术语 | 意思 |
|---|---|
| **选择性取证** | 不录全场，只在命中项目关注点时保存前后片段 |
| **clip / 片段** | 一次命中保存下来的音频 + 屏幕帧 + 转写 |
| **trigger / 命中** | L1 热词、L2 语义、L3 手动 VAR 触发的保存 |
| **Evidence Pack** | 可校验证据包 ZIP |
| **Commitment Ledger** | 承诺账本，追踪谁改了口 |
| **Find Moment** | 按自然语言找回 30–90 秒片段 |
| **Fact / 事实** | 项目记忆里的一条结构化事实（承诺/决定/风险等） |
| **RAG** | 检索增强生成，先找资料再回答 |
| **KG** | 知识图谱，实体 + 关系 + 时间 |
| **HUD** | 第二屏上的极简状态界面 |
| **Magic Bay** | 联想模块化配件生态，当前按副屏/状态屏理解 |
| **本地链路** | 不依赖云端的 ASR/索引/证据导出 |

---

## 10. 当前已知问题 / TODO

### P0（不做会影响 demo）

- 证据包查看/校验页缺失；
- 承诺账本表格未渲染；
- Find Moment 片段卡未渲染；
- 现场 60 秒采集是否能稳定产生 clip，未做 dry run；
- 安装版引擎 Python 需要手动配置；
- 演示电脑/第二屏/麦克风未确认。

### P1（有时间再做）

- HUD 第二窗口；
- consent 勾选 + 时间戳（现在 manifest 写死 `local-capture`）；
- Find Moment 跳转播放；
- 断网模式标识；
- 证据包 ZIP 的 verify CLI；
- Magic Bay 真机适配（拿到 SDK 后）。

### 明确不做

- 不做通用任务管理/日程；
- 不做系统级数字分身；
- 不做全量录屏；
- 不做另一个会议纪要 App；
- 不在主 demo 讲 100 节点知识图谱；
- 不宣称法律级取证；
- 不编造准确率。

---

## 11. 第一次上手（30 分钟）

给新同学：

1. 读本文 + [FINAL_SPRINT_5MIN.md](FINAL_SPRINT_5MIN.md)。
2. 打开 `desktop/README.md`，知道 pywebview 的桥怎么工作。
3. 跑一次测试（约 72 秒），确认环境。
4. 跑一次 UI（`D:\env\ui-venv\python.exe desktop\app.py`）。
5. 在设置里确认引擎 Python 和仓库目录。
6. 打开一个项目，点一次“开始会议”，看实时字幕。
7. UI 同学：用 mock JSON 把证据包页先画出来。
8. QA 同学：列现场设备清单，录 60 秒测试音频。

不要第一天就改核心。

---

## 12. 一句话记住

> **我们不是把会议总结得更漂亮，而是让会议里的每一句承诺都能被找回、被校验、被证明。**