# V0 UI Review — d75e488

> 审查对象：`v0/optimize-agent-page` / PR #36 / commit `d75e488`  
> 基线：`ui-design` @ `00605df`  
> 审查时间：2026-11-02  
> 预览：`http://127.0.0.1:8787/`（本地静态渲染）

## 1. 这次新增了什么

commit message：**新增证据包与承诺账本工作台页面**

改动：

```text
desktop/web/app.js      +13 / -9
desktop/web/index.html  +43 / -8
desktop/web/style.css   +180 / -0
```

新增：

- 侧栏：`证据包`、`承诺账本` 两个导航项；
- `PAGE_TITLES` 增加 `evidence` / `ledger`；
- `page-evidence`：证据包查看页；
- `page-ledger`：承诺账本页；
- 对应 CSS。

方向正确：这终于把 **Evidence Pack** 和 **Commitment Ledger** 从“聊天里一段文本”变成了独立工作台页面。

## 2. 做得好

### Evidence Pack 页面

- 顶部有 `EVIDENCE PACK` eyebrow + 大标题；
- 当前片段：`交付时间变更`、session、时间窗；
- 来源 chips：`MIC` / `SCREEN` / `本地`；
- 左：转写原文，当前句高亮、说话人、时间戳；
- 右：屏幕帧 3×2 网格；
- 底部：文件 + SHA256 + “通过”状态；
- 视觉层级清楚，符合“会议案卷”方向。

### 承诺账本页面

- 表格列：承诺/行动、负责人、截止、状态、首次出现、最后更新；
- 行内 diff：`小王 → 小李`、`周五 → 下周三`；
- 状态：`已变更` 暖色、`生效中` 绿色；
- 下方“原句与来源”面板，带 session + 时间戳；
- 比聊天里的纯文本强很多。

### 页面架构

`switchPage()` 现在区分 `page` 和 `page panel`，证据包/账本是完整页面，不再盖在首页上。这个修复是对的。

## 3. 合并前必须修的 blocker

### 3.1 中文乱码（P0）

`d75e488` 仍然有 **9 个字符串**被 UTF-8 截断成 `U+FFFD`。

`desktop/web/app.js` 6 处：

```text
190:  "��中 " + state.hits
332:  "仅系统声���"
1119: "记忆操作失败���"
1280: "引用未通过��验"
1520: "GraphRAG 失���："
1818: "续跑不重���花算力"
```

`desktop/web/index.html` 3 处：

```text
205: "分层��完整个文件夹"
271: "把两次原���找出来"
507: "这里会出��真实统计"
```

正确文本：

```text
"命中 "
"仅系统声音"
"记忆操作失败："
"引用未通过校验"
"GraphRAG 失败："
"续跑不重复花算力"
"分层读完整个文件夹"
"把两次原话找出来"
"这里会出现真实统计"
```

`node --check` 能过，说明是编码损坏，不是语法错误。**必须修完再合并。**

### 3.2 页面是纯静态假数据（P0）

`page-evidence` / `page-ledger` 目前是硬编码 HTML：

- 没有 `id` 绑定；
- 没有 onclick；
- 没有 load 函数；
- 没有空状态/加载/错误；
- 没有项目时也会显示“交付时间变更”“3 条生效中”。

这意味着：

- 静态预览很好看；
- 但接进真实 app 会**对任何项目都显示同一份假证据**；
- 无法从聊天动作跳转过来；
- 无法接 `EvidencePackSkill` / `CommitmentLedgerSkill` 的返回。

必须先定义占位 ID + 空状态，再谈接线。

### 3.3 关键按钮全部没有 handler

证据包页：

- `导出 ZIP`
- `打开原图`
- `复制全部哈希`
- `全部通过` 状态（应可点击查看校验详情）

承诺账本：

- `筛选`
- `搜索`
- `跳到会议片段`

这些按钮目前都没有 `id` 或 `onclick`，Vercel 需要补。

### 3.4 视觉细节

- **音频播放器是浏览器默认白色**，在深色界面里非常突兀；需要自定义播放器或至少深色样式。
- **屏幕帧是 CSS 渐变占位块**，看起来像图片加载失败；应换成真实缩略图，或明确的“无帧”占位图标 + 时间。
- `证据包` 页右侧 `×` 是浏览器覆盖层，不是页面元素；实际 app 中不存在。
- 1280×800 下整体可用；账本表格略挤，时间列 10px 偏小。

## 4. 接线所需的占位 ID

建议 Vercel 先加这些 ID（不需要真实数据）：

### Evidence

```text
evidencePackTitle
evidencePackSession
evidencePackWindow
evidencePackSources
evidencePackVerifyBadge
evidenceTranscript
evidenceAudio
evidenceFrames
evidenceFileRows
btnEvidenceExport
btnEvidenceVerify
btnEvidenceOpenDir
btnCopyHashes
evidenceEmpty
evidenceLoading
evidenceError
```

### Ledger

```text
ledgerRows
ledgerSearch
ledgerStatusFilter
ledgerDetail
ledgerQuoteList
btnLedgerJump
ledgerEmpty
ledgerLoading
ledgerError
```

### 行级

```text
<tr data-ledger-id="...">
<button data-evidence-file="audio.wav">
```

## 5. 建议的交互

### Evidence

- 无 pack：显示空状态“还没有证据包。先在聊天里说‘生成证据包’。”
- 点击文件：展开该文件哈希/大小/状态。
- 点击“校验”：逐文件从 `pending` → `checking` → `passed/failed`。
- 失败：红色行 + 失败原因 + 重试。
- 点击“导出 ZIP”：调用 `evidence.export(packId)`。
- 点击“打开原图”：打开当前帧大图/目录。

### Ledger

- 点击行：右侧原句面板切换到该行。
- 搜索：过滤 title/owner/quote。
- 状态筛选：生效中/已变更/已过期/冲突。
- 点击“跳到会议片段”：打开该 clip 的播放/定位。
- 空状态：“还没有可追踪的承诺。开一场会，或在聊天里说‘记住：周五交付第一版’。”

## 6. 下一步

1. Vercel 先修 9 处乱码。
2. 给两个页面加占位 ID + 空/加载/错误状态。
3. 用 mock 数据切三套状态：有数据 / 空 / 校验失败。
4. 深色音频播放器 + 真实帧缩略图/明确占位。
5. 再把聊天里的 `save_evidence_pack` 和 `commitment_ledger` 结果接到这两个页面。
6. 最后再做 Find Moment 页面和 HUD。

## 7. 结论

**方向对了，页面也开始像产品了。但现在还不能合：乱码 + 静态假数据 + 无 ID/无 handler，是三个硬 blocker。**

先让 Vercel 把这三件事修掉，再谈接接口。