# meetingVAR 桌面壳 v0.2.0

## 0.2.0：Agent 桌面页

新增 Agent 页：会前关注清单生成/勾选保存、会后五类会议事实预览/选择性写入、项目记忆问答与检索。Agent 通过 bridge 调用引擎 Python，复用工具契约、审批、Trace 和项目事实记忆。详见 [Agent Runtime](../docs/AGENT_RUNTIME.md)。

## 0.1.3：按硬件配置本地服务与统一API诊断

本地服务新增原生GPU后端，CPU备用明确关闭 `logits_all`，避免32K上下文触发18.5GiB数组申请。启动按钮验证实际生成，失败返回明确原因；API测试与项目读取共用客户端和参数，按上下文预算分块，失败不覆盖成功摘要。外部API若因思考耗尽输出预算，会自动关闭思考重试一次；读取输出默认8192。配置、实测和部署边界见 [本地模型与API服务说明](../docs/LOCAL_MODEL_0.1.3.md) 与 [外部API截断修复](../docs/API_OUTPUT_BUDGET_FIX.md)。本轮未修改 `desktop/web/**`。

## 0.1.2：API兴趣判断与讯飞RTASR

- 实时 `go/record` 停用结巴词匹配和TF-IDF直接保存；确认词只用于专业术语纠错。离线 replay/bench 保留旧规则用于实验。
- 桌面启动显式传 `--interest api|manual`。API模式使用自定义 Base URL、模型名与 Key，缺少配置阻止启动并提示；API超时或无效证据不会回退规则保存，手动VAR仍可用。
- 最近120秒的定稿转写、主页关注事项、确认词和已保存的项目模型阅读摘要交给API，约30秒检查一次（需新增定稿）。模型返回事件类型、理由与证据ID，系统核验ID并从原始时间映射取证。
- 同事件去重；近邻不同事件合并/延后提交，避免冷却期永久丢弃新决策。该机制不代表已经验证真实会议判准率。
- 新增 `--asr xfyun_rtasr`，使用“实时语音转写”服务的 APPID/APIKey，保持WebSocket长连接；按40ms发送16k单声道PCM，临时字幕覆盖、句尾定稿入管线。IAT仍是单独分块备用。
- RTASR返回的角色切换标记不当作人名，当前显示未知说话人；桌面默认不自动点名。
- 旧版设置首次升级推荐RTASR与API模式，保留原有API字段。启动不自动录音或发API请求；点击开始会议后音频发讯飞、定稿文本发所配API。可明确选本地识别或仅手动保存。
- 检测讯飞按钮只检查凭证格式；测试API按钮实际发一条简短生成请求，产生少量用量。前者不证明网络和配额可用。
- 项目全库理解完成后保存 `meetingvar/interest_context.json`；会中复用该摘要。未完成阅读时只提供已确认词和用户关注事项，不声称读懂全部项目。

会议采集控制台、项目页与会议库。UI 启动独立 CLI 引擎，使用结构化事件显示字幕和采集状态。
构建产物：`meetingVAR.exe`（壳）→ `meetingVAR-setup-0.2.0.exe`（安装包，自制、免管理员）。

## 字幕与轻量录屏

- RTASR模式不走固定分块，临时字幕持续更新。备用本地模式恢复8秒块、3秒重叠；实际延迟包含模型推理，不能等同于逐字流式。
- 每句完整发送；长句、换行、带空格的姓名均走 JSON 事件。本场字幕可滚动回看，回翻时暂停自动跟随。
- 状态区显示加载、就绪、识别耗时、音频输入电平和停止收尾。录屏预览每秒更新，不等于录屏采样帧率。
- 采集系统默认屏幕，目标 5fps、最大宽 1600、JPEG 质量 75；相同画面复用，按回溯时长加 30 秒保留，并限制压缩帧缓冲为 128MiB。触及限制时会显示覆盖缩短。
- 保留默认选择性保存：未命中不保存会话媒体；命中后保存回溯音频和画面。桌面不启用旧 `--hd` 高清补录。
- 结束会议时把已保存片段合成 H.264/AAC MP4；原始 WAV/JPEG 保留。无 ffmpeg 或合成失败时提示，并可在会议库用音频加画面时间轴回看。较早没有屏幕的时段显示缺失，不提前展示未来画面。
- MP4 使用原音频时长和屏幕时间戳对齐；媒体服务支持 Range 请求，便于拖动进度条。
- 新版 UI 需要重新打包；引擎仍由设置中的 Python 与本地仓库提供，不随安装包分发。

