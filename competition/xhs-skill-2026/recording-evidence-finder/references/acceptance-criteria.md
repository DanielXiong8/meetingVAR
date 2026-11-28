# 提交规范与自检清单

## 1. 结构（硬性）

| # | 要求 | 自检 |
|---|---|---|
| 1 | `SKILL.md` 存在，frontmatter 有 `name` / `description` | [ ] |
| 2 | `name` 与目录名一致（`recording-evidence-finder`） | [ ] |
| 3 | frontmatter 不写 `version` | [ ] |
| 4 | `description` 含触发词 | [ ] |
| 5 | 有 `## Overview`、`## Prerequisites`、`## Workflow`、`## Core Commands`、`## Parameter Confirmation`、`## Reference Documents` | [ ] |
| 6 | `SKILL.md` ≤ 500 行 | [ ] |
| 7 | 总文件数 ≤ 30 | [ ] |
| 8 | 总大小 ≤ 40MB | [ ] |
| 9 | 扩展名在允许列表内（无音频/视频） | [ ] |
| 10 | `references/` 文件名 kebab-case | [ ] |

## 2. 安全（硬性）

| # | 要求 | 自检 |
|---|---|---|
| 11 | 无硬编码 AK/SK、token、密码 | [ ] |
| 12 | 不拼接 shell 命令、不调用系统命令解释器、不做破坏性操作 | [ ] |
| 13 | 不读取环境变量里的密钥 | [ ] |
| 14 | 不联网、不上传录音 | [ ] |
| 15 | 子进程只用参数列表调用 | [ ] |

## 3. 合规（硬性）

| # | 要求 | 自检 |
|---|---|---|
| 16 | 明确“不构成法律意见” | [ ] |
| 17 | 不出现“司法采信/法律级证据/呈堂证供” | [ ] |
| 18 | 要求用户确认录音来源合法 | [ ] |
| 19 | 示例为合成素材，开头有声明 | [ ] |
| 20 | 不包含真实姓名、电话、公司名 | [ ] |
| 21 | 输出仅写 `--out` 指定目录 | [ ] |

## 4. 功能（演示前）

| # | 检查 | 命令 |
|---|---|---|
| 22 | 自测通过 | `python scripts/selftest.py` |
| 23 | 承认延迟交付可定位 | `find_evidence.py --claim "他什么时候承认这批货是延迟交付的"` |
| 24 | 承诺退款可定位 | `find_evidence.py --claim "谁承诺退款"` |
| 25 | 证据包可校验 | `verify_case.py out/xxx.zip` |
| 26 | 无媒体时也能出结果 | 不加 `--media` |
| 27 | 有媒体时能出 clip | 加 `--media` + 本机 ffmpeg |
| 28 | 无命中时给出可操作建议 | 用一个不存在的主张试一次 |

## 5. 比赛提交（硬性）

| # | 要求 | 自检 |
|---|---|---|
| 29 | 使用华为云码道 CodeArts 或果办 OfficeAce 创建/提交 | [ ] |
| 30 | 已发布到华为云 Skill 市场 | [ ] |
| 31 | 已发布到小红书 Skill 市场 | [ ] |
| 32 | 小红书笔记带 `#小红书Skill创新赛 #2026华为开发者大赛` | [ ] |
| 33 | 团队每个成员都单独报名 | [ ] |
| 34 | 提交文件 ≤40MB、≤30 个 | [ ] |
| 35 | 3 分钟演示视频已录制 | [ ] |
| 36 | 现场/线上答辩演示可在 2 分钟内跑通 | [ ] |

## 6. 五维评审自查

| 维度 | 我们的证据 |
|---|---|
| 场景真实性 | 商务纠纷、劳动仲裁、客户投诉三类真实场景；示例是一通 8 分钟供货延迟沟通 |
| 作品可用性 | 一条命令出结果；无网络也能跑；输出可人工复核 |
| Skill 复用率 | 任何带转写的录音都能用；不绑定行业/平台 |
| 技术完成度 | 时间窗合并、说话人保留、SHA256 证据包、自测脚本 |
| 内容传播力 | “2 小时录音 30 秒找到他承认的那 40 秒”适合前后对比演示 |

## 7. 提交前最后一分钟

```bash
python scripts/selftest.py
python scripts/find_evidence.py --transcript examples/dispute-transcript.json \
  --claim "他什么时候承认这批货是延迟交付的" --case-id DEMO --out /tmp/final-check
python scripts/verify_case.py /tmp/final-check.zip
```

三条都通过，再点提交。