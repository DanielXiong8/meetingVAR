# 小红书 Skill 创新赛 · 参赛方案（meetingVAR 衍生）

> 状态：学习 Skill 已升级为“实时录课 + 课后定位”双模式，法律取证 Skill 为课后模式；均自测通过。
> 下一步：在 CodeArts CLI 里跑实时冒烟，确认设备与后台进程权限。
> 最后更新：2026-11-17

## 1. 比赛事实

| 项 | 内容 |
|---|---|
| 名称 | 2026 华为开发者大赛 · 小红书 Skill 创新赛 |
| 主办 | 华为 + 小红书 |
| 截止 | **2026-11-24 15:59:59** |
| 规模 | 2,356 支团队 / 31,428 人报名 |
| 奖金 | 总池 ¥500,000；金奖 ¥100k、银奖 2×¥50k、铜奖 3×¥20k、最佳 Skill 创新奖 10×¥10k、单项奖 5×¥5k、优秀奖 115×¥1k |
| 赛道 | 产业赛道（电商/制造/农业/教育/文旅/医疗/本地生活）+ 通用赛道（效率工具/内容创作/学习成长/生活方式/开发工具） |
| 评审五维 | 场景真实性、作品可用性、Skill 复用率、技术完成度、内容传播力 |
| 晋级 | 11/25–27 初筛 Top200 → 11/30–12/3 联合评审 Top32 → 12/4 公布 → 12/11–13 线下答辩 |

### 硬门槛

1. 必须至少使用 **华为云码道 CodeArts** 或 **果办 OfficeAce** 之一；
2. Skill 必须同时上架 **华为云 Skill 市场** 和 **小红书 Skill 市场**；
3. 发小红书笔记，带 `#小红书Skill创新赛 #2026华为开发者大赛`（不少于 1 条）；
4. 单人/2–5 人组队，**每个队员都要单独报名**，每人只能进一支队；
5. 单次提交 ≤40MB、≤30 个文件；可多次提交，以截止前最新版为准；
6. 同一作品不能在多赛道重复获奖。

### 链接

- 比赛主页：https://developer.huaweicloud.com/competition/information/1300000350/introduction
- 码道 CodeArts（网页版）：https://devcloud.cn-north-4.huaweicloud.com/chat
- 码道 IDE 下载：https://codearts.huaweicloud.com/download.html
- 果办 OfficeAce：https://www.huaweicloud.com/product/officeace/download.html
- 华为云 Skill 市场：https://skills.huaweicloud.com/community
- 小红书 Skill Hub：https://creator.xiaohongshu.com/new/skill-hub

## 2. 参赛策略

**主攻：学习 Skill。第二发：法律取证 Skill。**

两个 Skill 共用同一个定位内核：转写解析 → 问题匹配 → 自适应完整区间（内部再挑核心 30–90 秒）→
原话/前后文。学习 Skill 输出可回放材料；法律取证 Skill 在此基础上增加 manifest + SHA256 证据包。
区别只在包装、话术、示例和合规边界。

| | 学习 Skill | 法律取证 Skill |
|---|---|---|
| 目录 | `lecture-moment-finder/` | `recording-evidence-finder/` |
| 一句话 | 3 小时网课，直接跳到老师讲重点的那几分钟 | 2 小时录音，30 秒找到对方承认违约的那 40 秒 |
| 赛道 | 通用 → 学习成长（赛题一 AI校园达人） | 通用 → 效率工具（赛题四 专业领域） |
| 状态 | **已完成，可提交** | **已完成，可提交** |
| 传播力 | 强 | 中，需注意法律内容合规 |

**为什么不是产业赛道**：产业赛道的七个方向里没有法律服务；学习方向则正好对应
赛题一的“上传课堂笔记、课件或录音内容”。

### 窗口机制（2026-11-17 调整）

不再锁死 30–90 秒：

- **完整区间**：从命中句出发，按相邻句相关度继续扩展，遇到长静音/课间休息/话题切换才停；
  上限按录音长度自动（默认 30%，最多 10 分钟）。
- **核心片段**：在完整区间内部挑最密的 30–90 秒，用于快速回放。
- 输出同时给 `start/end`（完整）和 `core_start/core_end`（核心），
  有媒体/ffmpeg 时同时生成 `clip_full.mp4` 与 `clip_core.mp4`。