录屏参数可通过 `MEETINGVAR_SCREEN_FPS`、`MEETINGVAR_SCREEN_WIDTH`、`MEETINGVAR_SCREEN_QUALITY`、`MEETINGVAR_SCREEN_MB` 调整。目标帧率不是设备实测帧率保证。

## 架构

```
[UI 壳 pywebview] ──启动──▶ [meetingvar go/record … 子进程（现有 CLI，cwd=仓库）]
        ▲                          │
        │ a. 事件流：stdout 逐行解析（状态/命中/点名/转写行）→ 前端 0.8s 轮询 poll_events()
        │ b. 输入流：UI → 子进程 stdin —— ":var"=手动 VAR；名字=说话人点名应答；":stop"=优雅停止
        └──c. 停止：优先 stdin ":stop"；CTRL_BREAK/terminate 仅作兜底
```

零落盘纪律、L3 冻结纪律原样保留；UI 不 import 引擎核心、不碰保存链路。
媒体播放：后端对 `data/` 起 127.0.0.1 只读静态服务（音频/高清 mp4 直接 `<audio>/<video>`）。

## 文件

| 文件 | 作用 |
|---|---|
| `app.py` | 后端：窗口、子进程桥、事件解析、本地文件服务、配置（%APPDATA%\meetingVAR\ui_config.json） |
| `engine_bridge.py` | 引擎侧**只读**预览桥（`preview <folder>` / `check`），由引擎 Python 执行，输出单行 JSON |
| `web/` | 前端三件套（index/style/app.js）：主页 / 项目页 / 会议库 / 设置 / 点名弹窗 / 命中 toast |
| `assets/make_icon.py` | 生成 icon.ico（多尺寸）+ logo.png |
| `build/build_exe.bat` | 打包 `dist\meetingVAR.exe`（PyInstaller，需 D:\env\ui-venv） |
| `build/build_setup.bat` | 打包安装器 `dist_installer\meetingVAR-setup-0.2.0.exe`（内嵌上面的 exe） |
| `build/setup_app.py` | 安装器/卸载器三合一：GUI 向导 + `--silent --dir X` + `--uninstall` |

## 构建

```bat
desktop\build\build_exe.bat      :: dist\meetingVAR.exe
desktop\build\build_setup.bat    :: dist_installer\meetingVAR-setup-0.2.0.exe
```

安装/卸载（免管理员，装到 %LOCALAPPDATA%\Programs\meetingVAR，建桌面+开始菜单快捷方式）：

```bat
meetingVAR-setup-0.2.0.exe                          :: GUI 向导
meetingVAR-setup-0.2.0.exe --silent --dir D:\apps\mv :: 静默
"%LOCALAPPDATA%\Programs\meetingVAR\uninstall.exe" --uninstall
```

**引擎不在安装包内**：首次运行在「设置」页指定引擎 Python（如 `D:\miniconda3\envs\meetingvar\python.exe`）
与仓库目录（本机均自动探测默认值）；没有引擎时壳仍可打开（项目页/库页只读不可用并如实报错）。

## 自检

```bat
python app.py --selftest        :: 开窗 8~10s → 后端引导验证 → 硬退出，打印 SELFTEST-OK
python app.py --smoke-engine    :: 不开窗演练全链：起会→手动 VAR→":stop" 优雅收尾（真实引擎）
```

## 已踩的坑（冻结/打包/桥相关，改动前先知）

1. **pywebview 必须钉 5.4**：6.2.1（装到过的最新版）在本机 WebView2 运行时时，
   **一启动就刷 `window.native.AccessibilityObject.Bounds.Empty….Empty` 无限递归错误**，
   GIL 被占死 → 后台线程全部饿死（表现为"计时器不触发"的假死）。5.4 稳定（ui-venv 已降级）。
