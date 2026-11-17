# Magic Bay 无缝接入技术栈（用于向联想提问）

> 目标：meetingVAR 不依赖某个具体配件也能跑；Magic Bay 到位时，只增加一个薄适配层，不改核心取证链路。
>
> **官方资料依据（2026-10-27 实读）**：`D:\项目\联想未来办公AI创新挑战\magic bay\Lenovo MagicBay 接口开放指南-V0.4.3.pdf`。  
> 指南明确：Magic Bay 是笔记本 A 面顶盖中央的磁吸 Pogo-Pin 接口，模块端走 **USB 2.0 / USB 3.0**，5V/3A/15W；可做“影像、显示、感知、音频、通信、AI 处理”等模块。指南配图里的在售模组包含**摄像头形态**和**小屏形态**。  
> 但指南开放的是**模块端**接口规范；主机端硬件架构、协议和 IP **不开放**，禁止反向工程。指南没有提供“第三方应用直接控制主机/现有模组”的 App SDK。

## 0. 一句话结论

**能接入，但要分清两条路：**

1. **应用层接入（现在可做）**：如果 Magic Bay 摄像头/小屏在 Windows 里枚举为标准 **UVC 摄像头 / USB 显示器 / UAC 音频设备**，meetingVAR 直接用系统 API 就能接，不需要联想 SDK。
2. **模块层接入（不建议比赛期做）**：如果要做一个专属 Magic Bay 证据模块（摄像头/补光灯/小屏/AI 加速器），可以按指南设计硬件，但这是硬件产品开发，不是 5 分钟 Demo 能完成的事。

**不能做的是**：直接调用主机端私有协议、逆向现有 Tiko/Tiko Pro 的 Widget、假设存在一个“万能 App SDK”。指南没有这些东西。

## 1. 结论先行

1. **现在就能做**：把 Magic Bay 小屏当 Windows 外接显示器，做一个 `HUD` 第二窗口；不调用厂商 SDK，插上就能用，拔掉自动回落主屏。
2. **摄像头也能做**：指南把摄像头列为 USB2.0 模块的典型场景；如果它是标准 UVC，`CaptureHub` 按普通摄像头枚举即可，不需要厂商 SDK。
3. **SDK 到位再加**：如果联想另外提供主机侧显示/输入/状态 API，再加 `LenovoAdapter`；核心代码不感知厂商。
4. **不要对外说“已集成 Magic Bay”**，除非在真机上跑过；现在只能说“Magic Bay-ready / 标准 UVC + 第二屏 HUD 已适配”。

## 1.5 官方指南关键事实（V0.4.3）

| 项目 | 指南内容 | 对 meetingVAR 的意义 |
|---|---|---|
| 位置 | 笔记本 A 面顶盖上沿中央 | 适合摄像头/补光/小屏/AR-HUD |
| 机械 | 磁吸对位 ±0.05mm；模块 ≤150g；磁吸力 ≥2kgf | 只和做硬件模块有关，App 不用管 |
| 电气 | Pogo-Pin；USB2.0 用 Pin 3、6–11；USB3.0 用全部 11 Pin | 摄像头/小屏本质是 USB 设备 |
| 供电 | 5V，最大 3A，15W | 模块功耗上限；不能反向供电 |
| 识别 | Pin9 ID_Detect 下拉电阻 215k/110k | 模块厂硬件设计用；App 不直接读 |
| 支持形态 | 影像、显示、感知、音频、通信、AI 处理 | 摄像头、小屏、麦克风都在生态范围内 |
| 在售模组 | 指南配图含摄像头形态、小屏形态 | 说明“Magic Bay 摄像头/小屏”不是臆想 |
| 主机端 | 架构/协议/IP 不开放；禁止反向工程 | **没有主机侧 App SDK**；只能走标准 USB/显示类 |
| 兼容机型 | ThinkBook 16p G4/G5/G6、ThinkBook X G1/G2 | 借测机优先 16p G6 |
| 联系 | `magicbay@lenovo.com` | 问 USB class、SDK、借测、认证 |

### 三种接入模式

