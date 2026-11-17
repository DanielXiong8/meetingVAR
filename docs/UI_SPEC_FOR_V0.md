# meetingVAR UI 规范与功能清单

> 给 Vercel / v0 的交接文档。  
> 目标：**先做静态 UI + mock 数据，后接 pywebview 接口**。  
> 当前基线：`feat/agent-runtime` / `v0.17.0`，但本文描述的是**目标 UI**，不是现有页面的简单美化。

---

## 0. 一句话

meetingVAR 是 **Windows 本地的会议证据 Agent**，不是会议纪要 SaaS。

> 平时不落盘，只在命中项目关注点时保存音频、屏幕和转写；  
> 会后能找回原话、看到谁改了口，并导出一份可校验的证据包。

UI 应该像：

> **会议案卷 + Agent 工作台**

而不是：

> 通用 SaaS 仪表盘 / 视频会议回放 / 聊天机器人套壳。

---

## 1. 风格总结

### 1.1 关键词

**冷静、取证、密度、可核对、低饱和、桌面工具感。**

- 不是“炫技型 AI 产品”；
- 不是“温暖笔记软件”；
- 不是“营销落地页”；
- 是“你会长时间盯着看、需要信任的案卷界面”。

### 1.2 色彩

| 角色 | 色值 | 用途 |
|---|---|---|
| 页面底色 | `#0F1115` | 主背景 |
| 一级表面 | `#151A22` | 卡片、侧栏 |
| 二级表面 | `#1B212B` | 卡片内分区、hover |
| 边框 | `#29313D` | 1px 分隔 |
| 主文字 | `#E8EDF7` | 标题、正文 |
| 次文字 | `#B7C2D3` | 说明、列表 |
| 弱文字 | `#8D99AA` | 元数据、空状态 |
| 极弱 | `#5D6979` | 时间戳、辅助 |
| 主强调 | `#8FADFF` | 主按钮、选中、链接 |
| 暖强调 | `#E4AE85` | 证据、承诺、待确认 |
| 成功 | `#7DD39A` | 校验通过、生效中 |
| 危险 | `#E58B8B` | 删除、失败、冲突 |
| 本地 | 绿色 `#7DD39A` | `本地` 标识 |
| 云端 | 蓝色 `#8FADFF` | `云端增强` 标识 |

原则：

- 强调色只用于“当前操作 / 证据 / 状态”，不要满屏。
- 状态不能只靠颜色，必须同时有文字或图标。
- 深色界面避免纯黑 `#000`，用 `#0F1115` 降低对比疲劳。

### 1.3 字体与字号

- 中文：`"Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", system-ui, sans-serif`
- 数字/哈希/时间戳：`"Cascadia Code", "Consolas", monospace`
- 字号：
  - 11px：辅助、标签
  - 12px：元数据、说明
  - 13px：列表正文
  - 14px：正文
  - 16px：卡片标题
  - 20–24px：页面小标题
  - 28–34px：页面主标题（只在首页/Agent 使用）
- 行高：正文 1.55；列表 1.45；标题 1.25。
- 不要用全大写英文做长标题；`PERSONAL EVIDENCE AGENT` 这种 eyebrow 可以。

### 1.4 布局

桌面三栏，宽度 ≥ 1280：

```text
┌────────────── topbar 56px ──────────────┐
│ back/forward  breadcrumb   local/REC  ⚙ │
├──────────┬──────────────────────┬───────┤
│ sidebar  │ main                 │ rail  │
│ 248–260  │ max 1280             │ 300–340│
│          │                      │       │
└──────────┴──────────────────────┴───────┘
```

- 侧栏：项目、最近对话、新建、搜索、技能、会议库、知识图谱、设置。
- 主区：页面标题 + 当前任务 + 主内容。
- 右栏：上下文/元数据/证据详情；在 1280 以下折叠为抽屉。
- 间距：4px 网格；卡片内 16–20px；页面区块 24–32px。
- 圆角：10px（小）、14px（卡片）、18px（主面板）。
- 阴影：很轻，主要靠边框和表面色分层；不要大面积发光。

### 1.5 组件规则

#### 按钮

- 主操作：实心 `#8FADFF`，深色字；一屏最多 1 个。
- 次操作：透明底 + 1px 边框。
- 幽灵操作：无边框，hover 才出现背景。
- 危险操作：红色边框/文字；删除必须二次确认。
- 高度：32px（表格/工具条）、36px（表单）、40px（主操作）。
- 图标按钮：32×32，必须有 tooltip/aria-label。

#### 卡片

- 默认：一级表面 + 1px 边框 + 14px 圆角。
- 卡片不是容器堆叠；同一屏最多 2 层嵌套。
- 证据卡必须有：来源、时间、状态、操作。

#### 输入

- 深色输入框 `#12161D`，边框 `#303948`。
- focus：蓝色边框 + 3px 低透明外环。
- 错误：红色边框 + 文字说明，不要只变红。

