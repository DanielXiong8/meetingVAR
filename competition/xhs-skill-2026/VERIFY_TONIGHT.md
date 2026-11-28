# 今晚 30 分钟验证清单（Go / No-Go）

> 目的：在写更多材料之前，确认 CodeArts / OfficeAce 的 Skill 运行时能不能跑我们的 Python 包。
> 这一步没过，后面的上架、笔记、视频都可能白做。
>
> 负责人：你来操作账号；我准备包和检查项。

## 0. 本地基线（已测，2026-11-17）

| 项 | 结果 |
|---|---|
| Python | `3.10.21`（meetingvar 环境） |
| faster-whisper | `1.2.1`，可用 |
| PyAV | `17.1.0`，可用 |
| ffmpeg CLI | **未安装**（不影响核心定位，只影响 clip） |
| 两个 Skill 自测 | 均 `SELFTEST PASS` |

## 1. 报名与组队（5 分钟）

- [ ] 打开 https://developer.huaweicloud.com/competition/information/1300000350/introduction
- [ ] 点“立即报名”
- [ ] 每个队员都单独报名（队长也要报）
- [ ] 创建团队，确认队长
- [ ] 截图保存“报名成功/团队页”

> 规则：每人只能加入一支队伍；团队 2–5 人，单人也可以。

## 2. 打开 CodeArts（5 分钟）

- [ ] 网页版：https://devcloud.cn-north-4.huaweicloud.com/chat
- [ ] 登录后确认能新建会话/工作区
- [ ] 如果网页版不能上传本地 Skill，下载 IDE：https://codearts.huaweicloud.com/download.html

## 3. 跑 runtime-probe（10 分钟）

**材料**：`competition/xhs-skill-2026/submission/runtime-probe.zip`

- [ ] 在 CodeArts 中新建/导入一个 Skill（名字随意，如 `runtime-probe`）
- [ ] 上传 zip，或把 `SKILL.md` 和 `scripts/probe.py` 放进工作区
- [ ] 在会话里说：

```text
运行 python scripts/probe.py，把完整 JSON 输出给我
```

- [ ] 把 JSON 保存到：

```text
competition/xhs-skill-2026/evidence/runtime-probe.json
```

- [ ] 截图保存运行界面

### 判定表

| 结果 | 结论 | 下一步 |
|---|---|---|
| Python 3.10+，write/hash/subprocess 都 OK | **Go** | 继续上架两个 Skill |
| 再加 ffmpeg | 完整版 | 可以演示 clip 和关键帧 |
| Python OK 但无 ffmpeg | 纯转写版 | 演示定位 + 回放材料，不演示视频片段 |
| Python 不可用 | **No-Go** | 两个脚本型 Skill 无法运行；要么换平台能力，要么放弃本次比赛 |
| subprocess 不可用 | 降级 | 不能调 ffmpeg；只用标准库定位，仍可提交 |

## 4. 跑学习 Skill（5 分钟）

**材料**：`competition/xhs-skill-2026/submission/lecture-moment-finder.zip`

- [ ] 上传/导入后运行：

```text
python scripts/selftest.py
```

- [ ] 预期：`SELFTEST PASS`
- [ ] 再运行：

```text
python scripts/find_moment.py --transcript examples/lecture-transcript.json --query "老师讲动态规划状态转移方程的那几分钟" --out out/demo
```

- [ ] 预期：完整讨论 `40:00 – 44:48`，核心 `41:20 – 42:28`，并生成 `out/demo.zip`
- [ ] 截图保存

## 5. 跑法律 Skill（3 分钟）

**材料**：`competition/xhs-skill-2026/submission/recording-evidence-finder.zip`

- [ ] 运行：

```text
python scripts/selftest.py
```

- [ ] 预期：`SELFTEST PASS`；承认语境 03:34–06:16（核心 04:12–04:46），退款语境 06:16–08:00（核心 07:30–08:00）
- [ ] 截图保存

## 6. 记录与决策（2 分钟）

- [ ] 把三个 JSON/截图放到 `competition/xhs-skill-2026/evidence/`
- [ ] 在 `PLAN.md` 的“已验证”表加一行：

```text
| CodeArts 运行时 | python scripts/probe.py | Go / No-Go：<结论> |
```

- [ ] 如果 No-Go：当天决定是换 OfficeAce、换实现方式，还是放弃本次比赛。

## 常见问题

### Q1：CodeArts 网页版找不到“上传 Skill”

先用 IDE 版；或者在网页版会话里直接说：

```text
帮我创建一个 Skill，名字叫 runtime-probe，包含 SKILL.md 和 scripts/probe.py
```

然后把两个文件内容贴进去。

### Q2：运行时提示 `python: command not found`

说明它不是脚本型运行时。先试 `python3`；都不行就是 No-Go。

### Q3：`ffmpeg.found=false`

不影响核心功能。演示时只讲“定位 + 回放材料”，不要讲“自动剪视频”。

### Q4：`faster_whisper.ok=false`

不要现场演示“上传录音直接转写”。改用已有 SRT/VTT 转写，或者提前在本地生成好转写再上传。

### Q5：市场要求“必须使用 CodeArts 或 OfficeAce”

用 CodeArts 创建/打包/提交即可。我们的 SKILL.md 和脚本就是在这个会话里生成和验证的，满足“使用”要求。**这条解释未向官方确认**，如果评委质疑，保留创建过程的截图。

## 需要我做的

你跑完第 3 步后，把 JSON 发我（或放到 `evidence/runtime-probe.json`），我根据结果：

- 决定是否给两个 Skill 加“无 ffmpeg 降级”提示；
- 调整演示脚本（讲不讲 clip）；
- 更新 `PLAN.md` 的 Go/No-Go 结论。