| 模式 | 前提 | 工作量 | 比赛期建议 |
|---|---|---|---|
| **A. 标准设备接入** | 摄像头是 UVC、小屏是 Windows 显示器、麦克风是 UAC | 1–2 天适配 + 真机验证 | **首选** |
| **B. 厂商 App SDK** | 联想额外提供主机侧 API/Widget API | 加 `LenovoAdapter` | 先问，没答复就不等 |
| **C. 自定义模块** | 按指南做硬件、打样、认证、驱动 | 数月 | **不做**，最多放 PPT 概念图 |

**结论**：比赛期只做 A；B 作为问题去问；C 只作为“联想生态想象空间”，不要声称已实现。

## 2. 设计原则

- **硬件无关**：核心只依赖 Windows 标准接口（WASAPI、Media Foundation/UVC、DXGI、显示器枚举）。
- **单向依赖**：`LenovoAdapter` 依赖核心接口，核心不依赖 Lenovo SDK。
- **可降级**：没有 Magic Bay、没有摄像头、没有网络，主流程仍可完成“采集 → 定位 → 证据包”。
- **来源可追溯**：每条音频/屏幕/摄像头数据都记录设备名、后端、时间窗；证据包里能看出“这段来自哪个设备”。
- **本地优先**：HUD 与主程序通过本机 IPC 通信，不开放局域网端口；云端只做显式可关闭的增强。

## 3. 目标架构

```text
┌────────────────────────────── meetingVAR core ──────────────────────────────┐
│                                                                              │
│  AudioSource (WASAPI loopback / mic)  ─┐                                    │
│  ScreenSource (DXGI / mss)             ├─> CaptureHub ─> SessionClock       │
│  CameraSource (Media Foundation / UVC) ─┘        │                           │
│                                                   ▼                          │
│                                        SessionStore / ClipPool              │
│                                                   │                          │
│                         ┌─────────────────────────┼──────────────────────┐   │
│                         ▼                         ▼                      ▼   │
│                    Local ASR                RAG / FTS5              Evidence  │
│                         │                   + BGE vectors            Skills    │
│                         └───────────────> Agent / Chat <──────────────┘       │
│                                                   │                          │
│                                        EvidenceBus (local IPC)               │
│                                                   │                          │
│                          ┌────────────────────────┴──────────────────────┐   │
│                          ▼                                               ▼   │
│                    Main Window (pywebview)                        HUD Window  │
│                    chat / evidence / ledger                       second screen│
└──────────────────────────────────────────────────────────────────────────────┘
```

关键点：**HUD 不是新后端**，只是同一份 `HudState` 的另一个渲染端。

## 4. 核心接口

### 4.1 CaptureSource

```python
class CaptureSource(Protocol):
    def describe(self) -> DeviceDescriptor: ...
    def start(self) -> None: ...
    def read(self) -> FramePacket | None: ...
    def stop(self) -> None: ...
```

`DeviceDescriptor` 至少包含：

```json
{
  "device_id": "wasapi:{endpoint_id}",
  "kind": "audio_loopback | microphone | screen | camera",
  "name": "Microphone Array (Lenovo)",
  "vendor": "Lenovo",
  "model": "",
  "backend": "wasapi | mf | dxgi | mss",
  "capabilities": {"sample_rate": 48000, "channels": 2, "width": 1920, "height": 1080, "fps": 30},
  "removable": true,
  "monitor_index": null
}
```

要求：

- 枚举失败不抛到 UI，返回空列表 + 原因；
- 设备热插拔后重新枚举，不能静默把新设备当成旧设备；
- 设备名只用于展示和证据来源，不作为唯一 ID。

### 4.2 DisplayTarget

```python
class DisplayTarget(Protocol):
    def list_displays(self) -> list[DisplayDescriptor]: ...
    def open_hud(self, display_id: str, state: HudState) -> None: ...
    def update_hud(self, state: HudState) -> None: ...
    def close_hud(self) -> None: ...
```

`DisplayDescriptor`：

```json
{
  "display_id": "\\\\.\\DISPLAY2",
  "name": "Magic Bay Display",
  "x": 1920, "y": 0, "width": 1920, "height": 1200,
  "dpi": 144, "primary": false, "work_area": [0, 0, 1920, 1160]
}
```

实现方式：

- Windows `EnumDisplayMonitors` + `GetMonitorInfoW`（ctypes）枚举；
- pywebview 创建第二个无边框窗口，按 `work_area` 定位；
- 监听 `WM_DISPLAYCHANGE` / 定时轮询，拔掉副屏后把 HUD 移回主屏并提示；
- 记住用户上次选择的 `display_id`，下次自动恢复；
- 不要求厂商 SDK。