#### 标签 / 状态

```text
[本地] [云端] [REC] [已校验] [待确认] [已替代] [已过期]
[承诺] [决定] [行动项] [风险] [未决问题]
[麦克风] [系统声音] [屏幕] [Magic Bay]
```

- 状态标签必须带文字；颜色只是辅助。
- 证据来源标签用等宽小字，例如 `MIC` / `SYS` / `SCR`。

#### 表格

- 承诺账本、文件哈希、会议列表用表格。
- 表头 sticky；行高 40–44px；hover 整行。
- 数字/时间/哈希右对齐或等宽。
- 行内 diff 用 `旧 → 新`，旧值删除线/弱色，新值暖色。

#### 引用

- 回答后跟编号芯片 `[1] [2]`。
- hover 显示来源：`会议 · 00:14:32 · 小林`。
- 点击跳到原句/片段；不能只是装饰。

#### 空状态

```text
标题：还没有可追踪的承诺
说明：开一场会，或在聊天里说“记住：周五交付第一版”。
操作：开始会议 / 打开技能
```

空状态必须说明“为什么空 + 下一步做什么”。

#### 加载 / 错误

- 加载：骨架屏或明确进度，不用无限转圈。
- 错误：`role=alert`，给出重试动作；不要只说“失败”。
- 长任务：显示阶段、已完成块数、可取消、完成后保留。

### 1.6 动效

- 120–160ms；只用于 hover、展开、页面淡入。
- 尊重 `prefers-reduced-motion`。
- 不做弹跳、霓虹、3D 翻转。
- 录制状态可以有非常轻的呼吸点，但不要闪。

### 1.7 文案语气

- 短句、动词开头、说人话。
- 用“证据、原话、承诺、来源、校验”，不用“智能总结、赋能、一站式”。
- 不承诺“法律级/司法采信”；说“可校验完整性”。
- 不写“准确率 95%”这类没有人工评测的数字。

### 1.8 风格禁区

- 不要大 Hero、3D 插画、玻璃拟态、满屏渐变。
- 不要浅色主题（当前产品是深色阅读面）。
- 不要移动端 bottom sheet 直接搬桌面。
- 不要 100 节点图谱毛线球。
- 不要把每个数字都做成大卡片。
- 不要用 emoji 当主图标。
- 不要用颜色代替文字状态。
- 不要伪造数据/指标。

---

## 2. 页面地图

| 页面 | 作用 | 优先级 | 现状 |
|---|---|---|---|
| App Shell | 侧栏、顶栏、项目、最近对话、状态 | P0 | 已有 |
| Home / Chat | 对话、找原话、技能入口、开始会议 | P0 | 已有，需重构 |
| Meeting Console | 录制、实时字幕、屏幕预览、关注事项 | P0 | 已有，需重构 |
| Evidence Pack | 证据包查看、文件哈希、校验 | **P0（缺）** | 后端有，前端无 |
| Commitment Ledger | 承诺/改口/原句 | **P0（缺）** | 后端有，前端无表格 |
| Find Moment | 30–90 秒片段卡、播放 | **P1（缺）** | 后端有，前端无卡片 |
| Project | 项目选择、文件树、词表、全库理解 | P1 | 已有 |
| Library / Session | 会议列表、纪要、片段、音视频 | P1 | 已有 |
| Home / Chat（唯一 Agent 入口） | 深度检索、关注、事实、记忆、RAG 都在同一个 thread | P0 | 已统一 |
| Knowledge Graph | 图、时间线、证据、GraphRAG | P1 | 已有 |
| Settings | 引擎、模型、用量、合规、记忆、关于 | P1 | 已有 |
| HUD | 第二屏 4 行状态 | P1（缺） | 未实现 |
| File Viewer | 文档/图片/音视频预览 | P2 | 已有 |
| Skills Modal | 8 个快捷技能 | P2 | 已有 |

---

## 3. 功能清单

状态说明：

- ✅ **已接通**：UI + 后端可用。
- 🟡 **后端有，UI 缺/弱**：接口/CLI 存在，界面没呈现或只显示文本。
- 🧩 **规划中**：未实现，UI 先留位置。
- ⚠️ **需验证**：代码有，但真实设备/环境未验收。

### 3.1 全局 / 桌面壳

