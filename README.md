# meetingVAR

> record your meeting like VAR referee —— 不是记下一切，是保住对你重要的每一刻。

> **定位（2026-10-08 修订，采纳外部评审）**：面向复杂工作会议的**个人证据层**——
> 理解你的项目上下文，在相关内容出现时保存可回放的原始证据，把每条重要结论关联到具体时间点。
> 不主张所有会议都该选择性记录；主张高密度技术决策场景值得更好的证据密度。

装在你自己电脑上的「会议取证器」：平时只留一条**不落盘**的草稿流（滚动音频/录屏缓冲 + 增量草稿转写），一旦话题命中你的兴趣（来自项目记忆），自动把前后片段连同屏幕画面**转正**保存，会后产出**有厚有薄**的纪要——薄的是全场概要，厚的是与你有关的完整取证（按说话人标注、带时间戳、可溯源回听）。

参赛：SDG Open Hack 2026 · Hack16 ·「AI 办公任务助手」赛道。

## 定位三声明（设计红线）

1. **系统级、平台无关**：依附"项目"，不依附任何会议软件——腾讯会议、飞书、微信视频、线下会议室都只是声音来源。
2. **选择性取证，而非全量记录**：默认零落盘，只存命中片段——比全量纪要更隐私。
3. **端侧优先**：音频、画面、判断全在本机完成，数据不出电脑，离线可用。

## 架构

```
[任意会议平台的声音] ──► Windows 系统音频环回（WASAPI loopback）
        │
        ▼
   ┌────────────── 草稿层（常驻、不落盘）──────────────┐
   │ 滚动环形缓冲 ≈10min 音频 + 低帧率(1–2fps)录屏缓冲    │
   │ 增量草稿转写（FunASR/SenseVoice，热词增强）          │
   │ + 说话人分离（CAM++，本地）                        │
   └───────────────┬─────────────────────────────────┘
                    ▼
  L1 热词规则 ──► L2 系统一打分 ──► 命中 ──► 转正：音频段+录屏段+关键帧
                   ▲                                   │
  L3 手动 VAR 热键 ┘                                   ▼
                                            [待审片段池 pool/]
                                                  │
                                                  ▼
            会后：系统二 LLM 深读（带项目记忆 RAG）──► 厚纪要 / 薄纪要 / 行动项
                                                  │
            评价反馈 ──► 词表权重与阈值更新 ──► 回写项目记忆
```

## 目录

```
docs/            计划书（plan.md）与片段池接口契约（pool_schema.md）
spikes/          五个开工 spike：c1 离线ASR / c2 音频环回 / c3 平台实测 / c4 录屏 / c5 热词
src/meetingvar/
  capture/       音频环回 + 录屏
  transcribe/    ASR + 说话人分离 + 热词
  monitor/       L1 词表 / L2 打分 / L3 热键
  pool/          待审片段池（schema 实现）
  report/        厚薄纪要 + 项目记忆 RAG
models/          模型权重本地固化（不入库）
data/            会议音频/录屏素材（不入库——含他人声音与屏幕）
```

## 当前状态：MVP 命令行骨架（截至 2026-10-07）

| 模块 | 验证目标 | 状态 |
|---|---|---|
| c1 | FunASR + VAD + CAM++ 本地推理 | CPU/GPU 冒烟通过；正式拔网线验证和多人剧本归因仍待补 |
| c2 | WASAPI 系统声音环回 | TTS 系统输出采集并成功送入 ASR，已验证 |
| c3 | 腾讯会议/微信视频真实会议 | 待人工实测；不能由 TTS 环回测试替代 |
| c4 | dxcam 低帧率录屏环形缓冲 | Windows 上 2fps JPEG 缓冲已验证 |
| c5 | 术语增强 | FunASR `hotword` 参数实测无效；现用转写后术语纠错，效果见 SETUP |

当前可运行入口是 CLI，不包含托盘/悬浮条/会后图形界面。真实音频、屏幕、模型权重均不入库；无声卡、模型或真实会议素材的功能只按其实际测试边界标注，不视为端到端验收。

## 快速开始（MVP 骨架，2026-10-04 起可用）

```bash
# 环境（一次性）：先按仓库锁定的 Python 3.10 创建环境，环境文件会安装 requirements.txt
conda env create -f environment.yml
conda activate meetingvar
pip install -e .
meetingvar devices                                            # 看环回端点
meetingvar replay --wav 某段.wav --terms configs/terms_demo.txt \
                 --memory spikes/b1_rag/sample_project        # 离线回放走全管线
meetingvar record --terms configs/terms_demo.txt              # 实时采集（回车=手动VAR）
meetingvar digest --session <id> [--llm]                      # 会后薄/厚纪要
meetingvar review --session <id> --summary                   # 会后片段列表与反馈摘要
meetingvar review --session <id> --clip <id> --status useful --project <项目目录>  # 评价并校准该项目 L2
python scripts/demo_feedback_loop.py                         # 无模型的两场反馈演示

# 不依赖模型和设备的本地回归测试
python -m unittest discover -s tests -v
```

管线：环回音频 → 10min 环形缓冲（不落盘）→ 8s 增量转写（3s 重叠，说话人+标点）→ 术语纠错 →
L1 热词 / L2 兴趣相似度 / L3 手动热键 → 命中转正（默认回溯 300s，可由 `MEETINGVAR_LOOKBACK` 调整）→ 片段池 →
digest 出 `data/reports/<session>/{minutes.md,var_reports.md}`。

LLM 接入（可选）：设 `MEETINGVAR_LLM_BASE_URL / _API_KEY / _MODEL`，`digest --llm` 出叙述版；
未配置时输出结构化模板版并如实标注〔模板版〕。

L2 反馈校准（显式 opt-in）：会后评审时加 `--project <项目目录>`，系统只采纳已评价的 L2 片段，
把有用样本小幅降低、不相关样本提高该项目的相似度阈值，并将样本与当前阈值保存到
`<项目目录>/meetingvar/calibration.json`。下一次 `go <项目目录>` 或 `record/replay --memory <项目目录>`
会读取它。阈值范围限制在 0.05–0.95；L1 热词与 L3 手动 VAR 不参与 L2 校准。此规则是可审计的
保守启发式，不代表模型训练或统计意义上的准确率保证。

## 文档

- [docs/plan.md](docs/plan.md) —— 完整计划书：定位 / 功能规格 / 竞品差异 / 赛程里程碑 / 两人分工
- [docs/daily_plan.md](docs/daily_plan.md) —— 逐日计划（你每天干什么看这个）
- [docs/pool_schema.md](docs/pool_schema.md) —— 待审片段池接口契约，**字段变更须两人确认**
- [docs/github_guide.md](docs/github_guide.md) —— GitHub 速成
- [docs/demo_script.md](docs/demo_script.md) —— 演示录制台词本
- [SETUP.md](SETUP.md) —— 环境踩坑记录（谁踩谁写）

## F1 · 词表自动化（agent 自己读记忆）

```bash
meetingvar terms --memory <项目文件夹> [--extra 手动词表.txt]   # 生成/更新词表
meetingvar record --memory <项目文件夹>                        # 不给 --terms 时自动抽取
```

监听词表三路自动抽取：markdown 结构化标记（标题/加粗/`词条：定义`）+ 人名（jieba 词性标注 + PI/师兄等称谓模式）+ 拉丁术语（频次≥2）。**用户手动热词是覆盖项，不是必选项**——给 `--memory` 即零输入开工。