### 4.3 HudState

HUD 只读，不写业务数据：

```json
{
  "recording": true,
  "elapsed": 73,
  "session_id": "go_...",
  "last_transcript": "……周五先交第一版……",
  "active_commitment": {"title": "周五交付第一版", "owner": "小王", "due": "周五"},
  "evidence_count": 2,
  "mode": "local",
  "consent": true,
  "devices": [{"kind": "microphone", "name": "..."}, {"kind": "camera", "name": "..."}]
}
```

HUD 内容优先级：

1. 录制状态 + 时长 + 本地/云端标识；
2. 最近一句字幕；
3. 当前命中的承诺/决策；
4. 证据数量；
5. 设备状态（麦克风/摄像头/副屏）。

8 英寸屏只放 1–4，不放大段文本。

### 4.4 LenovoAdapter（可选）

只在拿到 SDK 后实现：

```python
class LenovoAdapter:
    def probe(self) -> bool: ...
    def capabilities(self) -> dict: ...
    def push_hud(self, state: HudState) -> bool: ...
    def on_input(self, callback) -> None: ...
```

如果 Tiko/Tiko Pro 只能显示厂商自己的 widget：

- 不做逆向、不 root、不刷机；
- 改为“普通副屏 HUD”或“手机/平板 HUD”；
- 在 PPT 中明确写“Magic Bay-ready”，不写“已集成”。

## 5. 采集后端

| 数据类型 | 首选 | 回退 | 说明 |
|---|---|---|---|
| 系统声音 | WASAPI loopback (`pyaudiowpatch`) | 无 | 在线会议远端声音 |
| 麦克风 | WASAPI 默认输入 | 无麦克风时仅环回 | 现场说话人 |
| 屏幕 | DXGI (`dxcam`) | `mss` | 5 fps，JPEG，环形缓冲 |
| 摄像头 | Media Foundation (`cv2.CAP_MSMF`) | ffmpeg dshow | 720p/1080p，关键帧 |
| 副屏 | Win32 显示器枚举 + pywebview | 主屏浮动窗口 | HUD |

摄像头不是 P0；如果 Magic Bay 没有摄像头，不为了演示硬加。

## 6. 时钟、热插拔与证据来源

- **统一时钟**：会话启动时记录 `time.monotonic()` 与 `time.time()`；音频按采样点计数推进，屏幕/摄像头按采集时间戳；目标偏差 < 200 ms。
- **热插拔**：音频/摄像头掉线时写入 `gap` 事件，恢复后重新对齐；不允许把断档当连续音频。
- **副屏热插拔**：HUD 自动迁移，状态栏提示“副屏已断开，已回到主屏”。
- **证据 manifest** 增加：

```json
{
  "sources": [
    {"kind": "audio_loopback", "name": "...", "backend": "wasapi"},
    {"kind": "microphone", "name": "...", "backend": "wasapi"},
    {"kind": "screen", "name": "DISPLAY1", "backend": "dxgi"},
    {"kind": "camera", "name": "...", "backend": "mf"}
  ],
  "clock": {"session_start": 0.0, "skew_ms": 0}
}
```

## 7. 本地算力与 NPU

- **P0**：CPU 跑 FunASR / BGE / 4B llama.cpp；不承诺 NPU。
- **P1**：ONNX Runtime DirectML（Intel/AMD GPU）或 OpenVINO（Intel NPU）作为可选 provider；只有实测加速才在 PPT 写数字。
- **P2**：如果 Lenovo AI Now 提供第三方 API，再做 `LocalAiNowAdapter`；没有 API 就不碰。
- 不允许把“AI PC”讲成“NPU 一定加速”；讲“本地算力可离线完成核心链路”。

## 8. 安全与隐私

- HUD 与主程序用 pywebview JS API 或本机回环 WebSocket，加随机 token；不监听 `0.0.0.0`。
- 默认不上传；云端增强必须显式开关，并在 UI 显示 `local / cloud` 标识。
- `consent` 不能只写死 `local-capture`：P0 至少加一个“已获录制同意”勾选 + 时间戳；没有勾选不生成证据包。
- 证据包 SHA256 只能证明“文件未被改动”，不能证明“内容真实”或“法律有效”；对外说“可校验完整性”。
- 一键删除会话/证据包；删除后索引同步清理。

