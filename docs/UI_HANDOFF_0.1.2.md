# meetingVAR 0.1.2 · UI 交接

面向首次接手的 UI 同学。本文按 0.1.2 实现核对；测试边界见末节，不代表已完成真实会议验收。桌面版本取 `bootstrap().version`，不要用引擎包版本代替。

## 1. 当前行为

- **术语只用于保守纠错，不直接触发保存。** 项目确认词来自 `<项目目录>/meetingvar/terms.txt`；项目预览的原始抽取词不等于确认词。实时 `go/record` 停用热词与 TF-IDF 自动触发，旧规则仍用于历史数据和离线实验。
- **自动保存由 API 判断会议事件，手动 VAR 始终独立。** 默认桌面 `interest_mode=api`；也可明确选择 `manual`。API 使用最近 120 秒定稿转写、关注事项、确认词及已有项目阅读摘要，需新增定稿，通常按 30 秒步长检查。事件包括决策、分工/承诺、方向变化、风险/阻碍、关键未决问题、分歧、新证据。
- API 返回事件类型、理由、事件身份和证据 ID；引擎核验 ID，用原始发言时间定位证据，纠错文本仅作辨认参考。同事件去重，冷却期内不同事件暂存，之后或收尾时合并提交。配置缺失阻止 API 模式启动；超时、无效响应或证据无效会显示错误，**不退回词匹配/相似度保存，也不擅自切换模式**。手动 VAR 仍可用。
- **默认识别为讯飞 RTASR 长连接。** 临时字幕持续覆盖，句尾定稿才进入转写与兴趣判断；`xfyun` 是 IAT 分块备用，`local` 是 FunASR 分块备用。桌面始终传 `--no-ask-speakers`；RTASR 显示未知说话人 `spk?`，角色标记不能当成人名，不自动弹点名。
- **轻量录屏已接入。** 默认屏幕、目标 5fps、最大宽 1600、JPEG 质量 75；压缩帧内存预算 128MiB，按回溯时长加 30 秒保留，相同画面复用。回溯默认 300 秒，受已采集时长和内存淘汰限制；预览约每秒更新，不能据此宣称实测达到 5fps。
- **默认只保存命中媒体。** 零命中不创建该会话的片段、转写和报告；命中后保存所选音视频窗口，同时保存薄纪要所需的全场转写文本与摘要。配置、日志、项目资料不属于这项“零命中不存会议记录”承诺。`go` 有保存时生成结构化纪要并归档，未自动生成 LLM 叙述版纪要。
- 收尾时将有画面的片段合成带音轨的 H.264/AAC MP4，保留 WAV/JPEG。缺少 ffmpeg 或合成失败会提示，仍可用音频加画面时间轴回看。桌面未启用旧 `--hd` 命中后补录。
- 选择 RTASR 后开会会向讯飞发送音频；API 模式会向所配端点发送上述文本上下文。`manual` 只关闭兴趣 API，不改变所选 ASR 的联网行为。已有项目摘要位于 `<项目目录>/meetingvar/interest_context.json`，缺失时不能宣称已经读懂全部项目。

## 2. 分支与源码职责

从 **`origin/feat/desktop-ui` 的最新提交**创建个人分支 `feat/ui-<名字>`。在工作区干净时执行；占位目录和名字需自行替换。

```bash
cd "<仓库目录>"
git fetch origin
git switch --no-track -c "feat/ui-<名字>" origin/feat/desktop-ui
```

不要从旧 `main` 直接开始，也不要两个人同时推同一分支；各自提交个人分支，通过 PR 协调集成目标。开发中及时同步集成分支、提前沟通重叠文件。这能减少冲突，**不能保证未来冲突为零**；本文不固定提交 hash。

| 源码 | 当前职责与协作建议 |
|---|---|
| [`desktop/web/index.html`](../desktop/web/index.html)、[`app.js`](../desktop/web/app.js)、[`style.css`](../desktop/web/style.css) | UI 优先在 `desktop/web/**` 内改布局、样式、状态展示和交互。HTML ID 与 JS 绑定需一起维护；保留 `hidden` 隐藏规则、字幕滚动回看与跟随行为。 |
| [`desktop/app.py`](../desktop/app.py) | pywebview 窗口、JS API、配置、引擎子进程、事件转换、会议库与本地媒体服务。桥接口、参数、返回字段或进程生命周期变化先与引擎同学协调。 |
| [`desktop/engine_bridge.py`](../desktop/engine_bridge.py) | 引擎侧项目预览、检测、全库理解、确认词写入、文件转写。顶部旧“全部只读”注释已不覆盖所有命令；写词、理解摘要和文件转写会写文件。 |
| [`events.py`](../src/meetingvar/events.py)、[`live.py`](../src/meetingvar/live.py)、[`pipeline.py`](../src/meetingvar/pipeline.py) | 事件协议、采集与停止收尾、定稿处理及保存。UI 不另行实现触发或落盘逻辑。 |
| [`monitor/semantic.py`](../src/meetingvar/monitor/semantic.py)、[`transcribe/xfyun_rtasr.py`](../src/meetingvar/transcribe/xfyun_rtasr.py)、[`capture/`](../src/meetingvar/capture/) | API 事件判断、RTASR 生命周期、音频/画面缓冲及视频合成；由引擎侧维护。 |
| [`docs/pool_schema.md`](pool_schema.md)、[`desktop/build/`](../desktop/build/) | 片段字段契约与打包；改池结构或发布方式应共同确认。 |