| 功能 | 用户看到什么 | 入口 | 状态 |
|---|---|---|---|
| 本地桌面应用 | pywebview + WebView2，无浏览器地址栏 | 启动 | ✅ |
| 项目切换 | 当前项目路径/名称 | 侧栏、Composer | ✅ |
| 新建对话 | 新会话 | 侧栏 `＋` / Ctrl N | ✅ |
| 全局搜索 | 搜项目/会话/技能 | 侧栏搜索 / Ctrl K | 🟡（需统一命令面板） |
| 技能菜单 | 8 个常用技能 | 侧栏 `✦` | ✅ |
| 最近对话 | 会话列表 | 侧栏 | ✅ |
| 项目列表 | 已知项目 | 侧栏 | ✅ |
| 本地/云端状态 | 当前模型来源、数据边界 | 顶栏 / Composer | 🟡（需强化） |
| 录制状态 | REC、计时、命中数 | 顶栏 / HUD | 🟡（需 HUD） |
| 文件树与预览 | 文本/Office/PDF/图片/音视频 | 项目右栏 | ✅ |
| 内容优先类型识别 | `.txt` 实为 ZIP/DOCX 也能识别 | 文件树 | ✅ |
| 本地媒体播放 | Range 请求，音视频/帧 | 会议库/证据页 | ✅ |
| 窗口/侧栏折叠 | 窄屏适配 | 顶栏 | ✅ |

### 3.2 会议采集

| 功能 | 用户看到什么 | 入口 | 状态 |
|---|---|---|---|
| 开始/结束会议 | 录制按钮、计时、收尾 | Home | ✅ |
| 系统声音 + 麦克风 | 双源采集，掉线降级 | 设置/会议控制台 | ✅ |
| 仅系统声音 | 在线会议远端 | 设置 | ✅ |
| 仅麦克风 | 线下会议 | 设置 | ✅ |
| 屏幕录屏 | 5fps、1600px、环形缓冲 | 会议控制台 | ✅ |
| 高清补录 | 命中后短片段 30fps（资源上限） | 自动 | ✅ |
| 实时字幕 | 临时字幕 + 定稿句 | 会议控制台 | ✅ |
| 本地 FunASR | 离线转写 | 设置 | ✅ |
| 讯飞 RTASR | 连续实时转写 | 设置 | ✅ |
| 讯飞 IAT | 分块听写备用 | 设置 | ✅ |
| 说话人聚类 | 场内说话人分离 | 会议控制台 | ✅ |
| 说话人命名 | 新说话人主动询问 | 弹窗 | ✅ |
| 跨会话声纹名册 | 自动识别熟人 | 自动 | ✅ |
| 手动 VAR | 标记“这一刻很重要” | 会议控制台/回车 | ✅ |
| API 兴趣判断 | 重要事件自动保存 | 设置/会议控制台 | ✅ |
| 仅手动 VAR | 不调用 API | 设置 | ✅ |
| 本场关注事项 | 会前输入关注点 | 会议控制台 | ✅ |
| 三层触发 | L1 热词 / L2 语义 / L3 手动 | 自动 | ✅ |
| 零命中不落盘 | 没有命中就没有会话文件 | 自动 | ✅ |
| 采集状态 | 音频电平、ASR、屏幕、说话人 | 会议控制台 | ✅ |
| 文件转写 | 录音/录像 → 讯飞 LFASR → transcript | 会议控制台 | ✅ |
| 会后纪要 | 薄纪要 + 厚 VAR 报告 | 会议库 | ✅ |
| 项目归档 | 纪要复制到 `<项目>/meetingvar/minutes` | 自动 | ✅ |
| 会后评审/校准 | useful/not useful 反馈 | CLI/Agent | 🟡（UI 缺） |

### 3.3 项目理解

| 功能 | 用户看到什么 | 入口 | 状态 |
|---|---|---|---|
| 选择项目文件夹 | 原生文件夹对话框/手输路径 | 项目页 | ✅ |
| 扫描项目 | 文档数、词表、跳过文件 | 项目页 | ✅ |
| 项目文件树 | 目录/文件/类型徽章 | 项目右栏 | ✅ |
| 文件预览 | 文本/表格/幻灯片/PDF/图片/音视频 | 点击文件 | ✅ |
| 术语抽取 | Markdown/人名/拉丁术语 | 项目页 | ✅ |
| 术语勾选/写入 | 只写用户确认的词 | 项目页 | ✅ |
| 全库理解预算 | 字数、块数、预计耗时 | 项目页 | ✅ |
| 全库理解 | 分层阅读、进度、覆盖率 | 项目页 | ✅ |
| 小样试跑 | 前 5 块 | 项目页 | ✅ |
| 取消/断点续跑 | 已完成块保留 | 项目页 | ✅ |
| 本地/云端理解模型 | 本地 llama.cpp / 自定义 API | 设置 | ✅ |
| 项目 RAG 索引 | 文档 + 会议转写 | 主聊天 | ✅ |
| 项目 RAG 问答 | 答案 + 来源引用 | 主聊天 | ✅ |
| 项目记忆事实 | action/decision/commitment/risk/open-question 等 | 主聊天 | ✅ |

### 3.4 会议库 / 会话详情