2. **js_api 对象的公开属性必须全部下划线私有**（`_window` / `_proc` / `_cfg`）：
   pywebview `util.get_functions()` 会**递归遍历** js_api 上"带 `__module__` 的非可调用公开属性"——
   `self.window`（pywebview Window 对象）会把遍历带进 WinForms 原生对象，最终
   `Rectangle.op_Equality` TypeError，**JS 桥静默失效（前端一片空白，日志无显错）**。这个坑花了最久才挖到。
3. **pywebview `window.destroy()` 从后台线程调用会在 WinForms 上死锁**（实测 80s 无响应）——
   自检路径改为"验证开窗后硬退出"，正常关闭走用户点 X（原生行为）；
4. **CTRL_BREAK_EVENT 不可用于优雅停止**：引擎进程内 MKL 的 Fortran 运行库（forrtl）
   会劫持控制事件直接 abort（错误 200），绕过全部收尾 —— 因此新增 stdin `:stop` 哨兵通道
   （引擎侧 `live.py` 两行改动），信号只作兜底；
5. **conda 版 Python 冻结后 `_ctypes.pyd` 找不到 `ffi.dll`**（`Library\bin\ffi.dll`，pefile 实查；
   pythonnet 加载失败 → WebView 起不来）——`build_exe.bat` 已显式 `--add-binary`（ffi.dll + ffi-8.dll）；
   pythonnet/clr_loader 需 `--collect-all`；
6. pywebview 5.4 会把本地页面目录用自带 bottle 服务器挂载（另一个 127.0.0.1 临时端口），
   与本应用自己的 data/ 媒体服务并存；
7. 前端必须等 `pywebviewready` 事件再调 `pywebview.api.*`（脚本加载时桥可能未注入）；
8. 引擎 stdout 的 funasr `[INFO] decoding…` 空 VAD 日志每 5 秒一条，UI 侧已过滤；
   `[WARNING]/[ERROR]` 保留（如实暴露引擎问题）；冒烟脚本检测"会议监听开始"要扫 log 事件
   （该行不是 status 事件——等满 240s 的冤枉路走过一次）。
9. **`hidden` 属性会被作者样式里的 `display` 覆盖**——`.modal{display:flex}` 曾让点名弹窗
   "跳过/确认/回车都关不掉"（事件其实都执行了，只是弹窗永远显示）。全站由
   `[hidden]{display:none !important}` 兜底；另外 `.input{flex:1 1 220px}` 在**纵向** flex
   容器里会把 220px 当高度基准把输入框撑成大块，弹窗/表单里用 `.modal-card .input,.field>.input{flex:0 0 auto}` 修正。

## v0.2 功能清单（布局参照 zai-org/ZCode 的信息结构）

- **三栏布局**：左导航 / 中工作区 / **右栏项目文件树**（VSCode 式，随当前项目自动恢复，↻ 可刷新）
- 主页：项目卡 / 大红录制键 / 手动 VAR / 打开产物夹 / 实时（已听·命中·说话人）/ 转写实时流
  （含**系统行进实时流**：开始监听/归档/错误；引擎退出 toast 附最后一条日志）
- 项目页：选择文件夹 → 只读预览（词表/代码符号/人物/跳过非文本文件提示）；文件树在右栏
- 会议库：**按项目分组**（项目名 + 路径 + "N 场 · 相对时间"，可展开场次 → 纪要/证据回放/厚纪要；
  无映射会话归入"未归属"）
- 设置：引擎/仓库/设备 + **本地 LLM 服务管理**（状态指示 / 一键启动停止 / GGUF 路径 / 开会时启用
  语义层开关）；顶栏常驻 LLM ●/○ 指示灯
- **音频采集源三档**（设置页）：系统声音+麦克风（默认）/ 仅系统声音 / 仅麦克风——
  在线会议远端声音走环回、自己的声音走麦克风，两头都录；引擎侧 `--source` CLI 同款。
  注意：扬声器外放时麦克风会捎带收回外放声（声学回声，文本会变碎）；
  戴耳机开会（both，最干净）或外放时选"仅系统声音"。
- 说话人点名弹窗、命中 toast

**v1.1 待办**：托盘常驻与最小化、双击库页音频定位、选词人接进项目页预览、项目置顶。
