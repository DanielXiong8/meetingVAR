# 提交规范与自检清单

本文件把华为云 Skill 规范和本包的自检项放在一起，提交前逐项打勾。

## 1. 结构（硬性）

| # | 要求 | 自检 |
|---|---|---|
| 1 | `SKILL.md` 存在，frontmatter 有 `name` / `description` | [ ] |
| 2 | `name` 与目录名一致（`lecture-moment-finder`） | [ ] |
| 3 | frontmatter 不写 `version` | [ ] |
| 4 | `description` 含触发词（Triggers include） | [ ] |
| 5 | 有 `## Overview`、`## Prerequisites`、`## Workflow`、`## Core Commands`、`## Parameter Confirmation`、`## Reference Documents` | [ ] |
| 6 | `SKILL.md` ≤ 500 行 | [ ] |
| 7 | 总文件数 ≤ 30 | [ ] |
| 8 | 总大小 ≤ 40MB | [ ] |
| 9 | 所有文件扩展名在允许列表内（无音频/视频） | [ ] |
| 10 | `references/` 文件名 kebab-case | [ ] |

## 2. 安全（硬性）

| # | 要求 | 自检 |
|---|---|---|
| 11 | 无硬编码 AK/SK、token、密码 | [ ] |
| 12 | 不拼接 shell 命令、不调用系统命令解释器、不做破坏性操作 | [ ] |
| 13 | 不读取环境变量里的密钥 | [ ] |
| 14 | 不联网、不上传用户文件 | [ ] |
| 15 | 子进程只用参数列表调用（`subprocess.run([...])`） | [ ] |

## 3. 功能（演示前）

| # | 检查 | 命令 |
|---|---|---|
| 16 | 自测通过 | `python scripts/selftest.py` |
| 17 | JSON 转写可定位 | `find_moment.py --transcript examples/lecture-transcript.json ...` |
| 18 | SRT 可定位 | 用任意 SRT 跑一次 |
| 19 | 输出不含 manifest/SHA256/zip | 跑一次，检查输出目录 |
| 20 | 无媒体时也能出结果 | 不加 `--media` |
| 21 | 有媒体时能出 clip | 加 `--media` + 本机 ffmpeg |
| 22 | 无命中时给出可操作建议 | 用一个不存在的问题试一次 |

## 4. 比赛提交（硬性）

| # | 要求 | 自检 |
|---|---|---|
| 23 | 使用华为云码道 CodeArts 或果办 OfficeAce 创建/提交 | [ ] |
| 24 | 已发布到华为云 Skill 市场 | [ ] |
| 25 | 已发布到小红书 Skill 市场 | [ ] |
| 26 | 小红书笔记带 `#小红书Skill创新赛 #2026华为开发者大赛` | [ ] |
| 27 | 团队每个成员都单独报名 | [ ] |
| 28 | 提交文件 ≤40MB、≤30 个 | [ ] |
| 29 | 3 分钟演示视频已录制 | [ ] |
| 30 | 现场/线上答辩演示可在 2 分钟内跑通 | [ ] |

## 4.5 实时模式（演示前）

| # | 检查 | 命令 |
|---|---|---|
| 29 | 依赖齐全 | `live_ctl.py doctor` |
| 30 | 能看到环回/麦克风设备 | `live_ctl.py devices` |
| 31 | 10 秒采集无错误 | `live_ctl.py start ... --duration 10` |
| 32 | 状态持续更新 | `live_ctl.py status --out ...` |
| 33 | 后台进程能优雅停止 | `live_ctl.py stop --out ...` |
| 34 | 合成/真实语音能命中 | `live_ctl.py simulate ...` 或现场说话 |
| 35 | 命中区间覆盖完整知识点 | 看 `hit.json` 的 `span_start` / `span_end` |
| 36 | 命中目录含 audio/screen.mp4/transcript/hit.json | 打开 `hits/hit_001/` |
| 37 | 录屏时长与音频一致（±1 秒） | 用播放器对比 `screen.mp4` 和 `audio.wav` |
| 38 | 未确认许可时拒绝启动 | 去掉 `--consent` 试一次 |

## 5. 五维评审自查

| 维度 | 我们的证据 |
|---|---|
| 场景真实性 | 学生复习、补课、教研三类真实场景；示例就是一节 45 分钟数据结构课 |
| 作品可用性 | 一条命令出结果；无网络也能跑；没有转写也能用本地 ASR |
| Skill 复用率 | 任何课程、任何老师的转写都能用；不绑定学校/平台 |
| 技术完成度 | 完整区间跟踪、自适应时间窗、短语加权、自测脚本 |
| 内容传播力 | “3 小时网课 10 秒定位重点”适合小红书前后对比演示 |

## 6. 提交前最后一分钟

```bash
python scripts/selftest.py
python scripts/find_moment.py --transcript examples/lecture-transcript.json \
  --query "老师讲动态规划状态转移方程的那几分钟" --out /tmp/final-check
python scripts/live_ctl.py simulate --config examples/interests.example.json \
  --transcript examples/lecture-transcript.json --out /tmp/final-live --speed 0
```

三条都通过，再点提交。