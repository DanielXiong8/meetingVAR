# meetingVAR 桌面壳（oCam 风格）v0.1

小而专注的会议采集控制台 + "读取文件夹"的项目页 + 会议库；**CLI 引擎保持不变**，UI 只是壳。
构建产物：`meetingVAR.exe`（壳）→ `meetingVAR-setup-0.1.0.exe`（安装包，自制、免管理员）。

## 架构（零核心改动）

```
[UI 壳 pywebview] ──启动──▶ [meetingvar go/record … 子进程（现有 CLI，cwd=仓库）]
        ▲                          │
        │ a. 事件流：stdout 逐行解析（状态/命中/点名/转写行）→ 前端 0.8s 轮询 poll_events()
        │ b. 输入流：UI → 子进程 stdin —— 空行=手动 VAR；名字=说话人点名应答；":stop"=优雅停止
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
| `build/build_setup.bat` | 打包安装器 `dist_installer\meetingVAR-setup-0.1.0.exe`（内嵌上面的 exe） |
| `build/setup_app.py` | 安装器/卸载器三合一：GUI 向导 + `--silent --dir X` + `--uninstall` |

## 构建

```bat
desktop\build\build_exe.bat      :: dist\meetingVAR.exe
desktop\build\build_setup.bat    :: dist_installer\meetingVAR-setup-0.1.0.exe
```

安装/卸载（免管理员，装到 %LOCALAPPDATA%\Programs\meetingVAR，建桌面+开始菜单快捷方式）：

```bat
meetingVAR-setup-0.1.0.exe                          :: GUI 向导
meetingVAR-setup-0.1.0.exe --silent --dir D:\apps\mv :: 静默
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

## v0.1 功能清单

- 主页：项目卡 / 大红录制键 / 手动 VAR / 打开产物夹 / 实时（已听时长·命中数·说话人）/ 转写实时流
- 项目页：选择文件夹（对话框或手输）→ 只读预览：文档清单、抽取词表、代码符号、人物；一键用此项目开会
- 会议库：场次列表（片段数/有无纪要）→ 薄纪要 Markdown 渲染 + 证据片段音频/高清回放 + 厚纪要
- 设置：引擎 Python / 仓库目录 / 设备（cuda/cpu）/ 测试引擎 / 打开配置目录
- 说话人点名弹窗（≡ 终端点名）、命中 toast

**v1.1 待办**：托盘常驻与最小化、双击库页音频定位、引擎现实况日志窗、多项目最近列表。