| 功能 | 用户看到什么 | 入口 | 状态 |
|---|---|---|---|
| 按项目分组 | 项目 → 场次 | 会议库 | ✅ |
| 搜索 | 项目/会话 ID/纪要 | 会议库 | ✅ |
| 场次信息 | 时间、句数、片段数、有无纪要 | 列表 | ✅ |
| 薄纪要 | Markdown 渲染 | 会话详情 | ✅ |
| 厚 VAR 报告 | 折叠查看 | 会话详情 | ✅ |
| 证据片段 | 每条命中：层级、证据、时间、音频/视频/帧 | 会话详情 | ✅ |
| 音频播放 | WAV | 会话详情 | ✅ |
| 高清视频 | HD clip mp4 | 会话详情 | ✅ |
| 视频片段 | 屏幕片段 | 会话详情 | ✅ |
| 屏幕帧 | 关键帧缩略图 | 会话详情 | ✅ |
| 跳转原句 | 时间戳/片段 | 会话详情 | 🟡（需强化） |

### 3.5 Agent / 对话

| 功能 | 用户看到什么 | 入口 | 状态 |
|---|---|---|---|
| 主聊天 | 自然语言问答 | Home | ✅ |
| 问候/能力介绍 | 简短帮助 | Home | ✅ |
| 最近会议总结 | 跨会议摘要 | 技能/聊天 | ✅ |
| 项目进度 | 事实、关注、未决 | 技能/聊天 | ✅ |
| 找原话 | 搜索 + 引用 | 技能/聊天 | ✅ |
| 证据包 | 确认后生成 ZIP | 技能/聊天 | 🟡（后端有，查看页缺） |
| 承诺账本 | 谁改口 | 技能/聊天 | 🟡（后端有，表格缺） |
| 找回 30 秒 | 片段卡 | 技能/聊天 | 🟡（后端有，卡片缺） |
| Agent 深度检索 | 计划、工具轨迹、答案、引用 | 主聊天 | ✅ |
| 引用校验 | 引用未通过时明确提示 | 回答下方 | ✅ |
| 自动续跑 | 无固定步数/观察压缩/尽力答案 | 聊天元信息 | ✅ |
| 审批动作卡 | 保存记忆/证据包/开始会议等 | 聊天 | ✅ |
| 工具轨迹 | 折叠的 tool observations | 聊天 | ✅ |
| 会前关注清单 | 生成、勾选、保存 | 主聊天 | ✅ |
| 会后事实候选 | 预览、勾选、写入 | 主聊天 | ✅ |
| 项目记忆问答 | 只依据事实回答 + 引用 | 主聊天 | ✅ |
| 项目记忆列表 | 状态筛选、搜索、置顶 | 主聊天 | ✅ |
| 记忆编辑 | 描述/正文/状态 | 主聊天 | ✅ |
| 记忆冲突处理 | 采用新/保留旧/合并 | 主聊天 | ✅ |
| 删除/彻底删除 | 软删/硬删 | 主聊天 | ✅ |
| 对话历史 | 新建/加载/列表 | 侧栏 | ✅ |
| 对话摘要 | 滚动摘要 | 自动 | ✅ |
| 跨对话检索 | 搜索历史对话 | Agent 工具 | ✅ |
| 使用统计 | Token/调用/峰值/模型占比 | 设置 | ✅ |
| 模型测试 | 少量 API 用量测试 | 设置 | ✅ |

### 3.6 证据技能

| 功能 | 用户看到什么 | 入口 | 状态 |
|---|---|---|---|
| Evidence Pack 生成 | 确认卡 → ZIP 路径 | 聊天/CLI | ✅ 后端 |
| Evidence Pack 查看 | 音频/转写/屏幕帧/文件列表 | 新页面 | **🧩 缺** |
| SHA256 校验 | 全部通过/失败 | 新页面 | **🧩 缺** |
| manifest 查看 | session/clip/时间窗/来源 | 新页面 | **🧩 缺** |
| 下载/打开目录 | 导出 ZIP | 新页面 | 🟡 |
| 证据来源设备 | MIC/SYS/SCREEN/Magic Bay | manifest | 🟡（需展示） |
| 录制同意 | 勾选 + 时间戳 | 会前 | **🧩 缺**（现在写死） |
| Commitment Ledger | 表格 + diff + 原句 | 新页面 | **🧩 缺** |
| Find Moment | 片段卡 + 播放 | 新页面 | **🧩 缺** |
| 时间戳跳转 | 点击回到会议 | 新页面 | 🟡 |

### 3.7 知识图谱

| 功能 | 用户看到什么 | 入口 | 状态 |
|---|---|---|---|
| 图谱状态 | 节点/边/类型统计 | KG 页 | ✅ |
| 规则构图 | 从事实/会议规则建图 | KG 页 | ✅ |
| LLM 抽取 | 当前会话/全部会话 | KG 页 | ✅ |
| 搜索实体 | 1/2/3 跳 | KG 页 | ✅ |
| as-of 查询 | 某天有效的图 | KG 页 | ✅ |
| 节点详情 | 类型、描述、置信度、时间线 | 右侧 inspector | ✅ |
| 边详情 | 关系、有效期、来源、原句 | 右侧 inspector | ✅ |
| 关系确认/驳回 | asserted/retracted | inspector | ✅ |
| 实体合并 | 选两个节点合并 | KG 页 | ✅ |
| GraphRAG 问答 | 图关系 + 文档 + 引用 | KG 页 | ✅ |
| 图谱压测 | 10万节点/100万边 | 非 UI | ✅ |