## 3. 必要前后端 API

调用入口是异步 `await pywebview.api.<方法>(...)`，不是 HTTP 业务路由。等待 `pywebviewready` 后调用；当前启动顺序是 `bootstrap → get_config → 每 800ms poll_events → page_ready`，轮询有并发保护。HTTP 服务仅用于本地媒体播放。

| 方法与参数 | 返回与必要语义 |
|---|---|
| `bootstrap()` | `{app, version, config, meeting_running, media_base, llm}`；`llm={up, model, managed, gguf}`。会启动本地媒体服务并探测本地模型状态，不启动会议。 |
| `get_config()` | 完整配置对象，无 `ok` 包装。 |
| `set_config(updates)` | 合并允许的键并持久化，返回完整配置，无 `ok` 包装。未知键忽略；字符串键只接受字符串，`projects` 接受列表并把元素转字符串，`use_llm` 按布尔转换。返回中包含本机路径及凭证字段，不应整包写入前端日志。 |
| `start_meeting(folder="", mode="go")` | 成功 `{ok:true, pid, started_at}`，失败 `{ok:false, error}`。正常主页传项目目录和 `"go"`；另有 `"record"` 路径无需项目目录（实现将任何非 `go` 值当 `record`，UI 只传这两个值）。校验引擎、项目、理解任务占用、兴趣/ASR 配置后启动进程；成功只代表进程已启动，采集就绪看 `phase`。 |
| `stop_meeting()` | 成功 `{ok:true, mode:"stdin"/"signal"/"terminate"}`，失败 `{ok:false, error}`。首选向 stdin 写 `:stop\n`；返回只是停止请求回执，继续轮询到 `exit`，不能立即显示已保存完成。 |
| `poll_events(since=0)` | `{events, next, running, started_at, screen, phase}`。返回 `i > since` 的事件，下一轮使用 `next`；`started_at` 是 Unix 秒或 `null`，`running` 表示进程仍存活，`screen` 是最新快照。 |
| `manual_var()` | `{ok:true}` 或 `{ok:false, error}`；写入 `:var\n`，不是保存完成回执。保存结果由后续命中事件及收尾摘要确认。 |
| `answer_prompt(text)` | 旧点名应答接口，返回同 `manual_var`；去掉首尾空白后写入 stdin，空字符串在点名流程中表示跳过。**当前没有 `answers()` 接口。** 桌面默认不点名，不应主动调用它；未启用点名时普通 stdin 输入会被引擎当手动 VAR。 |
| `session_detail(sid)` | `{sid, minutes, reports, clips}`；纪要为 Markdown 字符串，缺失时空字符串，片段缺失时空列表，无 `ok` 包装。具体片段字段见下表。 |

辅助入口：`page_ready(info)` 返回 `{ok:true}`；`list_sessions()` 返回 `{sid, lines, clips, minutes, mtime}[]`（这里 `minutes` 是是否存在纪要的布尔值）；`list_library()` 返回 `{project, name, sessions, latest}[]`，供项目分组与选取 `sid`。

`session_detail().clips[]`：

| 字段 | 含义 |
|---|---|
| `clip_id, layer, evidence, lines` | `clip_id` 当前只有前 8 位，供展示；触发层、解释文本、该片段转写句数。当前手动与 API 均为 `L3`，分别用 `manual_hotkey` 与 `API兴趣/<事件类型>:` 前缀区分，不能仅凭 L3 标成手动。 |
| `at, start_ts, end_ts` | 会议内相对秒；`at` 当前取音频结束时间，起止是实际保存音频窗口。 |
| `audio_url, video_url, hd_url` | 可为 `null`；桥生成的本地媒体 URL。优先播放 `video_url`；`hd_url` 是旧版独立时段、无音轨补录，不能当同步视频。 |
| `frames: [{ts, url}]` | 按会议相对秒升序。无 MP4 时，以 `start_ts + audio.currentTime` 选最近一张 `ts <= 当前时间` 的画面；首帧之前显示缺失，不提前展示未来画面。 |

媒体地址按本次本地服务端口生成，支持 Range 拖动播放；不要硬编码端口或直接拼本机文件路径。

