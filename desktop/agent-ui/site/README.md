# meetingVAR 官网（静态站）

线上地址：[https://meetingvar-site.vercel.app](https://meetingvar-site.vercel.app)
Vercel 项目：`danielxiong8s-projects/meetingvar-site`

GitHub Release：[latest](https://github.com/DanielXiong8/meetingVAR/releases/latest)
安装包直链：`https://github.com/DanielXiong8/meetingVAR/releases/latest/download/meetingVAR-setup.exe`
当前版本：`0.28.5`

从桌面端首屏扩展出来的独立产品网站：首屏保留原来的 sky hero、Instrument Serif 字标和 workspace 输入，往下是定位、三种证据能力演示、工作流程、本地优先边界、FAQ 和下载入口。

- 纯 HTML / CSS / JS，无框架、无构建、无 CDN、无外部请求。
- 所有素材（背景图、字体、图标）都在本地 `assets/`。
- 演示数据全部标注为「示例数据」，不连接真实会议库。

## 预览

在仓库根目录运行：

```bat
python -m http.server 4173 --directory desktop\agent-ui\site
```

打开 [http://127.0.0.1:4173/](http://127.0.0.1:4173/)。

也可以直接双击 `index.html`。建议用 localhost 预览：证据包的 SHA256 演示依赖 Web Crypto，在部分 `file://` 环境下会显示「不支持」提示，其余部分仍可正常浏览。

## 文件

```text
site/
├─ index.html                 页面结构
├─ styles.css                 视觉、响应式、动效
├─ app.js                     导航、首屏表单、三个证据演示、SHA256 校验
├─ assets/
│  ├─ sky-hero.webp           首屏/收尾背景（1920w）
│  ├─ sky-hero@2x.webp        高分屏（2560w）
│  ├─ sky-hero.jpg            WebP 不可用时的回退
│  ├─ icon.svg                favicon / 导航图标
│  └─ fonts/                  Instrument Serif + Geist（含 OFL 许可证）
└─ README.md
```

## 已实现的交互

- 首屏 workspace 命名：写入 localStorage，并同步到演示区标题；输入为空时使用「证据工作区」。
- 「How it works」：原地展开/收起说明面板。
- 承诺台账：点任意一行，右侧（移动端为下方）切换到该承诺的原始发言、时间戳和变更 diff。
- 找回那一刻：输入问题或点示例 chip，返回对应的 30–90 秒示例片段；播放按钮推进波形、时间和字幕。
- 证据包：浏览器内实时计算 4 个示例文件的 SHA256；「一键校验」显示 4/4 一致；「模拟篡改一个文件」会验证失败，随后可恢复。
- 下载入口：弹窗直接指向 GitHub Releases 的 latest release；安装包直链固定为 `meetingVAR-setup.exe`。
- 响应式：1280×800 / 1440×900 / 390×844 均无横向溢出；≤720px 时台账变为卡片布局。

## 事实边界

- 页面里的姓名、承诺、时间戳、片段、manifest 和文件大小都是示例，不是真实会议记录。
- 没有 CER/WER、准确率、用户满意度等指标声明。
- 桌面版最新安装包发布在 GitHub Releases（v0.28.5）；网站不托管安装包，也不连接真实会议库。
- 不声称与 Magic Bay 集成。

## 与桌面端的关系

这个目录是独立的公开站点，不参与 `desktop/agent-ui` 的 Next.js 构建，也不被 `desktop/app.py` 引用。桌面壳目前仍加载 `desktop/web-next` 的旧首页。

如果要把桌面壳的启动页也换成这个站点，属于 `desktop/app.py` / 桌面路由的 Core 改动，需要项目 owner 确认后再做；本次没有修改。

## 部署

把 `site/` 目录整体上传到任意静态托管即可，不需要构建步骤。页面没有第三方请求，离线打开也能工作（SHA256 演示除外，见上）。

### Vercel

```bat
cd desktop\agent-ui\site
npx vercel@latest login
npx vercel@latest deploy --prod --yes --project meetingvar-site --create-if-missing
```

`README.md`、`.gitignore`、`.vercelignore` 已通过 `.vercelignore` 排除，不会出现在公开站点里。

### 发版同步

每次发布新安装包后，更新 `index.html` 里的版本号、安装包大小和 SHA256，再重新部署。下载直链 `releases/latest/download/meetingVAR-setup.exe` 本身不需要改。