### 3.8 商业 / 安全 / 合规

| 功能 | 用户看到什么 | 入口 | 状态 |
|---|---|---|---|
| 离线许可证 | 激活/移除/状态/试用 | 设置→商业 | ✅ |
| 机器绑定/到期 | 状态文字 | 设置 | ✅ |
| 更新检查 | 清单、SHA256、下载/安装 | 设置 | ✅ |
| 崩溃报告 | 本机脱敏 JSON | 设置 | ✅ |
| 遥测开关 | 默认关闭，显式开启 | 设置 | ✅ |
| 隐私说明 | 本地文档 | 设置 | ✅ |
| 服务条款 | 本地文档 | 设置 | ✅ |
| 第三方许可证 | 本地文档 | 设置 | ✅ |
| 数据统计 | 各类数据大小 | 设置 | ✅ |
| 导出项目 ZIP | 导出 | 设置 | ✅ |
| 删除类别数据 | 输入 DELETE 确认 | 设置 | ✅ |
| 保留天数 | 配置项 | 设置 | 🟡（UI 未强化） |
| 支付/订阅 | — | — | 🧩 未实现 |
| RBAC/SSO/多租户 | — | — | 🧩 未实现 |
| 代码签名 | 构建钩子 | 非 UI | 🟡 |

### 3.9 HUD / Magic Bay

| 功能 | 用户看到什么 | 入口 | 状态 |
|---|---|---|---|
| 第二屏 HUD | REC、计时、最近一句、承诺/证据数 | 第二屏 | **🧩 未实现** |
| HUD 热插拔 | 拔掉回主屏 | 自动 | **🧩** |
| 标准 UVC 摄像头 | 作为证据摄像头 | 采集 | **🧩** |
| 标准外接显示器 | 作为 HUD | 显示 | **🧩** |
| Magic Bay 摄像头/小屏 | 若标准 USB 设备则直接接 | 采集/显示 | **🧩 待真机** |
| 厂商主机侧 SDK | 若联想提供再加 adapter | — | **🧩 待确认** |

### 3.10 系统 / 运维

| 功能 | 用户看到什么 | 入口 | 状态 |
|---|---|---|---|
| CLI | replay/record/digest/agent/kg/rag/evidence | 终端 | ✅ |
| 干净 Windows 运行时 | 无需 LibreOffice/ffmpeg/pdftotext | 安装 | ✅ |
| 引擎自检 | Python/meetingvar 版本 | 设置 | ✅ |
| 本地模型服务 | llama.cpp 启停/状态 | 设置 | ✅ |
| 自定义 API | OpenAI 兼容 | 设置 | ✅ |
| 讯飞凭证检测 | 不发音频，只握手 | 设置 | ✅ |
| 崩溃日志目录 | 打开 | 设置 | ✅ |
| 配置/数据目录 | 打开 | 设置 | ✅ |

---

## 4. 关键页面设计

### 4.1 Home / Chat

**目标**：用户第一眼知道“我能问会议、能开始记录、能看项目”。

结构：

```text
┌ sidebar ┬──────────────────────────────┬ rail ┐
│ 项目    │ 页面标题：新任务              │ 项目 │
│ 最近    │ 聊天消息                       │ 文件 │
│         │ 引用芯片 / 审批卡 / 工具轨迹    │ 证据 │
│         │ composer（项目/模型/发送）      │      │
└─────────┴──────────────────────────────┴──────┘
```

- 空状态：一句问题示例 + 4 个快捷入口（开始会议、会议纪要、项目进展、技能）。
- 不要大 Hero 占满屏；首屏应该能看到 composer。
- 消息气泡不要过度圆角；证据/引用比气泡重要。
- 录制时顶部显示极窄 `REC 00:47 · 本地 · 命中 2`。

### 4.2 Meeting Console

- 顶部：`REC` 主按钮、计时、状态、结束会议。
- 第二行：音频电平、ASR 状态、说话人数、屏幕状态。
- 左：实时字幕（可滚动，临时字幕灰色，定稿句正常）。
- 右：当前屏幕预览（小图，不抢字幕）。
- 下方：本场关注事项（可折叠）。
- 操作：手动 VAR、打开产物、文件转写。
- 零命中时明确显示“本场尚未保存任何片段”。

### 4.3 Evidence Pack 查看页（P0）