这样既回答“老师从哪讲到哪”，又保留“最关键的几十秒”。

## 3. 学习 Skill 已交付内容

目录：`competition/xhs-skill-2026/lecture-moment-finder/`

```text
SKILL.md                          # 436 行，实时模式 + 课后模式
scripts/
  moment_core.py                  # 纯标准库：解析、检索、自适应窗口、结果/报告
  find_moment.py                  # 课后定位 CLI
  live_core.py                    # 实时采集/ASR/匹配/完整区间跟踪/屏幕录像
  live_engine.py                  # 前台引擎：录音/录屏/转写/触发/跟踪/保存
  live_ctl.py                     # devices/doctor/start/status/watch/stop/simulate
  transcribe.py                   # 可选：faster-whisper 本地转写
  selftest.py                     # 离线 + 实时完整区间自测
  package.py                      # 打包 + 30 文件/40MB/500 行限制检查
examples/
  lecture-transcript.json         # 45 分钟合成课堂
  interests.example.json          # 课前关心清单
  live-smoke.json                 # 只测采集链路（ASR off）
  query.txt                       # 4 个示例问题
references/
  live-mode.md                    # 实时模式配置/输出/调参/故障排查
  output-format.md
  asr-setup.md
  acceptance-criteria.md
  privacy-and-consent.md
```

### 已验证

| 检查 | 命令 | 结果 |
|---|---|---|
| 自测 | `python scripts/selftest.py` | `SELFTEST PASS`；课后定位 + 实时完整区间断言均通过 |
| 多查询 | `find_moment.py` × 4 | 动态规划、考试范围、最长公共子序列、快慢指针均命中 |
| 输出边界 | 检查学习 Skill 输出目录 | 不含 manifest/SHA256SUMS/zip |
| 格式解析 | SRT / VTT / TXT | 三种均可解析并定位 |
| 实时模拟 | `live_ctl.py simulate ... --speed 0` | 45 分钟课堂 → 6 次命中；DP 完整区间 `40:00–44:48` |
| 采集冒烟 | `live_engine.py --duration 6`（ASR off） | 环回设备打开成功，status/session 正常 |
| 屏幕采集 | 5 秒 `screen=true` | `screen.available=true`，无错误 |
| 后台启动 | `live_ctl.py start/status/stop` | 分离进程存活，状态持续更新，优雅停止 |
| 合成语音实时 | SAPI 播放 + 环回采集 + base ASR | 完整区间 `00:12–00:52`，`audio.wav` 40.07s，无 SHA256 包 |
| 合成语音 + 录屏 | SAPI + 环回 + `screen=true` | `screen.mp4` 24.8s / 124 帧 / 1280×800，`audio.wav` 24.78s |
| 打包 | `python scripts/package.py .` | 19 文件 / 179.6KB / SKILL.md 436 行，通过 |

提交包：`competition/xhs-skill-2026/submission/lecture-moment-finder.zip`（63.0KB）。

### 演示话术

> 上课前只写一句：“我想找老师讲动态规划状态转移方程的地方。”
> 启动后它自己录音/录屏、实时转写；老师一讲到这个知识点就开始跟踪，
> 一直录到老师讲完，保存整段音频、屏幕录像和原话（比如 01:30–06:30）。
> 下课后还能对完整转写再做一次“那几分钟”的定位。全程本地，不联网，不上传录音。

### 实时模式（meetingVAR Skill 版）

```text
课前 interests.json
  → live_ctl start --consent
  → 录音/录屏 + faster-whisper 实时转写
  → 关键词/短语阈值命中
  → 继续跟踪到主题结束（相关度下降/停顿/下课/上限）
  → 保存完整区间音频 + 屏幕录像 + 原话
  → status.json / hits.jsonl / hits/hit_NNN/
  → 课后 find_moment.py 二次定位
```

与 meetingVAR 的对齐情况：