## 4. 事件与状态约定

引擎 stdout 协议为 `@@MEETINGVAR_EVENT ` 加单行 JSON。桥给进入队列的事件补 `{i, t, type}`：`i` 单调递增，`t` 是桥接收时的 Unix 秒，**不是音频时间**。队列超过 4000 条时删除最早 1000 条，不是永久历史；新会议不会重置桥的序号，前端应延续游标，避免重放旧会议。

| `type` | 负载字段与 UI 处理 |
|---|---|
| `started` | `cmd, pid`；进入准备状态。`cmd` 可能含本机路径，用户界面无需常驻展示。 |
| `capture_state` | `phase, text`；常规流程 `loading → ready → stopping`，进程退出后轮询返回 `idle`。区分“进程运行”和“采集就绪”；停止与视频合成期间保持收尾态，禁用重复停止和手动 VAR，关闭窗口会先请求收尾。 |
| `partial_line` | `id, text`，可带 `clear:true`。相同 `id` 覆盖；空 `text` 删除该项；`clear` 清空全部。当前 UI 最多保留 4 项临时字幕，**不增加句数、不进入历史定稿或兴趣判断**。 |
| `line` | `clock, spk, text, ts`；定稿字幕，`clock` 为本地 `HH:MM:SS`，`ts` 为会议相对秒。RTASR 定稿先发同 ID 的空 `partial_line`，再由管线去重后发 `line`；UI 只按 `line` 增加句数。`line` 没有临时字幕 `id` 字段；旧日志解析兼容路径也可能没有 `ts`。保留全文、换行及含空格姓名。 |
| `asr_stream_state` | `state, message`；状态有 `connecting/ready/reconnecting/error/stopped`。错误或重连时清掉临时字幕并显示消息，不把残留临时文字伪装成定稿，也不把重连提示当会议结束。 |
| `asr_state` | 分块备用识别：`processing:true, seconds`；完成时 `processing:false, latency, lag`；失败时 `processing:false, error`。不是 RTASR 的逐字进度。 |
| `audio_state` | `rms, captured_until`；归一化音频电平、采集到的绝对时间秒。只表明音频输入，不证明识别成功。 |
| `interest_state` | `state, message`；状态有 `ready/manual/checking/no_hit/suppressed/saved/error`。`ready` 也可能表示新事件已暂存，应显示 `message`；`saved` 是证据已保存，不表示 MP4 已合成。错误必须可见，不能伪装成 `no_hit`。 |
| `hit` | `text`；由 `>>> VAR!` 日志转换，当前 UI 据此加命中数。不要再因 `interest_state.saved` 重复加数，也不要假定负载含 `clip_id` 或 API 原始 JSON。 |
| `meeting_summary` | `clips, lines, saved`；引擎汇总，`saved` 表示已生成转写文件，不保证 MP4 成功。桥会把它带到最终 `exit.summary`。 |
| `recording_warning` | `text`；视频合成等问题，展示提示并保留原证据回看入口。 |
| `exit` | `code, last_log, summary`；进程退出，区分正常结束、零保存和异常结束。`summary` 可能为空；不要以 `code=0` 推断一定有片段。 |
| `log/status/ask` | 旧文本日志兼容：前两者为 `text`，`ask` 为 `spk, sample`；点名弹窗保留兼容代码，默认桌面流程不会主动产生点名请求。 |

**屏幕预览单独处理。** 引擎的 `screen_state` 不入事件队列；桥只保存最新值，放在 `poll_events().screen`，初始为 `{}`。字段为 `backend, fps_target, max_width, frame_count, buffer_bytes, budget_bytes, oldest_ts, last_frame_ts, last_success_ts, limited, error, preview`。`preview` 是 JPEG data URL 或 `null`；只替换同一个预览，不累计进字幕、历史数组或日志。时间戳为 Unix 秒；静态屏幕 `last_frame_ts` 可以不变，健康状态看 `last_success_ts/error`；`limited` 表示发生过预算淘汰，覆盖时长不能一律显示“完整 5 分钟”。

## 5. 现用配置名

以 `desktop/app.py:CONFIG_DEFAULTS` 为准，存于用户本机 `ui_config.json`。以下是源码默认值，不是任何人的实际配置；会议参数在启动时读取，修改通常下次会议生效。