```text
┌ 顶部：Evidence Pack · session · clip · 00:14:32–00:15:28 ─┐
│ 来源：MIC / SYS / SCREEN · 生成时间 · 本地                    │
├──────────────┬──────────────────────────────┬─────────────┤
│ 音频播放器    │ 转写文本（当前句高亮）         │ 屏幕帧网格   │
│ 波形/计时     │ 说话人 / 时间戳               │ 12 张缩略图  │
├──────────────┴──────────────────────────────┴─────────────┤
│ 文件列表：audio.wav · transcript.txt · screens/ · manifest  │
│ SHA256：abcd…  [复制]  [校验]  ✅ 全部通过                   │
└────────────────────────────────────────────────────────────┘
```

必须有：

- manifest 摘要；
- 文件 SHA256（等宽、可复制）；
- 一键校验（逐文件状态）；
- 下载 ZIP / 打开目录；
- 校验失败时明确标红并说明哪个文件；
- 空状态/无音频/无屏幕帧的降级。

### 4.4 Commitment Ledger（P0）

- 表头：承诺/行动、负责人、截止、状态、首次出现、最后更新。
- 行内 diff：`due: 周五 → 下周三`、`owner: 小王 → 小李`。
- 右侧/展开：原句、会议、时间戳、片段播放。
- 筛选：生效中 / 已替代 / 已过期 / 冲突。
- 空状态：解释“承诺从哪来 + 怎么产生”。

### 4.5 Find Moment（P1）

- 搜索框 + 结果卡。
- 卡片：时间、说话人、原句、匹配原因、屏幕帧、播放。
- 按 score 排序；多结果去重。
- 点击播放跳到对应时间；没有 clip 时显示“只有转写，无片段”。

### 4.6 Project

- 当前项目路径 + 选择/读取。
- 扫描摘要：文档数、词表、跳过文件。
- 项目文件树 + 预览。
- 全库理解：预算 → 开始 → 进度 → 取消/小样。
- 术语候选：勾选后写入；不要自动写。

### 4.7 Library / Session

- 项目分组 + 搜索。
- 场次行：时间、句数、片段数、有无纪要。
- 详情 tabs：纪要 / 片段 / 厚报告。
- 片段：层级、证据、时间、音频/视频/帧。
- 时间戳可点击。

### 4.8 主聊天里的 Agent artifacts

**没有独立 Agent 页。** 主聊天是唯一 Agent 入口，所有能力都以同一个 thread 里的工具/卡片出现。

- 深度检索：问题 → 计划 → 工具轨迹 → 答案 → 引用。
- 会前关注：生成 → 聊天内卡片 → 确认保存。
- 会后事实：选最近/指定会议 → 聊天内卡片 → 确认写入。
- 项目记忆：直接问，聊天内列出生效事实；管理动作放右栏 artifact。
- RAG：显式说“用知识库回答”或“建立知识库索引”。
- 不要把所有卡片堆成仪表盘；主问题是“问一句，给证据”。
- 证据包/承诺账本/片段卡是 artifact，不是另一个 Agent。

### 4.9 Knowledge Graph

- 画布 60–70%，右侧 inspector 30–40%。
- 顶部：搜索、1/2/3 跳、as-of、构图/抽取/合并。
- 右侧：节点/边详情、时间线、证据、确认/驳回。
- Demo 只显示 6–10 节点；不要全量图。

### 4.10 Settings

7 个 tab：

1. 常规：引擎 Python、仓库、设备、音频源、保存方式。
2. 外观：深色说明（暂不做主题切换）。
3. 模型设置：ASR、本地 llama.cpp、自定义 API、推理强度、测试。
4. 使用统计：Token、调用、峰值、模型占比、热力图。
5. 商业与合规：许可证、更新、遥测、数据导出/删除、法律文档。
6. 记忆与 Agent：记忆目录、Trace、技能入口。
7. 关于：版本、运行方式、数据边界、法律。

### 4.11 HUD（第二屏）

8 英寸屏只放 4 行：

```text
REC 00:47        [本地]
最近一句：周五先交第一版。
承诺 1 · 证据 2
```

- 字号 ≥ 16px；1.5 米可读；
- 不放大段转写；
- 拔掉第二屏回主屏；
- 不显示全量录制暗示。

---

## 5. Mock API 约定

Vercel 预览不要直接调用 `pywebview.api`。新增一个适配层：

```js
// desktop/web/api.js
window.meetingVARApi = window.__MOCK__
  ? createMockApi()
  : createPywebviewApi(window.pywebview.api);
```

UI 只调用 `window.meetingVARApi.*`；后续接接口只替换 adapter。

### 5.1 方法清单