| meetingVAR | Skill 实时模式 |
|---|---|
| 系统声音 + 麦克风 | ✅ `loopback` / `mic` / `both` |
| 屏幕采集 | ✅ `capture.screen` |
| 实时 ASR | ✅ faster-whisper 分块 + 重叠拼接 |
| 课前关注清单 | ✅ `interests.json` |
| 关键词/兴趣触发 | ✅ 关键词 + 短语加权 + 阈值 + 冷却 |
| 完整知识点区间 | ✅ 命中后跟踪 + 自适应区间（不是固定 90 秒） |
| 屏幕录屏 | ✅ `screen.mp4`，覆盖完整区间（PyAV，无需 ffmpeg） |
| 可回放材料 | ✅ audio.wav / screen.mp4 / transcript / hit.json |
| 证据包 + SHA256 | — 不在学习 Skill；见法律取证 Skill |
| 课后 Find Moment | ✅ |
| 同意/本地优先 | ✅ `--consent` / local-only |
| 语义/LLM 判断 | ⬜ P1 |
| 说话人分离 | ⬜ P1 |
| HD 补录 | ⬜ P1 |
| HUD 副屏 | ⬜ P1（先轮询 status.json） |

详见 [lecture-moment-finder/references/live-mode.md](C:/Users/Lenovo/projects/meetingVAR/competition/xhs-skill-2026/lecture-moment-finder/references/live-mode.md)。

## 4. 法律取证 Skill 已交付内容

目录：`competition/xhs-skill-2026/recording-evidence-finder/`

```text
SKILL.md                          # 227 行，含合规边界、工作流、命令、参数确认
scripts/
  evidence_core.py                # 共享内核：解析、检索、时间窗、证据包、校验
  find_evidence.py                # CLI：--claim 定位 + 证据包
  verify_case.py                  # 校验证据包
  transcribe.py                   # 可选：本地转写
  selftest.py                     # 离线自测
  package.py                      # 打包 + 限制检查
examples/
  dispute-transcript.json         # 8 分钟合成供货延迟通话，含承认/改口/退款承诺
  claims.txt                      # 3 条示例主张
references/
  output-format.md
  asr-setup.md
  compliance.md
  acceptance-criteria.md
```

### 已验证

| 检查 | 命令 | 结果 |
|---|---|---|
| 自测 | `python scripts/selftest.py` | `SELFTEST PASS`；承认语境 `03:34–06:16`（核心 `04:12–04:46`），退款语境 `06:16–08:00`（核心 `07:30–08:00`） |
| 承认延迟交付 | `find_evidence.py --claim "他什么时候承认这批货是延迟交付的"` | 完整语境 03:34–06:16，核心 04:12–04:46，原话“我承认这批货是我们延迟交付的” |
| 证据包 | `verify_case.py out.zip` | `PASS：所有文件哈希一致。` |
| 打包 | `python scripts/package.py .` | 13 文件 / 69.1KB / SKILL.md 242 行，通过 |

提交包：`competition/xhs-skill-2026/submission/recording-evidence-finder.zip`（29.2KB）。

### 与学习 Skill 的差异

| 维度 | 学习 Skill | 法律取证 Skill |
|---|---|---|
| 输入 | 课堂转写 | 录音/录屏转写（可带说话人） |
| 查询 | 知识点 | 事实主张，如“他什么时候承认延迟交付” |
| 输出重点 | 时间戳 + 原话 | 时间段 + 原话 + 说话人 + 原始文件 SHA256 |
| 额外能力 | 关键帧 | 证据链清单、校验报告、说话人时间线 |
| 合规措辞 | 隐私与同意 | **明确不构成法律意见、不宣称司法采信** |
| 示例 | 合成课堂 | 合成商务通话（延迟交付/退款承诺） |

### 复用方式

- 复制 `moment_core.py`，保留检索/时间窗/证据包；
- `find_moment.py` 改名为 `find_evidence.py`，参数 `--query` 改 `--claim`；
- 新增 `verify_case.py`：校验原始媒体、转写、证据包三者哈希；
- `SKILL.md` 重写场景、话术、合规声明；
- 示例换成 10–15 轮合成通话，含一个明确“承认”和一个“改口”。

### 合规红线

1. 只处理用户合法持有的录音；
2. 不提供法律意见，不判断证据效力；
3. 不宣称“司法采信”“法律级证据”；
4. 默认本地处理、不上传；
5. 公开演示只用合成素材，不用真实纠纷录音。

## 4.5 运行时探测（Go/No-Go）

目录：`competition/xhs-skill-2026/runtime-probe/`
提交包：`competition/xhs-skill-2026/submission/runtime-probe.zip`

在 CodeArts / OfficeAce 里运行：

```bash
python scripts/probe.py
```