## 9. 验收测试

| 测试 | 通过标准 |
|---|---|
| 任意外接显示器 | HUD 在选定屏幕打开，位置/DPI 正确 |
| 副屏热插拔 | 拔掉 5 秒内回主屏；插回可恢复 |
| 8 英寸可读性 | 1.5 米内能读清录制状态和最近一句 |
| 音频 60 分钟 | 无累积漂移；掉线有 gap |
| 摄像头（如有） | 枚举、预览、关键帧、时间戳正确 |
| 断网 | 转写/检索/证据包可用；云端标识关闭 |
| 证据包 | `SHA256SUMS.txt` 全通过；manifest 记录来源设备 |
| 无 Magic Bay | 全部功能可用，HUD 可在主屏浮动 |

## 10. 向联想提问的清单

按优先级排序，直接发给对接人：

### A. 设备形态与协议（先问这组）

1. 在售的 Magic Bay 摄像头模组在 Windows 里是不是**标准 UVC 摄像头**？需要专用驱动吗？
2. 在售的小屏模组在 Windows 里是不是**标准外接显示器**？还是只能显示联想预置 Widget？
3. 麦克风/音频模组是否走标准 **UAC/WASAPI**？还是私有音频通道？
4. 指南写的是 USB2.0/USB3.0；实际模组枚举成什么 USB class（UVC/HID/大容量/厂商自定义）？
5. 双 13.3 英寸副屏、8 英寸 AI dashboard、Tiko / Tiko Pro 分别是独立设备还是组合？各自 USB class 是什么？
6. 是否与扩展坞、外接显示器、合盖模式共存？有没有已知冲突？

### B. SDK 与 API（决定要不要写 LenovoAdapter）

7. 指南只开放模块端；**有没有主机侧 App SDK**，让第三方应用向小屏推送自定义窗口/Widget？
8. 如果小屏是标准显示器，是否允许第三方应用直接开无边框窗口？有没有安全区域/常亮/烧屏限制？
9. 是否提供设备状态、触摸/手势/按键、亮度、隐私快门等 API？
10. 是否提供 NPU / GPU / Lenovo AI Now 的本地推理 API？第三方模型能否调用？
11. SDK 支持哪些语言（C++/C#/Python/JS）？是否有 Windows App SDK / WinRT 接口？是否需要 NDA？

### C. 兼容与测试

12. 哪些机型/代数支持 Magic Bay？指南列了 ThinkBook 16p G4/G5/G6、ThinkBook X G1/G2，是否还有更新机型？
13. 能否提供一台测试机或 Magic Bay 配件借测？测试周期、保修、损坏责任怎么算？
14. 驱动是否需要签名/管理员权限？企业环境是否允许安装？
15. 副屏分辨率、DPI、色域、刷新率、安全区域、烧屏策略是什么？
16. 8 英寸屏在 1–1.5 米距离的推荐字号/布局有没有规范？

### D. 品牌与合规

17. Demo / PPT / 视频中能否使用 Lenovo、Magic Bay、Tiko 的名称和产品图？是否需要审核？
18. 是否有推荐的“Powered by Lenovo / Compatible with Magic Bay”话术？
19. 对会议录音、屏幕采集、证据导出有没有额外的隐私/合规要求？
20. 是否要求数据必须本地处理、加密存储或通过某项认证？

### E. 商业合作

21. 是否有 AI PC 预装 / OEM / 联合解决方案的入口？
22. 如果比赛后继续做，Magic Bay 生态的合作流程是什么？

## 11. 一句话对联想说

> 我们不是再做一个会议纪要 App，而是把会议里的“谁在什么时候说了什么”变成可回看的本地证据。Magic Bay 的摄像头可以成为“证据摄像头”，小屏可以成为“证据 HUD”：开会时显示录制状态和当前承诺，会后显示证据时间线。我们优先按标准 UVC/显示器/WASAPI 接口实现，不碰主机私有协议；如果贵方有主机侧 App SDK，我们再加一层适配。想确认在售摄像头/小屏的 USB class，以及能否借测一台 ThinkBook 16p G6 + 模组。