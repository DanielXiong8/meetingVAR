# meetingVAR

> record your meeting like VAR referee —— 不是记下一切，是保住对你重要的每一刻。

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

## 当前状态：Spike 阶段（2026-09-29 开工）

| spike | 验证目标 | 状态 |
|---|---|---|
| c1 | torch+FunASR+CAM++ 离线推理（**断网跑通**，权重固化 models/） | 待验证 |
| c2 | pyaudiowpatch 采到"系统输出"声音 | 待验证 |
| c3 | 腾讯会议 + 微信视频实测 c2 | 待验证 |
| c4 | dxcam 低帧率内存环形缓冲（退路 mss） | 待验证 |
| c5 | 热词增强术语纠错可见 | 待验证 |

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

# 不依赖模型和设备的本地回归测试
python -m unittest discover -s tests -v
```

管线：环回音频 → 10min 环形缓冲（不落盘）→ 30s 增量转写（说话人+标点）→ 术语纠错 →
L1 热词 / L2 兴趣相似度 / L3 手动热键 → 命中转正（回溯 120s 音频+截屏）→ 片段池 →
digest 出 `data/reports/<session>/{minutes.md,var_reports.md}`。

LLM 接入（可选）：设 `MEETINGVAR_LLM_BASE_URL / _API_KEY / _MODEL`，`digest --llm` 出叙述版；
未配置时输出结构化模板版并如实标注〔模板版〕。

## 文档

- [docs/plan.md](docs/plan.md) —— 完整计划书：定位 / 功能规格 / 竞品差异 / 赛程里程碑 / 两人分工
- [docs/daily_plan.md](docs/daily_plan.md) —— 逐日计划（你每天干什么看这个）
- [docs/pool_schema.md](docs/pool_schema.md) —— 待审片段池接口契约，**字段变更须两人确认**
- [docs/github_guide.md](docs/github_guide.md) —— GitHub 速成
- [docs/demo_script.md](docs/demo_script.md) —— 演示录制台词本
- [SETUP.md](SETUP.md) —— 环境踩坑记录（谁踩谁写）