输出 Python 版本、ffmpeg/ffprobe、faster-whisper、PyAV、临时目录写入、子进程、SHA256 能力。
把 JSON 存到 `competition/xhs-skill-2026/evidence/runtime-probe.json`。

本地基线（2026-11-17 已测）：

| 项 | 结果 |
|---|---|
| Python | 3.10.21 |
| faster-whisper | 1.2.1，可用 |
| PyAV | 17.1.0，可用 |
| ffmpeg CLI | **未安装**（只影响 clip，不影响核心定位） |
| 两个 Skill 自测 | 均 `SELFTEST PASS` |

完整操作步骤见 [VERIFY_TONIGHT.md](VERIFY_TONIGHT.md)。

## 5. 8 天排期

| 日期 | 动作 | 产出/门禁 |
|---|---|---|
| 11/16 今晚 | 全员报名 + 组队；CodeArts 网页版建 hello skill；验证 Python/ffmpeg | **Go/No-Go：跑不了 Python 就退纯转写版** |
| 11/17 | 跑 `runtime-probe`；在 CodeArts 里跑 `live_ctl.py doctor` + 10 秒采集冒烟 | **Go/No-Go 结论**；设备/后台进程可用 |
| 11/18 | 用真实/合成语音跑实时命中；打磨演示脚本、3 分钟视频脚本 | 真实素材命中一次；视频脚本定稿 |
| 11/19 | 法律 Skill 复制内核 + 重写 SKILL.md + 合成通话示例 | 已完成（11/16 提前） |
| 11/20 | 两个包跑 `package.py`；上架华为云 Skill 市场 | 市场可见 |
| 11/21 | 上架小红书 Skill Hub；发笔记；录 3 分钟视频 | 双市场 + 笔记链接 |
| 11/22 | 按评委视角彩排 3 次；修问题 | 连续 3 次干净跑通 |
| 11/23 | 正式提交；留缓冲 | 提交成功截图 |
| 11/24 | 只监控，12:00 前完成最终提交 | 不赌 15:59 |

> 两个 Skill 的代码与示例已在 11/16 提前完成。后续时间全部留给：报名/组队、
> CodeArts 运行时验证、双市场上架、小红书笔记、3 分钟演示视频、彩排。

## 6. 提交清单

- [ ] 每个队员都已单独报名
- [ ] 团队已创建，队长确认
- [ ] Skill 使用 CodeArts 或 OfficeAce 创建/提交
- [ ] 华为云 Skill 市场可见
- [ ] 小红书 Skill 市场可见
- [ ] 小红书笔记已发，带两个标签
- [ ] zip ≤40MB、≤30 文件
- [ ] `SKILL.md` ≤500 行
- [ ] 自测/实时完整区间模拟通过
- [ ] 3 分钟演示视频
- [ ] 现场答辩可在 2 分钟内跑通

## 7. 风险与未验证项

| 风险 | 影响 | 应对 |
|---|---|---|
| CodeArts 运行时不能跑 Python/ffmpeg | 核心演示受限 | 今晚验证；退化为纯转写版，仍可定位 |
| 没有现成课堂转写 | 演示不真实 | 用合成示例 + 本地 ASR 现场生成 |
| 法律内容在小红书受限 | 笔记/传播受影响 | 措辞用“录音整理/辅助定位”，不用“取证神器” |
| 两个 Skill 被视为同一作品拆赛道 | 评审风险 | SKILL.md、示例、价值主张明确区分；必要时只提交学习 Skill |
| SDG 赛程冲突 | 人力不足 | 法律 Skill 可裁剪；优先保学习 Skill |
| 跨赛事排他条款 | 资格风险 | 查 SDG 规则；未确认前不公开宣称“双赛作品” |

## 8. 常用命令

```bash
cd competition/xhs-skill-2026/lecture-moment-finder

# 自测
python scripts/selftest.py

# 定位 + 回放材料
python scripts/find_moment.py \
  --transcript examples/lecture-transcript.json \
  --query "老师讲动态规划状态转移方程的那几分钟" \
  --out out/dp-moment

# 实时完整区间模拟（不打开设备）
python scripts/live_ctl.py simulate \
  --config examples/interests.example.json \
  --transcript examples/lecture-transcript.json \
  --out out/simulated-lecture --speed 0

# 打包提交
python scripts/package.py . --out ../submission/lecture-moment-finder.zip
```