# 怎么看探测结果

## 正常结果

```json
{
  "python": {"version": "3.10.21", "implementation": "CPython"},
  "platform": {"system": "Linux", "release": "5.10", "machine": "x86_64"},
  "ffmpeg": {"found": true, "path": "/usr/bin/ffmpeg", "version": "ffmpeg version 6.1"},
  "ffprobe": {"found": true, "path": "/usr/bin/ffprobe"},
  "modules": {
    "faster_whisper": {"ok": true, "version": "1.2.1"},
    "av": {"ok": true, "version": "14.0.0"},
    "numpy": {"ok": true, "version": "1.26.4"}
  },
  "write": {"ok": true},
  "subprocess": {"ok": true, "returncode": 0},
  "hashlib": {"ok": true, "sha256_prefix": "93e35b6f369d"}
}
```

## 常见异常

| 现象 | 原因 | 处理 |
|---|---|---|
| `python.version` 是 3.8/3.9 | 运行时版本过低 | 核心代码用了 3.10 语法，需要改兼容或申请更高版本 |
| `ffmpeg.found=false` | 运行时没有 ffmpeg CLI | 只做转写定位，不生成 clip；或改用 PyAV 抽帧 |
| `faster_whisper.ok=false` | 未安装或依赖缺失 | 只吃用户提供的转写；不要现场演示 ASR |
| `av.ok=false` | PyAV 缺失 | faster-whisper 可能无法解码音频 |
| `write.ok=false` | 临时目录不可写 | 证据包无法落盘，需要换输出目录或改用只读输出 |
| `subprocess.ok=false` | 沙箱禁止子进程 | 不能调用 ffmpeg/ASR，退化为纯文本 Skill |
| `hashlib.ok=false` | 极罕见 | 证据包校验不可信，不能宣称“完整性校验” |

## 怎么记录

把完整 JSON 保存到：

```text
competition/xhs-skill-2026/evidence/runtime-probe.json
```

并在 `PLAN.md` 的“已验证”表里加一行：

```text
| CodeArts 运行时 | python scripts/probe.py | Go / No-Go：<结论> |
```

## 注意

- 探测脚本不联网、不读环境变量、不读密钥；
- 只写系统临时目录，不写用户目录；
- 如果运行时连 `python` 都没有，说明这类 Skill 不是脚本型运行时，需要改投“纯指令型 Skill”，两个参赛包都要重做。这是最坏情况，必须在 11/17 当天确认。