```text
bootstrap()
projects.pick()
projects.scan(path)
projects.understandPlan(path)
projects.understandStart(path, maxChunks)
projects.understandStatus()
projects.understandCancel()
projects.writeTerms(path, terms)

meeting.start(project)
meeting.stop()
meeting.manualVar()
meeting.answerPrompt(text)
meeting.pollEvents(since)

library.list()
library.detail(sessionId)

chat.new(project)
chat.list(project)
chat.load(project, conversationId)
chat.send(project, conversationId, text)
chat.action(project, conversationId, actionId)

agent.run(project, question)
agent.focusPreview(project)
agent.focusSave(project, items, sources)
agent.factsPreview(sessionId, project)
agent.factsSave(sessionId, project, items)
agent.ask(project, question)
agent.memory(project, query, status)
agent.memoryUpdate(project, name, payload)
agent.memoryDelete(project, name, hard)
agent.memoryPin(project, name, pinned)
agent.memoryResolve(project, name, resolution)

rag.status(project)
rag.index(project, includeMeetings)
rag.ask(project, question)

kg.status(project)
kg.build(project)
kg.ingest(project, sessionId, allSessions)
kg.search(project, query)
kg.neighbors(project, query, depth, asOf)
kg.timeline(project, query)
kg.path(project, src, dst, maxDepth)
kg.merge(project, source, target)
kg.reviewEdge(project, edgeId, status)
kg.ask(project, question)

evidence.packInfo(packId)
evidence.verify(packId)
evidence.export(packId)

ledger.list(project, query)
moment.find(project, query, sessionId, limit)

settings.get()
settings.set(updates)
settings.testEngine()
settings.testLlm()
settings.llmStart()
settings.llmStop()
settings.checkXfyun()

commercial.licenseStatus()
commercial.licenseActivate(token)
commercial.licenseRemove()
commercial.updateCheck()
commercial.updateInstall()
commercial.dataInventory(project)
commercial.dataExport(project, outDir)
commercial.dataDelete(project, categories, confirm)
commercial.openLegal(name)

system.openPath(path)
system.openDataDir()
system.openConfigDir()
system.pickMedia()
system.transcribeStart(path)
system.transcribeStatus()
```

### 5.2 核心类型

#### Project

```json
{
  "path": "D:\\项目\\AI for ALS",
  "name": "AI for ALS",
  "docCount": 12,
  "termCount": 38,
  "skipped": 2,
  "tree": [{"name":"docs","dir":true,"children":[]}],
  "lastOpened": "2026-11-02T10:00:00+08:00"
}
```

#### MeetingState

```json
{
  "running": true,
  "phase": "recording",
  "elapsed": 47,
  "hits": 2,
  "speakers": 3,
  "audioLevel": 0.42,
  "audioSource": "both",
  "asr": "讯飞 RTASR",
  "screen": "5fps · 1600px",
  "lastCaption": "周五先交第一版。",
  "partialCaption": "预算先按三十",
  "zeroPersist": true
}
```

#### SessionSummary

```json
{
  "sid": "go_20261102_1000",
  "project": "D:\\项目\\AI for ALS",
  "startedAt": "2026-11-02T10:00:00+08:00",
  "duration": 1820,
  "lines": 186,
  "clips": 3,
  "minutes": true,
  "hasTranscript": true
}
```

#### Clip

```json
{
  "clipId": "8f3a2c1d",
  "layer": "L2",
  "evidence": "交付时间变更为下周三",
  "start": 872.4,
  "end": 931.2,
  "audioUrl": "/media/...",
  "videoUrl": "/media/...",
  "hdUrl": "/media/...",
  "frames": [{"ts": 873.0, "url": "/media/..."}],
  "lines": 18
}
```

#### EvidencePack

```json
{
  "id": "go_20261102_1000_8f3a2c1d",
  "sessionId": "go_20261102_1000",
  "clipId": "8f3a2c1d",
  "title": "交付时间变更",
  "window": {"start": 872.4, "end": 931.2},
  "sources": [
    {"kind": "microphone", "name": "Microphone Array", "backend": "wasapi"},
    {"kind": "screen", "name": "DISPLAY1", "backend": "dxgi"}
  ],
  "files": [
    {"path": "audio.wav", "sha256": "a1b2...", "size": 983040},
    {"path": "transcript.txt", "sha256": "c3d4...", "size": 2048},
    {"path": "screens/frame_0001.jpg", "sha256": "e5f6...", "size": 81234}
  ],
  "verification": {"status": "passed", "checkedAt": "2026-11-02T10:31:00+08:00"},
  "zipUrl": "/media/...zip",
  "manifest": {}
}
```

#### LedgerRow

```json
{
  "id": "commitment_deliver_v1",
  "title": "周五交付第一版",
  "type": "commitment",
  "owner": "小王",
  "due": "周五",
  "status": "active",
  "firstSeen": "2026-10-28T10:12:00+08:00",
  "lastSeen": "2026-11-02T10:18:00+08:00",
  "version": 2,
  "changes": [
    {"field": "due", "from": "周五", "to": "下周三"},
    {"field": "owner", "from": "小王", "to": "小李"}
  ],
  "evidence": [
    {"sessionId": "go_20261028_1000", "start": 872.4, "end": 931.2, "quote": "第一版周五交，小王负责。"}
  ]
}
```

#### MomentSegment

```json
{
  "id": "moment_1",
  "sessionId": "go_20261102_1000",
  "start": 872.4,
  "end": 931.2,
  "speaker": "小林",
  "text": "预算先按三十万算……",
  "score": 0.82,
  "why": ["预算", "三十万"],
  "clipId": "8f3a2c1d",
  "frameUrl": "/media/..."
}
```