| 键 | 默认/取值与用途 |
|---|---|
| `engine_python`, `repo_dir` | 空字符串起始，随后尝试本机探测；分别指定引擎 Python 和仓库目录。不要依赖作者机器的探测路径。 |
| `device` | `cuda`；可选 `cuda/cpu`。 |
| `last_project`, `projects` | `""`、`[]`；最近项目和已知项目，`go` 启动路径最多保留 30 个。 |
| `interest_mode`, `interest_focus` | `api`、`""`；模式仅 `api/manual`，本场关注事项最多向引擎传 6000 字符。 |
| `asr_engine`, `xfyun_dir` | `xfyun_rtasr`、`""`；引擎可选 `xfyun_rtasr/xfyun/local`，讯飞模式需对应服务凭证目录。 |
| `audio_source` | `both`；可选 `both/loopback/mic`。 |
| `llm_base_url`, `llm_model`, `llm_api_key` | 均默认空；实时 API 兴趣判断直接使用这三个键。地址须为不含用户信息的 HTTP(S) 地址，不能把密钥放入 URL；地址和模型缺失会阻止 API 模式启动。 |
| `llm_provider`, `llm_gguf` | `local`、`""`；`local/custom` 控制项目全库理解的模型服务，GGUF 用于本地模型。**切换 provider 不会替代实时兴趣 API 的地址/模型配置。** |
| `use_llm`, `meeting_engine_version` | `false`、`0.1.2`；前者为旧兼容项，不控制实时兴趣模式。已有配置版本不符时迁移为 RTASR、API 模式及 `llm_provider=custom`，保留原 API 字段。 |

录屏参数 `MEETINGVAR_SCREEN_FPS/SCREEN_WIDTH/SCREEN_QUALITY/SCREEN_MB` 和回溯参数 `MEETINGVAR_LOOKBACK` 是引擎环境变量，不是 `set_config` 接受的 UI 键。顶栏“兴趣 API 已配置”仅检查地址和模型非空，实际调用健康状态看 `interest_state`。

## 6. 安装、测试与验收边界

安装包 `meetingVAR-setup-0.1.2.exe` 安装的是 **UI 壳**，包含前端资源和桥文件，仍依赖设置中指定的本机引擎 Python、仓库与所选能力的依赖。它不包含完整引擎环境、模型或凭证；MP4 合成依赖 ffmpeg。改前端后需重新打包才会更新已安装版本。参见 [桌面说明](../desktop/README.md)；构建脚本中的本机路径需适配，不能直接视作通用环境。

建议 UI 先用合成数据实现 `window.pywebview.api` mock，覆盖初始化、启动失败、准备/就绪/收尾、临时字幕多次覆盖后定稿、重连、兴趣错误、零保存、预览替换、缺失画面及回放跳转。普通浏览器不会自行提供 Python 桥；mock 不应读真实配置、启动引擎或发送请求。

| 已有测试入口 | 覆盖与边界 |
|---|---|
| `tests/test_semantic_monitor.py`、`tests/test_xfyun_rtasr.py` | 注入假 API/假 WebSocket，覆盖证据核验、去重、错误、重连、临时/定稿及停止。不能证明真实账号、配额、识别质量或会议判准率。 |
| `tests/test_live_interest_integration.py` | 合成音频与替代采集/识别/API，覆盖停用规则、定稿触发、失败不回退及手动保存。 |
| `tests/test_desktop_backend.py` | 事件解析、配置、启动参数、会议库等。配置改指临时目录，但包含本地 LLM 状态探测，不能把整套测试称作完全无网络动作。 |
| `tests/test_live_desktop_recording.py` | 合成字幕、画面缓冲、预览不累积、回放时间轴；其中 Range 用例启动本地 HTTP 服务，视频用例可能调用真实 ffmpeg。 |

运行 Python 集成测试前要隔离环境与配置：部分导入链会加载仓库 `.env`，测试替身不能自动隔离这一步。UI 纯视觉联调优先 mock；真实测试另行准备环境与合成素材。

本次上传前，本地 Python 3.10 最小依赖环境执行 `scripts/run_local_checks.py --skip-simulation --isolated-cwd` 通过：发现 329 项，执行通过 288 项，按依赖条件跳过 41 项，编译检查通过。另在带依赖的环境验证 3 项图像/视频测试和完整 CLI 仿真，4 项全部通过；CLI 仿真的声纹抽取使用替身，未启动真实模型或采集设备。这些是本地验证结果，不是 GitHub Actions 或真实会议质量报告。

真实动作应明确区分：`check_xfyun()` 当前只读取并检查凭证格式，不证明连接可用；`test_llm()` 会实际发短生成请求并产生用量；全库理解会调用所选模型；开始会议会打开设备并按设置联网。`--selftest` 会开窗、初始化媒体服务和配置，`--smoke-engine` 会真实起会，均不能冒充纯 UI 模拟。真实识别延迟、断线恢复、音画同步、事件判断质量和长会议稳定性仍需单独验收。

继续查阅 [desktop/README.md](../desktop/README.md) 与 [pool_schema.md](pool_schema.md)。根 README、旧使用指南及 [github_guide.md](github_guide.md) 含早期 CLI、全离线、L1/L2 和从 main 开始的说明；接手本轮 UI 时，以当前源码及本文明确的 0.1.2 行为和分支起点为准。
