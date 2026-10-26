# 证据 Agent 三技能（0.17.0）

这三个能力不是独立页面，而是 ChatAgent / AgentLoop 可直接调用的 skill：

1. **Evidence Pack**：把命中片段变成可校验证据包；
2. **Commitment Ledger**：承诺账本，回答“谁改了口”；
3. **Find Moment**：按自然语言找回具体 30–90 秒片段。

## 1. Evidence Pack

触发：

```text
帮我把最近一次命中的会议片段生成证据包
```

Agent 先找 session + clip，生成一张待确认动作卡；确认后输出：

```text
<项目>/meetingvar/evidence_packs/<session>_<clip8>/
  manifest.json
  SHA256SUMS.txt
  summary.md
  summary.html
  summary.pdf（本机有 Edge/Chrome 时自动生成）
  audio.wav
  transcript.txt
  screens/frame_*.jpg
<同名>.zip
```

manifest 内容：

- session_id / clip_id / trigger / 时间窗；
- 相关转写；
- 每个文件的 SHA256；
- `consent: local-capture`；
- 生成器版本。

CLI：

```bash
python -m meetingvar evidence-pack --project "D:/项目/X" --session s1 --clip <clip_id>
python -m meetingvar evidence-pack --project "D:/项目/X" --query "交付时间"
```

`--query` 会先走 Find Moment 找片段，再打包。

## 2. Commitment Ledger

触发：

```text
打开承诺账本，看看谁改了口，原来的承诺是什么
```

数据来源：

- FactStore 的 commitment / action-item / decision；
- `versions/` 版本快照；
- `valid_from/valid_to/status`；
- 负责人/截止/正文变化。

输出示例：

```text
承诺账本

- [active] 周五交付第一版（小林 / 周一）
  - due: 周五 → 周一
  - body: 上一版 → 已更新
```

CLI：

```bash
python -m meetingvar commitment-ledger --project "D:/项目/X"
python -m meetingvar commitment-ledger --project "D:/项目/X" --query "交付"
```

## 3. Find Moment

触发：

```text
找回上次提到交付时间的那段原话和具体时间戳
```

数据来源优先级：

1. RAG meeting chunks（带 start_ts/end_ts/turn_ids）；
2. SessionCatalog 关键词匹配；
3. 片段池中查找邻近 clip。

返回：

- session_id；
- 开始/结束时间戳；
- 原句；
- turn_ids；
- 邻近的 clip_id（如果有）；
- why（关键词）。

CLI：

```bash
python -m meetingvar find-moment --project "D:/项目/X" --query "谁负责周五交付"
python -m meetingvar find-moment --project "D:/项目/X" --query "测试风险" --session s1
```

## Agent 工具

新增只读工具：

- `find_moment(query, session_id?, limit?)`
- `commitment_ledger(query?)`

当模型判断用户在问“哪一段/谁改口/原话在哪”时，应优先调用它们。

Evidence Pack 是写文件动作，不作为自由工具；必须走聊天里的确认卡。这样模型不能直接往磁盘写 ZIP。

## 测试

`tests/test_evidence_skills.py` 覆盖：

- 证据包 ZIP / manifest / SHA256 / 音频/帧/转写；
- 承诺账本从版本快照识别 due 变化；
- Find Moment 从 RAG chunk 返回 session/时间/原句；
- ChatAgent 的“生成证据包”动作卡与执行。

完整测试：543 项通过，0 跳过。

## 边界

- PDF 依赖本机 Edge/Chrome；没有浏览器时仍有 HTML/Markdown/转写/音频；
- Find Moment 的准确率取决于 RAG 索引和转写质量；
- 证据包只复制已保存的片段，不会重新录制；
- 承诺账本的“变化”来自事实版本，不代替人工确认。