#### Fact

```json
{
  "name": "commitment-deliver-v1",
  "type": "commitment",
  "description": "周五交付第一版",
  "body": "小林承诺周五交付第一版。",
  "owner": "小林",
  "due": "周五",
  "status": "active",
  "confidence": 0.86,
  "version": 2,
  "pinned": false,
  "validFrom": "2026-10-28",
  "validTo": null,
  "sourceRefs": [{"kind": "turn", "sessionId": "go_20261028_1000", "turnIds": ["turn_42"]}]
}
```

#### AgentRun

```json
{
  "taskId": "task_...",
  "status": "done",
  "plan": ["搜索承诺", "比较版本", "核对原句"],
  "steps": [
    {"tool": "commitment_ledger", "args": {"query": "交付"}, "observation": "2 条"}
  ],
  "answer": "上周五的承诺已被改成下周三。",
  "citations": [
    {"kind": "turn", "sessionId": "go_20261028_1000", "turnIds": ["turn_42"]}
  ],
  "grounded": true,
  "stopReason": "final",
  "usage": {"promptTokens": 1200, "completionTokens": 180}
}
```

#### KG

```json
{
  "nodes": [
    {"id": "person_xiaolin", "kind": "Person", "label": "小林", "status": "active", "confidence": 0.9}
  ],
  "edges": [
    {"id": "edge_1", "src": "person_xiaolin", "dst": "commitment_deliver", "relation": "assigned_to",
     "validFrom": "2026-10-28", "validTo": null, "status": "asserted", "quote": "小王负责。"}
  ]
}
```

#### HUDState

```json
{
  "recording": true,
  "elapsed": 47,
  "mode": "local",
  "lastTranscript": "周五先交第一版。",
  "activeCommitment": {"title": "周五交付第一版", "owner": "小王", "due": "周五"},
  "evidenceCount": 2,
  "devices": ["MIC", "SCREEN"]
}
```

---

## 6. 状态与边界

每个页面都必须设计以下状态：

| 状态 | 表现 |
|---|---|
| 空 | 说明为什么空 + 下一步 |
| 加载 | 骨架/进度，不用无限转圈 |
| 错误 | 红色 `role=alert` + 重试 |
| 离线 | 顶部 `本地`，云端功能禁用/提示 |
| 无权限 | 明确“需要麦克风/屏幕权限”，给设置入口 |
| 录制中 | REC + 计时 + 命中数；不要遮挡主内容 |
| 零命中 | “本场没有保存片段”，不制造假数据 |
| 长任务 | 阶段、块数、取消、已完成保留 |
| 校验失败 | 指出文件、重试、不要笼统“失败” |

---

## 7. Vercel 构建顺序

### P0（先做）

1. App Shell：侧栏、顶栏、本地/云端、REC 状态。
2. Home / Chat：composer、消息、引用、审批卡。
3. Meeting Console：REC、字幕、屏幕预览、关注事项。
4. Evidence Pack 查看页（mock 数据）。
5. Commitment Ledger（mock 数据）。
6. HUD（8 英寸 mock）。

### P1

7. Project（项目选择、文件树、全库理解）。
8. Library / Session detail。
9. 主聊天里的 Agent artifacts（深度检索、关注、事实、记忆、RAG 卡片）。
10. Find Moment 片段卡。
11. Knowledge Graph（6–10 节点）。
12. Settings 七个 tab。

### P2

13. 文件查看器全类型。
14. 商业/合规细节。
15. 空状态/错误态/响应式打磨。

---

## 8. 验收标准

- 1440×900、1280×800、1920×1080 无横向滚动。
- 所有页面有 mock 数据；不依赖真实后端。
- 每个列表有空状态、加载态、错误态。
- 时间戳可点击；引用可点击；哈希可复制。
- 状态不只靠颜色。
- 不引入 Next/React/Vue/Tailwind/npm/外部 CDN。
- 不读取/打印真实会议数据。
- Evidence Pack 校验页能展示“全部通过/失败文件”。
- Ledger 能展示 `周五 → 下周三`。
- HUD 1.5 米可读。
- 不伪造任何指标。

---

## 9. 不要做

- 不要做视频会议回放优先；
- 不要做全量录制暗示；
- 不要做通用任务管理/日程；
- 不要做系统级数字分身；
- 不要做 100 节点图谱主界面；
- 不要做浅色主题；
- 不要做大 Hero/3D/霓虹/玻璃拟态；
- 不要做“法律级取证”承诺；
- 不要编造 CER/WER/P/R/准确率；
- 不要把 `pywebview.api` 直接散落在新 UI 里，统一走 adapter。

---

## 10. 一句话

**UI 的每个页面都要回答一个问题：这段会议里，谁在什么时候说了什么，我凭什么相信它？**