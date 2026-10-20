/* meetingVAR 桌面壳前端逻辑：轮询事件流 + 页面渲染 + 点名弹窗 */
"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  next: 0, running: false, startedAt: null, hits: 0,
  speakers: new Set(), project: "", scan: null, mediaBase: "",
  askQueue: [], askOpen: false, ticker: [], phase: "idle", follow: true, captionCount: 0,
  lastCaptionAt: null, screen: {}, starting: false, polling: false, partials: new Map(),
  agentFocus: [], agentFocusSources: [], agentFacts: [], agentBusy: false,
  pageHistory: ["home"], pageIndex: 0, usage: null, usageRange: 7, libraryGroups: [], consolePinned: false,
  chatId: "", chatMessages: [], chatBusy: false,
};

/* 全局错误可见化 + 导航事件委托（避免某个旧处理器异常后整页失效） */
window.addEventListener("error", (event) => {
  const t = document.getElementById("toast");
  if (t) { t.hidden = false; t.textContent = "界面脚本错误：" + (event.message || "未知错误"); }
});
window.addEventListener("unhandledrejection", (event) => {
  const t = document.getElementById("toast");
  if (t) { t.hidden = false; t.textContent = "后台调用失败：" + (event.reason && event.reason.message ? event.reason.message : event.reason); }
});
document.addEventListener("click", (event) => {
  const target = event.target && event.target.closest ? event.target.closest(".navbtn") : null;
  if (target && target.dataset.page) switchPage(target.dataset.page);
});

/* ---------- 小工具 ---------- */
function esc(s) {
  return String(s == null ? "" : s)
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}
function fmtClock(sec) {
  sec = Math.max(0, Math.floor(sec));
  const m = Math.floor(sec / 60), s = sec % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}
/* 相对时间（参照 ZCode 侧栏：刚刚 / N分 / N小时 / N天） */
function relTime(tsSec) {
  if (!tsSec) return "";
  const d = Math.max(0, Date.now() / 1000 - tsSec);
  if (d < 60) return "刚刚";
  if (d < 3600) return Math.floor(d / 60) + "分";
  if (d < 86400) return Math.floor(d / 3600) + "小时";
  return Math.floor(d / 86400) + "天";
}
function toast(msg, ms = 5000) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(t._timer);
  t._timer = setTimeout(() => { t.hidden = true; }, ms);
}
const PAGE_TITLES = { home: "新任务", project: "项目", library: "会议库", agent: "Agent", settings: "设置" };
function switchPage(name, push = true) {
  const target = document.getElementById("page-" + name);
  if (!target) return;
  const home = document.getElementById("page-home");
  if (name === "home") {
    document.querySelectorAll(".page").forEach((p) => p.classList.toggle("active", p.id === "page-home"));
    document.body.classList.remove("panel-open");
    $("panelScrim").hidden = true;
  } else {
    if (home) home.classList.add("active");
    document.querySelectorAll(".page.panel").forEach((p) => p.classList.toggle("active", p === target));
    target.classList.add("active");
    document.body.classList.add("panel-open");
    $("panelScrim").hidden = false;
  }
  document.querySelectorAll(".navbtn").forEach((b) => b.classList.toggle("active", b.dataset.page === name));
  const workspace = document.querySelector(".workspace");
  if (workspace) { workspace.scrollLeft = 0; workspace.scrollTop = 0; }
  $("pageTitle").textContent = PAGE_TITLES[name] || name;
  document.title = "meetingVAR " + (($("ver") && $("ver").textContent) || "") + " · " + ($("pageTitle").textContent);
  if (push) {
    state.pageHistory = state.pageHistory.slice(0, state.pageIndex + 1);
    if (state.pageHistory[state.pageHistory.length - 1] !== name) state.pageHistory.push(name);
    state.pageIndex = state.pageHistory.length - 1;
  }
  if (name === "library") refreshLibrary();
  if (name === "agent") loadAgentPage();
  if (name === "settings") loadUsage();
}
function pageBack() {
  if (state.pageIndex <= 0) return;
  state.pageIndex -= 1;
  switchPage(state.pageHistory[state.pageIndex], false);
}
function pageForward() {
  if (state.pageIndex >= state.pageHistory.length - 1) return;
  state.pageIndex += 1;
  switchPage(state.pageHistory[state.pageIndex], false);
}
function openMeetingConsole() {
  state.consolePinned = true;
  showMeetingConsole(true);
}
function showMeetingConsole(show = true) {
  const box = $("meetingConsole");
  if (!box) return;
  box.hidden = !show;
  const workspace = document.querySelector(".workspace");
  if (show) box.scrollIntoView({ behavior: "smooth", block: "start" });
  else if (workspace) workspace.scrollTop = 0;
}

/* ---------- 迷你 Markdown 渲染（足够覆盖我们自己的纪要格式） ---------- */
function mdToHtml(md) {
  const lines = esc(md).split(/\r?\n/);
  let html = "", inCode = false, inList = false;
  const closeList = () => { if (inList) { html += "</ul>"; inList = false; } };
  for (const raw of lines) {
    const line = raw;
    if (line.trim().startsWith("```")) {
      closeList();
      html += inCode ? "" : "<blockquote><code>";
      inCode = !inCode;
      if (!inCode) html += "</code></blockquote>";
      continue;
    }
    if (inCode) { html += line + "<br>"; continue; }
    let m;
    if ((m = line.match(/^(#{1,3})\s+(.*)$/))) {
      closeList();
      const lv = m[1].length;
      html += `<h${lv}>${m[2]}</h${lv}>`;
    } else if (line.match(/^\s*[-*]\s+/)) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += `<li>${inline(line.replace(/^\s*[-*]\s+/, ""))}</li>`;
    } else if (line.match(/^\s*>\s?/)) {
      closeList();
      html += `<blockquote>${inline(line.replace(/^\s*>\s?/, ""))}</blockquote>`;
    } else if (line.trim() === "") {
      closeList();
    } else {
      closeList();
      html += `<p>${inline(line)}</p>`;
    }
  }
  closeList();
  if (inCode) html += "</code></blockquote>";
  return html;
  function inline(s) {
    return s
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
  }
}

/* ---------- 事件轮询 ---------- */
async function poll() {
  if (state.polling) return;
  state.polling = true;
  try {
    const res = await pywebview.api.poll_events(state.next);
    state.next = res.next;
    for (const ev of res.events) handleEvent(ev);
    if (res.phase) state.phase = res.phase;
    if (res.running !== state.running) setRunning(res.running, res.started_at);
    if (res.screen) renderScreenStatus(res.screen);
  } catch (e) {
    if (state.running) $("captureStatus").textContent = "界面连接暂时中断，正在重试";
  } finally { state.polling = false; }
}

const SYS_LINE_RE = /\[rec\]|\[go\]|\[done\]|\[spk\]|\[calibration\]|归档|停止指令|就绪|Traceback|Error|error/i;

function handleEvent(ev) {
  if (ev.type === "line") {
    state.gotLines = true;
    state.lastCaptionAt = Date.now();
    $("asrStatus").textContent = (state.cfg || {}).asr_engine === "xfyun_rtasr" ? "讯飞RTASR持续转写 · 已收到定稿" : "已收到分块识别结果";
    state.captionCount += 1;
    $("captionCount").textContent = `${state.captionCount} 句`;
    state.speakers.add(ev.spk);
    $("statSpeakers").textContent = "说话人 " + [...state.speakers].join(" / ");
    pushTicker(`${ev.clock}`, ev.spk, ev.text, "");
  } else if (ev.type === "log") {
    // 关键进程行进实时流（让"正在听"可见）：开始监听/归档/错误等
    if (SYS_LINE_RE.test(ev.text)) pushTicker("", "系统", ev.text.trim(), "sys");
  } else if (ev.type === "hit") {
    state.hits += 1;
    $("statHits").textContent = "命中 " + state.hits;
    toast("VAR! " + ev.text.replace(/^.*VAR!\s*/, ""));
    pushTicker("", "★", ev.text, "hit");
  } else if (ev.type === "ask") {
    state.askQueue.push(ev);
    maybeShowAsk();
  } else if (ev.type === "partial_line") {
    if (ev.clear) state.partials.clear();
    else if (ev.text) state.partials.set(ev.id, ev.text);
    else state.partials.delete(ev.id);
    while (state.partials.size > 4) state.partials.delete(state.partials.keys().next().value);
    $("partialCaption").textContent = [...state.partials.values()].join("\n");
    $("partialCaption").hidden = !state.partials.size;
    if (ev.text) { state.lastCaptionAt = Date.now(); $("asrStatus").textContent = "讯飞实时转写中 · 当前句尚未定稿"; }
  } else if (ev.type === "asr_stream_state") {
    $("asrStatus").textContent = ev.message || ev.state;
    if (["error", "reconnecting"].includes(ev.state)) {
      state.partials.clear(); $("partialCaption").hidden = true;
      pushTicker("", "系统", ev.message || "实时识别连接异常", "err");
    }
  } else if (ev.type === "interest_state") {
    $("interestStatus").textContent = ev.message || ev.state;
    $("interestStatus").classList.toggle("warn", ev.state === "error");
  } else if (ev.type === "recording_warning") {
    toast(ev.text, 12000);
    pushTicker("", "系统", ev.text, "err");
  } else if (ev.type === "capture_state") {
    state.phase = ev.phase;
    $("captureStatus").textContent = ev.text || "";
    setRunning(state.running, null);
  } else if (ev.type === "asr_state") {
    $("asrStatus").textContent = ev.error || (ev.processing
      ? `正在识别 ${ev.seconds} 秒音频…`
      : `字幕识别耗时 ${ev.latency} 秒${ev.lag > 10 ? ` · 落后 ${Math.round(ev.lag)} 秒` : ""}`);
  } else if (ev.type === "audio_state") {
    $("audioLevel").value = Math.min(1, Math.sqrt(ev.rms || 0) * 2);
    $("audioStatus").textContent = ev.rms > 0.001 ? "音频有输入" : "音频输入较静";
  } else if (ev.type === "started") {
    state.phase = "loading";
    setRunning(true, Date.now() / 1000);
  } else if (ev.type === "exit") {
    state.phase = "idle";
    setRunning(false, null);
    $("captureStatus").textContent = ev.code === 0 ? "会议已结束" : "会议异常结束";
    $("audioLevel").value = 0;
    $("audioStatus").textContent = "音频已停止";
    $("asrStatus").textContent = "字幕已停止";
    state.partials.clear(); $("partialCaption").hidden = true;
    $("interestStatus").textContent = "兴趣判断已停止 · " + $("interestStatus").textContent;
    let msg = "会议已结束（引擎退出码 " + ev.code + "）";
    if (ev.code === 0) msg = ev.summary && ev.summary.saved
      ? `会议已结束，已保存 ${ev.summary.clips} 个证据片段`
      : "会议已结束，本场没有保存证据片段";
    else if (ev.last_log) msg += " · 最后一条：" + ev.last_log.slice(0, 110);
    toast(msg, 9000);
    pushTicker("", "系统", msg, ev.code === 0 ? "sys" : "err");
  } else if (ev.type === "understand_done") {
    if (ev.ok) toast("全库理解完成——回「项目」页勾选候选词并写入词表", 9000);
    else if (ev.partial) toast(`全库理解部分完成：${ev.chunks_read}/${ev.chunks_total} 块，覆盖 ${ev.coverage_pct}%——候选词已显示，可重跑续读`, 12000);
    else toast("全库理解中断：" + (ev.error || ""), 9000);
  } else if (ev.type === "transcribe_done") {
    if (ev.ok) {
      toast("文件转写完成：" + (ev.out || "").split("\\").pop(), 9000);
      pushTicker("", "系统", "文件转写完成 → " + (ev.out || ""), "sys");
    } else {
      toast("文件转写失败：" + (ev.error || "未知错误"), 9000);
      pushTicker("", "系统", "文件转写失败：" + (ev.error || ""), "err");
    }
  }
}

function pushTicker(t, spk, text, kind) {
  state.ticker.push({ t, spk, text, kind });
  const body = $("ticker");
  const div = document.createElement("div");
  div.className = "tk" + (kind ? " " + kind : "");
  div.innerHTML = `<span class="t">${esc(t)}</span><span class="s">${esc(spk)}</span>${esc(text)}`;
  body.appendChild(div);
  if (state.follow) body.scrollTop = body.scrollHeight;
}
$("ticker").addEventListener("scroll", () => {
  const box = $("ticker");
  state.follow = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  $("btnFollow").textContent = state.follow ? "跟随最新" : "返回最新字幕";
});
$("btnFollow").onclick = () => {
  state.follow = true;
  $("ticker").scrollTop = $("ticker").scrollHeight;
};
function renderScreenStatus(s) {
  state.screen = s;
  const age = s.last_success_ts ? Date.now() / 1000 - s.last_success_ts : Infinity;
  const live = state.running && state.phase !== "stopping";
  if (s.preview && $("screenPreview").getAttribute("src") !== s.preview) {
    $("screenPreview").src = s.preview;
    $("screenPreview").hidden = false;
    $("screenEmpty").hidden = true;
  }
  let text = live ? "等待屏幕首帧…" : "屏幕采集未运行";
  if (s.last_success_ts) {
    const coverage = s.oldest_ts == null ? 0 : Math.min(300, Math.max(0, s.last_success_ts - s.oldest_ts));
    text = `${live ? "录屏中" : "采集已停止"} · 回溯 ${Math.floor(coverage)} 秒 · ${((s.buffer_bytes || 0) / 1048576).toFixed(1)} MB`;
    if (live && age > 5) text = "屏幕采集未更新，请检查录屏状态 · " + text;
  }
  if (s.error) text = "屏幕采集异常：" + s.error;
  if (s.limited) text += " · 已触及内存上限，较早画面可能已淘汰";
  $("screenStatus").textContent = text;
}

/* ---------- 运行状态 ---------- */
function setRunning(running, startedAt) {
  state.running = running;
  if (startedAt) state.startedAt = startedAt;
  if (running) showMeetingConsole(true);
  const pill = $("statusPill");
  pill.textContent = running ? ({loading: "● 正在准备", ready: "● 会议中", stopping: "● 正在收尾"}[state.phase] || "● 正在准备") : "● 待机";
  pill.classList.toggle("live", running);
  $("recLabel").textContent = running ? (state.phase === "stopping" ? "正在收尾" : "结束会议") : "开始会议";
  $("btnRec").disabled = state.starting || (running && state.phase === "stopping");
  $("btnVar").disabled = !running || state.phase === "stopping";
  $("interestFocus").disabled = running;
  $("btnRec").classList.toggle("live", running);
  if (!running) {
    $("statTime").textContent = "--";
  }
}
setInterval(() => {
  if (state.running && state.startedAt) {
    const el = Date.now() / 1000 - state.startedAt;
    $("statTime").textContent = "本场 " + fmtClock(el);
    if (state.lastCaptionAt && Date.now() - state.lastCaptionAt > 45000 && state.phase === "ready") {
      $("asrStatus").textContent = `距上一条字幕 ${Math.floor((Date.now() - state.lastCaptionAt) / 1000)} 秒（可能是静音，也可能识别落后）`;
    }
    renderScreenStatus(state.screen);
    // 静默看门狗：90 秒没有一句转写 → 检查采集和识别状态
    if (!state.gotLines && !state.silenceHinted && el > 90) {
      state.silenceHinted = true;
      const src = (state.cfg && state.cfg.audio_source) || "both";
      const hint = src === "loopback"
        ? "已 90 秒未听到语音：当前是【仅系统声音】模式——请确认电脑在播放声音（或到设置页改为「系统声音+麦克风」）。"
        : "已 90 秒未听到可识别语音：请确认电脑在播放声音、且麦克风未被系统静音/被其他软件独占。";
      pushTicker("", "系统", hint, "sys");
    }
  }
}, 1000);

/* ---------- 点名弹窗 ---------- */
function maybeShowAsk() {
  if (state.askOpen || state.askQueue.length === 0) return;
  const ev = state.askQueue.shift();
  state.askOpen = true;
  $("askSub").textContent = `检测到新说话人 ${ev.spk}——刚才说：「${ev.sample}」`;
  $("askInput").value = "";
  $("askModal").hidden = false;
  $("askInput").focus();
}
async function answerAsk(text) {
  $("askModal").hidden = true;
  state.askOpen = false;
  await pywebview.api.answer_prompt(text);
  maybeShowAsk();
}

/* ---------- 主页 ---------- */
$("btnRec").onclick = async () => {
  if (state.starting) return;
  if (state.running) {
    const r = await pywebview.api.stop_meeting();
    if (!r.ok) toast("停止失败：" + (r.error || ""));
    else { state.phase = "stopping"; setRunning(true, null); }
    return;
  }
  if (!state.project) { toast("还没有选项目——先去「项目」页选一个文件夹"); switchPage("project"); return; }
  state.starting = true;
  state.hits = 0; state.speakers.clear(); state.ticker = [];
  state.gotLines = false; state.silenceHinted = false; state.follow = true;
  state.captionCount = 0; state.lastCaptionAt = null; state.phase = "loading"; state.screen = {};
  state.partials.clear(); $("partialCaption").hidden = true;
  $("interestStatus").textContent = "正在准备兴趣判断…";
  $("ticker").innerHTML = ""; $("statHits").textContent = "命中 0";
  $("captionCount").textContent = "0 句"; $("statSpeakers").textContent = "说话人 —";
  $("screenPreview").hidden = true; $("screenPreview").removeAttribute("src");
  $("screenEmpty").hidden = false; $("screenEmpty").textContent = "等待屏幕首帧…";
  $("captureStatus").textContent = "正在准备会议…";
  $("btnRec").disabled = true;
  let r;
  try {
    await pywebview.api.set_config({interest_focus: $("interestFocus").value.trim()});
    r = await pywebview.api.start_meeting(state.project, "go");
  }
  catch (e) { r = {ok: false, error: String(e)}; }
  finally { state.starting = false; $("btnRec").disabled = false; }
  if (!r.ok) { state.phase = "idle"; $("captureStatus").textContent = "启动失败"; toast("启动失败：" + (r.error || "")); return; }
  setRunning(true, r.started_at);
  const cfg0 = state.cfg || {};
  const streaming = cfg0.asr_engine === "xfyun_rtasr";
  pushTicker("", "系统", streaming ? "正在连接讯飞RTASR，连接成功后持续显示临时字幕和定稿。" : "正在准备分块语音识别…", "sys");
  const src = cfg0.audio_source || "both";
  pushTicker("", "系统", `监听源：${SOURCE_LABEL[src] || src}。专业词只用于纠错；自动取证由API判断，或由你手动VAR。`, "sys");
  toast("会议正在准备：" + state.project);
};
$("btnVar").onclick = async () => {
  const r = await pywebview.api.manual_var();
  toast(r.ok ? "已标记（手动 VAR：回溯 300s）" : "会议未在运行");
};
$("btnOpenOut").onclick = () => pywebview.api.open_data_dir();

/* ---------- 项目页（VSCode 式文件树 + 右侧摘要） ---------- */
const FILE_GLYPHS = {
  png: "🖼", jpg: "🖼", jpeg: "🖼", gif: "🖼", webp: "🖼", bmp: "🖼",
  md: "📄", txt: "📄", docx: "📕", doc: "📕", pdf: "📕",
  pptx: "📊", ppt: "📊", xlsx: "📊", csv: "📊",
  py: "⚙", js: "⚙", ts: "⚙", html: "⚙", css: "⚙", json: "⚙",
  zip: "🗜", rar: "🗜", "7z": "🗜",
};
function fileGlyph(name) {
  const ext = (name.split(".").pop() || "").toLowerCase();
  return FILE_GLYPHS[ext] || "📄";
}
function fmtSize(n) {
  if (n == null) return "";
  if (n < 1024) return n + " B";
  if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
  return (n / 1048576).toFixed(1) + " MB";
}
function renderTree(nodes, box, depth) {
  for (const n of nodes || []) {
    const row = document.createElement("div");
    row.className = "tree-row" + (n.dir ? " tree-dir" : "");
    row.style.paddingLeft = (6 + depth * 14) + "px";
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    row.innerHTML =
      `<span class="tree-ico">${n.dir ? "📁" : fileGlyph(n.name)}</span>` +
      `<span class="tree-name">${esc(n.name)}</span>` +
      (!n.dir ? `<span class="tree-size">${fmtSize(n.size)}</span>` : "");
    box.appendChild(row);
    if (n.dir) {
      const kids = document.createElement("div");
      kids.hidden = depth >= 1; // 顶层目录默认展开，更深层折叠
      renderTree(n.children || [], kids, depth + 1);
      row.onclick = () => { kids.hidden = !kids.hidden; row.classList.toggle("collapsed", kids.hidden); };
      row.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); row.click(); } };
      box.appendChild(kids);
    } else {
      row.classList.add("tree-file");
      row.title = "点击预览：" + (n.rel || n.name);
      row.onclick = () => openFilePreview(n.rel || n.name);
      row.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); row.click(); } };
    }
  }
}
async function openFilePreview(rel) {
  if (!state.project || !rel) return;
  const box = $("fileViewerBody");
  $("fileViewerName").textContent = rel.split("/").pop() || rel;
  $("fileViewerMeta").textContent = "读取中…";
  $("fileViewer").hidden = false;
  box.innerHTML = '<div class="preview-note">读取文件…</div>';
  state.previewRel = rel;
  try {
    const r = await pywebview.api.read_project_file(state.project, rel);
    if (!r || !r.ok) {
      box.innerHTML = `<div class="file-preview-note">无法预览：${esc((r && r.error) || "未知错误")}</div>`;
      $("fileViewerMeta").textContent = "预览失败";
      return;
    }
    $("fileViewerMeta").textContent = `${fmtSize(r.size)} · ${r.language || r.kind || ""}`;
    if (r.kind === "image") {
      box.innerHTML = r.data_url
        ? `<div class="file-image-wrap"><img src="${r.data_url}" alt="${esc(r.name)}"></div>`
        : '<div class="file-preview-note">图片过大，未内嵌预览；请用系统应用打开。</div>';
      return;
    }
    if (r.kind === "video" || r.kind === "audio") {
      if (r.data_url) {
        box.innerHTML = r.kind === "video"
          ? `<video controls preload="metadata" src="${r.data_url}"></video>`
          : `<audio controls preload="metadata" src="${r.data_url}"></audio>`;
      } else {
        box.innerHTML = '<div class="file-preview-note">媒体文件较大，未内嵌预览；请用系统应用打开。</div>';
      }
      return;
    }
    if (r.kind === "binary") {
      box.innerHTML = '<div class="file-preview-note">这是二进制文件，文本查看器无法显示；请用系统应用打开。</div>';
      return;
    }
    const text = r.text || "";
    if (!text.trim()) {
      box.innerHTML = '<div class="file-preview-note">没有可提取的文本（可能是扫描件/图片型 PDF/空文件）；请用系统应用打开。</div>';
      return;
    }
    const lines = text.split(String.fromCharCode(10)).map((x) => x.endsWith(String.fromCharCode(13)) ? x.slice(0, -1) : x);
    const shown = lines.slice(0, 6000);
    const note = r.truncated || lines.length > shown.length
      ? `仅显示前 ${shown.length} 行 / 200,000 字符，完整内容请用系统应用打开。`
      : (r.kind === "pdf" || r.kind === "office" ? "这是提取的文本预览；完整排版请用系统应用打开。" : "");
    const sep = String.fromCharCode(10);
    const nums = shown.map((_, i) => i + 1).join(sep);
    box.innerHTML = (note ? `<div class="preview-note">${esc(note)}</div>` : "") +
      `<div class="code-wrap"><pre class="code-nums">${nums}</pre><pre class="code-text">${esc(shown.join(sep))}</pre></div>`;
  } catch (e) {
    box.innerHTML = `<div class="file-preview-note">预览失败：${esc(String(e))}</div>`;
  }
}
async function scanProject(path) {
  state.project = path;
  resetUnderstand();  // 换项目：清空全库理解卡片（结果属于旧项目）
  $("projPath").textContent = path;
  $("homeProject").textContent = path;
  $("rpProjName").textContent = path;
  $("scanMeta").textContent = "读取中…（大文件夹约需数秒到半分钟）";
  $("scanTerms").innerHTML = "";
  $("sideTree").innerHTML = "";
  $("treeStat").textContent = "";
  $("btnUseProject").disabled = true;
  const d = await pywebview.api.scan_project(path);
  state.scan = d;
  if (!d.ok) {
    $("scanMeta").textContent = "读取失败：" + (d.error || "");
    $("sideTree").innerHTML = `<div class="muted" style="padding:4px 8px">读取失败：${esc(d.error || "")}</div>`;
    return;
  }
  renderTree(d.tree || [], $("sideTree"), 0);
  $("treeStat").textContent = "（" + (d.doc_count || 0) + " 份文档）";
  let meta = `文档 ${d.doc_count} 份`;
  if (d.skipped && d.skipped.length) {
    const head = d.skipped.slice(0, 3).map(esc).join("、");
    meta += ` · 跳过 ${d.skipped.length} 个非文本文件（${head}${d.skipped.length > 3 ? " 等" : ""}）`;
  }
  $("scanMeta").textContent = meta;
  const chips = (obj, n = 20) => Object.keys(obj).slice(0, n)
    .map((w) => `<span class="chip"><b>${esc(w)}</b></span>`).join("");
  let html = "";
  if (d.repo && d.repo.is_repo && Object.keys(d.repo.top || {}).length) html += `<h3>代码符号（L0）</h3><div class="chips">${chips(d.repo.top)}</div>`;
  if (d.persons && d.persons.length) html += `<h3>人物</h3><div class="chips">${d.persons.map((p) => `<span class="chip">${esc(p)}</span>`).join("")}</div>`;
  if (d.terms && Object.keys(d.terms).length) {
    // 原始 L1 词表默认折叠：实测在小说/杂文档上产出的多是 A4/AM5/碎片这类噪音，
    // 真正该看的候选词走下面的「全库理解」。这里只留调试入口，不再抢视线。
    html += `<details class="rawterms"><summary class="muted">原始抽取（未治理，仅供调试）· ${d.term_count} 个词</summary>` +
      `<div class="chips">${chips(d.terms, 40)}</div></details>`;
  }
  if (d.notes && d.notes.length) html += `<div class="muted">${d.notes.map(esc).join("；")}</div>`;
  if (!html) html = `<div class="muted">没有可从文本抽取的内容（图片/压缩包不会被解析）</div>`;
  $("scanTerms").innerHTML = html;
  $("btnUseProject").disabled = false;
  await pywebview.api.set_config({ last_project: path });
  updateComposer();
  await renderSidebar();
  await loadConversations();
  newChat();
}
function updateComposer() {
  const cfg = state.cfg || {};
  $("homeProject").textContent = state.project || "选择项目";
  $("composerModel").textContent = cfg.llm_provider === "custom"
    ? (cfg.llm_model || "自定义 API") : "本地模型";
  $("composerMode").textContent = "只读 Agent";
  $("aboutVersion").textContent = $("ver").textContent || "—";
}
function chatTime(ts) {
  if (!ts) return "";
  const d = new Date(Number(ts) * (Number(ts) < 1e12 ? 1000 : 1));
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
function chatTools(msg) {
  const obs = msg.observations || [];
  if (!obs.length) return "";
  return `<details class="chat-tools"><summary>工具轨迹 · ${obs.length} 次</summary>` +
    obs.map((o, i) => `<div class="chat-tool-row"><span class="n">${i + 1}</span><span class="t">${agentEsc(o.tool || "")}</span><span class="muted">${agentEsc(JSON.stringify(o.arguments || {}).slice(0, 220))}</span></div>`).join("") +
    `</details>`;
}
function chatCitations(msg) {
  const cites = msg.citations || [];
  if (!cites.length) return "";
  const parts = cites.map((c) => {
    if (c.kind === "turn") return `${agentEsc(c.session_id || "")}:${agentEsc((c.turn_ids || []).join(","))}`;
    return agentEsc(c.name || "");
  });
  const stateText = msg.mode === "agent"
    ? (msg.grounded ? "引用已校验" : "引用未通过校验")
    : "";
  return `<div class="chat-cites">${stateText ? stateText + " · " : ""}依据：${parts.map((p) => `<span class="cite">${p}</span>`).join("")}</div>`;
}
function chatActions(msg) {
  const actions = msg.actions || [];
  if (!actions.length) return null;
  const wrap = document.createElement("div");
  wrap.className = "chat-actions";
  for (const action of actions) {
    const card = document.createElement("div");
    card.className = "chat-action-card";
    const pending = !action.status || action.status === "pending";
    card.innerHTML = `<span class="action-copy"><b>${agentEsc(action.label || "待确认操作")}</b><span class="muted">${pending ? "需要你确认后执行" : "已处理"}</span></span>`;
    const btn = document.createElement("button");
    btn.className = "btn btn-primary chat-action-btn";
    btn.textContent = pending ? "确认" : "已处理";
    btn.disabled = !pending;
    btn.onclick = () => executeChatAction(action.id, btn);
    card.appendChild(btn);
    wrap.appendChild(card);
  }
  return wrap;
}
function chatNode(msg) {
  const row = document.createElement("div");
  row.className = "chat-msg " + (msg.role === "user" ? "user" : "assistant");
  const avatar = document.createElement("div");
  avatar.className = "chat-avatar";
  avatar.textContent = msg.role === "user" ? "你" : "M";
  const body = document.createElement("div");
  body.className = "chat-body";
  const bubble = document.createElement("div");
  bubble.className = "chat-bubble";
  bubble.textContent = msg.content || "";
  body.appendChild(bubble);
  if (msg.role === "assistant") {
    const meta = document.createElement("div");
    meta.className = "chat-meta";
    const bits = [];
    if (msg.mode === "agent") bits.push("Agent", `${msg.observations ? msg.observations.length : 0} 次工具`);
    if (msg.stop_reason) bits.push(msg.stop_reason);
    if (msg.ts) bits.push(chatTime(msg.ts));
    meta.textContent = bits.join(" · ");
    if (meta.textContent) body.appendChild(meta);
    const tools = chatTools(msg);
    if (tools) body.insertAdjacentHTML("beforeend", tools);
    const cites = chatCitations(msg);
    if (cites) body.insertAdjacentHTML("beforeend", cites);
    const actions = chatActions(msg);
    if (actions) body.appendChild(actions);
  }
  row.appendChild(avatar);
  row.appendChild(body);
  return row;
}
function appendChatMessage(msg, scroll = true) {
  const thread = $("chatThread");
  thread.appendChild(chatNode(msg));
  if (scroll) $("chatScroll").scrollTop = $("chatScroll").scrollHeight;
}
function setChatActive(active) {
  $("homeHero").hidden = !!active;
  $("chatThread").hidden = !active;
}
function newChat() {
  state.chatId = "";
  state.chatMessages = [];
  $("chatThread").innerHTML = "";
  $("chatThread").hidden = true;
  $("homeHero").hidden = false;
  $("chatState").textContent = "新对话";
  $("pageTitle").textContent = "新任务";
  $("homeAsk").value = "";
  $("homeAsk").focus();
}
async function ensureChat() {
  if (state.chatId) return state.chatId;
  if (!state.project) throw new Error("先选择项目");
  const r = await pywebview.api.chat_new(state.project);
  if (!r || !r.ok) throw new Error((r && r.error) || "无法创建对话");
  state.chatId = r.conversation_id;
  return state.chatId;
}
function appendTyping() {
  const thread = $("chatThread");
  const row = document.createElement("div");
  row.className = "chat-msg assistant";
  row.id = "chatTyping";
  row.innerHTML = `<div class="chat-avatar">M</div><div class="chat-body"><span class="chat-typing"><i></i><i></i><i></i> 正在处理…</span></div>`;
  thread.appendChild(row);
  $("chatScroll").scrollTop = $("chatScroll").scrollHeight;
}
async function sendChat(text) {
  const message = String(text || "").trim();
  if (!message) return;
  if (!state.project) { toast("先选择项目"); switchPage("project"); return; }
  if (state.chatBusy) return;
  state.chatBusy = true;
  $("btnComposerSend").disabled = true;
  $("homeAsk").value = "";
  const userMsg = { role: "user", content: message, ts: Date.now() / 1000 };
  state.chatMessages.push(userMsg);
  setChatActive(true);
  appendChatMessage(userMsg);
  appendTyping();
  try {
    const cid = await ensureChat();
    const r = await pywebview.api.chat_send(state.project, cid, message);
    const typing = $("chatTyping");
    if (typing) typing.remove();
    if (!r || !r.ok || !r.message) {
      const errorMsg = { role: "assistant", content: "这次没有完成：" + ((r && r.error) || "未知错误"), ts: Date.now() / 1000, mode: "error" };
      state.chatMessages.push(errorMsg);
      appendChatMessage(errorMsg);
    } else {
      state.chatId = r.conversation_id || cid;
      state.chatMessages.push(r.message);
      appendChatMessage(r.message);
      $("chatState").textContent = r.title || "对话";
      $("pageTitle").textContent = r.title || "新任务";
    }
    await loadConversations();
  } catch (e) {
    const typing = $("chatTyping");
    if (typing) typing.remove();
    const errorMsg = { role: "assistant", content: "调用失败：" + (e && e.message ? e.message : e), ts: Date.now() / 1000, mode: "error" };
    state.chatMessages.push(errorMsg);
    appendChatMessage(errorMsg);
  } finally {
    state.chatBusy = false;
    $("btnComposerSend").disabled = false;
    $("chatScroll").scrollTop = $("chatScroll").scrollHeight;
  }
}
async function executeChatAction(actionId, button) {
  if (!state.chatId) { toast("对话尚未保存"); return; }
  button.disabled = true;
  button.textContent = "执行中…";
  try {
    const r = await pywebview.api.chat_action(state.project, state.chatId, actionId);
    if (!r || !r.ok) {
      button.disabled = false;
      button.textContent = "重试";
      toast("操作失败：" + ((r && r.error) || "未知错误"), 9000);
      return;
    }
    if (r.ui_only) {
      if (r.action === "start_meeting") {
        if (state.running) toast("会议已经在进行中");
        else {
          switchPage("home", false);
          openMeetingConsole();
          $("btnRec").click();
        }
      } else if (r.action === "stop_meeting") {
        if (state.running) $("btnRec").click();
        else toast("会议未在运行");
      } else if (r.action === "start_project_reading") {
        const plan = await pywebview.api.understand_plan(state.project);
        if (!plan || !plan.ok) {
          toast("全库理解无法开始：" + ((plan && plan.error) || "未知错误"), 9000);
        } else {
          const started = await pywebview.api.understand_start(state.project, 0);
          toast(started && started.ok ? "已在后台开始全库理解" : "全库理解启动失败", 9000);
        }
      }
    }
    if (r.message) {
      state.chatMessages.push(r.message);
      appendChatMessage(r.message);
    }
    button.textContent = "已处理";
    button.disabled = true;
    await loadConversations();
  } catch (e) {
    button.disabled = false;
    button.textContent = "重试";
    toast("操作失败：" + e, 9000);
  }
}
async function loadConversations() {
  const box = $("sideSessions");
  if (!box) return;
  if (!state.project) { box.innerHTML = '<div class="side-empty">选择项目后显示对话</div>'; return; }
  try {
    const r = await pywebview.api.chat_list(state.project);
    const rows = (r && r.ok ? r.conversations : []) || [];
    box.innerHTML = rows.length ? rows.slice(0, 12).map((c) =>
      `<button class="side-session" data-conversation="${esc(c.id)}" title="${esc(c.title)}">
        <span class="s-ico">◈</span><span class="s-name">${esc(c.title)}</span><span class="s-time">${relTime(Date.parse(c.updated_at || "") / 1000)}</span></button>`
    ).join("") : '<div class="side-empty">还没有对话</div>';
    box.querySelectorAll(".side-session").forEach((row) => {
      row.onclick = () => openConversation(row.dataset.conversation);
    });
  } catch (e) { box.innerHTML = '<div class="side-empty">对话暂不可用</div>'; }
}
async function openConversation(id) {
  if (!id || state.chatBusy) return;
  try {
    const r = await pywebview.api.chat_load(state.project, id);
    if (!r || !r.ok) { toast((r && r.error) || "对话读取失败"); return; }
    state.chatId = r.conversation.id;
    state.chatMessages = r.conversation.messages || [];
    $("chatThread").innerHTML = "";
    setChatActive(state.chatMessages.length > 0);
    for (const msg of state.chatMessages) appendChatMessage(msg, false);
    switchPage("home", false);
    $("chatState").textContent = r.conversation.title || "对话";
    $("pageTitle").textContent = r.conversation.title || "新任务";
    $("chatScroll").scrollTop = $("chatScroll").scrollHeight;
  } catch (e) { toast("对话读取失败：" + e); }
}
function showSkills() {
  switchPage("home", false);
  $("pageTitle").textContent = "技能";
  setChatActive(true);
  const row = document.createElement("div");
  row.className = "chat-msg assistant skill-message";
  row.innerHTML = `<div class="chat-avatar">M</div><div class="chat-body"><div class="chat-bubble">
    <b>会议技能</b><div class="muted" style="margin-top:4px">选一个，Agent 会调用会议工具并给出引用。</div>
    <div class="skill-grid">
      <button class="skill-btn" data-prompt="列出最近的会议，并总结每场的主要决定、行动项和未决问题。">最近会议总结</button>
      <button class="skill-btn" data-prompt="查看当前项目的进展、已保存事实和会前关注，告诉我现在最需要确认什么。">项目进度</button>
      <button class="skill-btn" data-prompt="计算当前项目全库阅读的字数、块数和预计耗时，并说明是否值得现在跑。">全库阅读预算</button>
      <button class="skill-btn" data-prompt="在最近会议里搜索关于交付时间的原话，并给出引用。">找原话</button>
      <button class="skill-btn" data-prompt="列出当前项目记忆里所有未决问题和风险，并给出来源。">未决问题</button>
    </div></div></div>`;
  $("chatThread").appendChild(row);
  row.querySelectorAll(".skill-btn").forEach((btn) => {
    btn.onclick = () => { row.remove(); sendChat(btn.dataset.prompt); };
  });
  $("chatScroll").scrollTop = $("chatScroll").scrollHeight;
}

async function renderSidebar() {
  const cfg = state.cfg || {};
  const projects = (cfg.projects || []).filter((p, i, arr) => p && arr.indexOf(p) === i);
  const box = $("sideProjects");
  if (box) {
    box.innerHTML = projects.length ? projects.map((p) => {
      const name = p.split(/[\\/]/).filter(Boolean).pop() || p;
      return `<button class="side-project${p === state.project ? " current" : ""}" data-project="${esc(p)}" title="${esc(p)}">
        <span class="p-ico">▣</span><span class="p-name">${esc(name)}</span><span class="p-time">${p === state.project ? "当前" : ""}</span></button>`;
    }).join("") : '<div class="side-empty">还没有项目</div>';
    box.querySelectorAll(".side-project").forEach((row) => {
      row.onclick = () => { scanProject(row.dataset.project); switchPage("home"); };
    });
  }
  await loadConversations();
}

$("btnPick").onclick = async () => {
  const p = await pywebview.api.pick_folder();
  if (p) scanProject(p);
};
$("btnScanInput").onclick = () => {
  const p = $("projManual").value.trim();
  if (p) scanProject(p);
};
$("btnUseProject").onclick = () => { switchPage("home"); toast("已就绪——回主页按「开始会议」"); };

/* ---------- 会议库（按项目分组：项目 → 场次 → 详情） ---------- */
function filterLibrary() {
  const q = ($("librarySearch")?.value || "").trim().toLowerCase();
  document.querySelectorAll("#libGroups .lib-group").forEach((group) => {
    const text = group.textContent.toLowerCase();
    const groupMatch = !q || text.includes(q);
    group.hidden = !groupMatch;
    group.querySelectorAll(".sess-row").forEach((row) => {
      row.hidden = !!q && !groupMatch && !row.textContent.toLowerCase().includes(q);
    });
  });
}
async function refreshLibrary() {
  const groups = await pywebview.api.list_library();
  state.libraryGroups = groups || [];
  $("sessionDetail").hidden = true;
  const box = $("libGroups");
  if (!groups.length) { box.innerHTML = `<div class="muted">还没有会议记录（零命中会话按设计不留任何文件）</div>`; renderSidebar(); return; }
  box.innerHTML = "";
  for (const g of groups) {
    const wrap = document.createElement("div");
    wrap.className = "lib-group";
    const head = document.createElement("div");
    head.className = "lib-proj";
    head.innerHTML =
      `<span class="tree-ico">${g.project ? "📁" : "❔"}</span>` +
      `<span class="lib-name">${esc(g.name)}</span>` +
      `<span class="lib-path muted">${esc(g.project || "")}</span>` +
      `<span class="lib-meta muted">${g.sessions.length} 场 · ${relTime(g.latest)}</span>`;
    const body = document.createElement("div");
    body.className = "lib-sessions";
    body.hidden = true;
    for (const s of g.sessions) {
      const row = document.createElement("div");
      row.className = "sess-row";
      row.innerHTML =
        `<span class="sid">${esc(s.sid)}</span>` +
        `<span class="meta muted">${new Date(s.mtime * 1000).toLocaleString()} · ${relTime(s.mtime)} · ` +
        `${s.clips != null ? s.clips + " 片段" : ""} ${s.minutes ? " · 有纪要" : ""}</span>`;
      row.onclick = (e) => { e.stopPropagation(); showSession(s.sid); };
      body.appendChild(row);
    }
    head.onclick = () => { body.hidden = !body.hidden; head.classList.toggle("open", !body.hidden); };
    wrap.appendChild(head);
    wrap.appendChild(body);
    box.appendChild(wrap);
  }
  filterLibrary();
  renderSidebar();
}
async function showSession(sid) {
  const d = await pywebview.api.session_detail(sid);
  $("sessionDetail").querySelectorAll("audio, video").forEach((el) => el.pause());
  const clips = d.clips.map((c, index) => `
    <div class="clip" data-clip-index="${index}">
      <div class="head"><span class="layer">${esc(c.layer)}</span><span>${esc(c.evidence)}</span>
        <span class="muted">@${fmtClock(c.at)} · ${c.lines} 句</span></div>
      ${c.video_url ? `<video controls preload="metadata" src="${esc(c.video_url)}"></video>` : `
        ${(c.frames || []).length ? `<img class="replay-screen" alt="与音频同步的屏幕画面" hidden><div class="replay-note muted"></div>` : '<div class="muted">此片段没有可回看的屏幕画面</div>'}
        ${c.audio_url ? `<audio controls preload="metadata" src="${esc(c.audio_url)}"></audio>` : ""}`}
      ${c.hd_url ? `<details><summary>旧版命中后补录（独立时间段，无音轨）</summary><video controls preload="none" src="${esc(c.hd_url)}"></video></details>` : ""}
    </div>`).join("");
  $("sessionDetail").innerHTML =
    `<h2>${esc(sid)}</h2>` +
    `<div class="md">${d.minutes ? mdToHtml(d.minutes) : "<p class='muted'>无薄纪要文件</p>"}</div>` +
    (clips ? `<h2>证据片段</h2>${clips}` : "") +
    `<details><summary class="muted">厚纪要（VAR 报告原文）</summary><div class="md">${d.reports ? mdToHtml(d.reports) : "<p class='muted'>无</p>"}</div></details>`;
  $("sessionDetail").hidden = false;
  $("sessionDetail").querySelectorAll(".clip").forEach((box) => {
    const c = d.clips[Number(box.dataset.clipIndex)];
    const audio = box.querySelector("audio"), image = box.querySelector(".replay-screen");
    if (!audio || !image) return;
    const frames = c.frames || [];
    const update = () => {
      const ts = (Number(c.start_ts) || 0) + audio.currentTime;
      let lo = 0, hi = frames.length;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (frames[mid].ts <= ts) lo = mid + 1; else hi = mid; }
      const frame = frames[lo - 1];
      image.hidden = !frame;
      box.querySelector(".replay-note").textContent = frame ? `屏幕与音频同步 · ${fmtClock(ts)}` : "此时间段缺少屏幕画面，音频仍可播放";
      if (frame && image.getAttribute("src") !== frame.url) image.src = frame.url;
    };
    ["timeupdate", "seeking", "loadedmetadata"].forEach((event) => audio.addEventListener(event, update));
    update();
  });
  $("sessionDetail").scrollIntoView({ behavior: "smooth" });
}

/* ---------- Agent 页 ---------- */
const AGENT_TYPE_LABEL = {
  "action-item": "行动项", decision: "决策", commitment: "承诺",
  risk: "风险", "open-question": "未决问题", "project-fact": "项目事实",
  preference: "偏好",
};
function agentType(t) { return AGENT_TYPE_LABEL[t] || t || "事实"; }
function agentEsc(s) { return esc(s == null ? "" : String(s)); }
function setAgentBusy(busy, text) {
  state.agentBusy = busy;
  const page = $("page-agent");
  if (page) page.classList.toggle("agent-busy", busy);
  if (text) $("agentStatusLine").textContent = text;
  ["btnFocusGen", "btnFocusSave", "btnFactsPreview", "btnFactsSave", "btnAsk", "btnAgentRun", "btnMemory"]
    .forEach((id) => { const el = $(id); if (el) el.disabled = busy || (id.endsWith("Save") && !((id === "btnFocusSave" ? state.agentFocus.length : state.agentFacts.length))); });
}
function renderAgentStatus(st) {
  if (!st || !st.ok) { $("agentStatusLine").textContent = "Agent 状态不可用：" + ((st && st.error) || "未知错误"); return; }
  const counts = st.by_type || {};
  const dist = Object.keys(counts).map((k) => `${agentType(k)} ${counts[k]}`).join(" · ");
  $("agentStatusLine").textContent = `项目记忆 ${st.facts || 0} 条${dist ? "（" + dist + "）" : ""} · 会前关注 ${(st.focus || []).length} 项`;
}
function renderFocus(items, sources) {
  state.agentFocus = Array.isArray(items) ? items : [];
  state.agentFocusSources = Array.isArray(sources) ? sources : state.agentFocusSources;
  const box = $("focusList");
  if (!state.agentFocus.length) { box.className = "agent-list muted"; box.textContent = "暂无关注清单，点「生成关注清单」让 Agent 阅读项目材料。"; $("btnFocusSave").disabled = true; return; }
  box.className = "agent-list";
  box.innerHTML = state.agentFocus.map((item, i) =>
    `<div class="agent-item"><label><input type="checkbox" class="agent-pick agent-focus-pick" data-index="${i}" checked>
      <span><span class="title">${agentEsc(item.title)}</span>
      <span class="meta">${agentEsc(item.why || "")}</span></span></label></div>`).join("");
  $("btnFocusSave").disabled = state.agentBusy;
}
function renderSessions(sessions) {
  const select = $("agentSession");
  const current = select.value;
  const available = (sessions || []).filter((s) => s.has_transcript);
  select.innerHTML = '<option value="">选择有转写的会话</option>' + available.map((s) => {
    const when = s.mtime ? new Date(s.mtime * 1000).toLocaleString() : "";
    return `<option value="${agentEsc(s.sid)}">${agentEsc(s.sid)} · ${when}${s.clips != null ? " · " + s.clips + " 片段" : ""}</option>`;
  }).join("");
  if (current && [...select.options].some((o) => o.value === current)) select.value = current;
}
function renderFacts(items) {
  state.agentFacts = Array.isArray(items) ? items : [];
  const box = $("factsList");
  if (!state.agentFacts.length) { box.className = "agent-list muted"; box.textContent = "没有可写入的会议事实（可能没有明确事件或模型未配置）。"; $("btnFactsSave").disabled = true; return; }
  box.className = "agent-list";
  box.innerHTML = state.agentFacts.map((item, i) => {
    const bits = [item.owner ? "负责人 " + item.owner : "", item.due ? "截止 " + item.due : "",
                  item.severity ? "严重度 " + item.severity : "",
                  item.confidence != null ? "置信 " + Number(item.confidence).toFixed(2) : ""].filter(Boolean).join(" · ");
    return `<div class="agent-item"><label><input type="checkbox" class="agent-pick agent-fact-pick" data-index="${i}" checked>
      <span><span class="agent-type">${agentEsc(agentType(item.fact_type))}</span><span class="title">${agentEsc(item.title)}</span>
      ${bits ? `<span class="meta">${agentEsc(bits)}</span>` : ""}
      ${item.evidence_text ? `<span class="evidence">${agentEsc(item.evidence_text.slice(0, 900))}</span>` : ""}</span></label></div>`;
  }).join("");
  $("btnFactsSave").disabled = state.agentBusy;
}
function renderMemory(facts) {
  const box = $("agentMemory");
  if (!facts || !facts.length) { box.className = "agent-list muted"; box.textContent = "暂无项目记忆"; return; }
  box.className = "agent-list";
  box.innerHTML = facts.map((f) =>
    `<div class="agent-item"><span class="agent-type">${agentEsc(agentType(f.type))}</span><span class="title">${agentEsc(f.name)}</span>
     <div class="meta">${agentEsc(f.description || "")}${f.origin_meeting ? " · " + agentEsc(f.origin_meeting) : ""}</div></div>`).join("");
}
async function loadAgentPage() {
  const project = state.project || "";
  $("agentProject").textContent = project || "（未选择项目——先去「项目」页选择）";
  if (!project) { $("agentStatusLine").textContent = "需要先选择项目"; renderFocus([]); renderFacts([]); renderMemory([]); return; }
  setAgentBusy(true, "加载项目记忆与关注清单…");
  try {
    const st = await pywebview.api.agent_status(project);
    renderAgentStatus(st);
    renderFocus(st && st.ok ? st.focus : [], st && st.ok ? st.focus_sources : []);
    const sessions = await pywebview.api.list_sessions();
    renderSessions(sessions);
    const mem = await pywebview.api.agent_memory(project, "");
    renderMemory(mem && mem.ok ? mem.facts : []);
  } catch (e) {
    $("agentStatusLine").textContent = "当前安装版本不支持 Agent 页面，请重新安装新版。";
  } finally { setAgentBusy(false); }
}
$("btnFocusGen").onclick = async () => {
  if (!state.project) { toast("先选择项目"); switchPage("project"); return; }
  if (state.agentBusy) return;
  setAgentBusy(true, "Agent 正在阅读项目材料并生成关注清单…");
  try {
    const r = await pywebview.api.agent_focus_preview(state.project);
    if (!r || !r.ok) { $("agentStatusLine").textContent = "关注清单生成失败"; toast("生成失败：" + ((r && r.error) || "未知错误"), 9000); return; }
    renderFocus(r.items || [], r.sources || []);
    $("agentStatusLine").textContent = `已生成 ${(r.items || []).length} 条关注点，勾选后保存`;
    toast("已生成 " + (r.items || []).length + " 条关注点，勾选后保存");
  } finally { setAgentBusy(false); }
};
$("btnFocusSave").onclick = async () => {
  const picked = [...document.querySelectorAll(".agent-focus-pick:checked")].map((c) => state.agentFocus[Number(c.dataset.index)]).filter(Boolean);
  if (!picked.length) { toast("没有勾选关注点"); return; }
  setAgentBusy(true, "保存会前关注清单…");
  try {
    const r = await pywebview.api.agent_focus_save(state.project, picked, state.agentFocusSources || []);
    if (!r || !r.ok) { toast("保存失败：" + ((r && r.error) || "未知错误"), 9000); return; }
    toast("关注清单已保存；下次会议的项目上下文会采用");
    const st = await pywebview.api.agent_status(state.project); renderAgentStatus(st);
  } finally { setAgentBusy(false); }
};
$("btnFactsPreview").onclick = async () => {
  const session = $("agentSession").value;
  if (!session) { toast("先选择一个已保存会话"); return; }
  if (!state.project) { toast("先选择项目"); return; }
  setAgentBusy(true, "Agent 正在提取并核对会议事实…");
  try {
    const r = await pywebview.api.agent_extract_preview(session, state.project);
    if (!r || !r.ok) { $("agentStatusLine").textContent = "事实提取失败"; toast("提取失败：" + ((r && r.error) || "未知错误"), 9000); return; }
    renderFacts(r.items || []);
    $("agentStatusLine").textContent = `候选 ${r.candidates || 0} 条 · 核对通过 ${r.verified || 0} 条 · 勾选后写入`;
    toast(`候选 ${r.candidates || 0} 条，核对通过 ${r.verified || 0} 条；勾选后写入`);
  } finally { setAgentBusy(false); }
};
$("btnFactsSave").onclick = async () => {
  const session = $("agentSession").value;
  const picked = [...document.querySelectorAll(".agent-fact-pick:checked")].map((c) => state.agentFacts[Number(c.dataset.index)]).filter(Boolean);
  if (!session) { toast("先选择会话"); return; }
  if (!picked.length) { toast("没有勾选事实"); return; }
  setAgentBusy(true, "正在写入项目记忆…");
  try {
    const r = await pywebview.api.agent_facts_save(session, state.project, picked);
    if (!r || !r.ok) { toast("写入失败：" + ((r && r.error) || "未知错误"), 9000); return; }
    toast(`已写入 ${r.written || 0} 条会议事实`);
    const st = await pywebview.api.agent_status(state.project); renderAgentStatus(st);
    const mem = await pywebview.api.agent_memory(state.project, ""); renderMemory(mem && mem.ok ? mem.facts : []);
  } finally { setAgentBusy(false); }
};
$("btnAsk").onclick = async () => {
  const q = $("agentQuestion").value.trim();
  if (!q) { toast("请输入问题"); return; }
  if (!state.project) { toast("先选择项目"); return; }
  setAgentBusy(true, "Agent 正在检索项目记忆并回答…");
  try {
    const r = await pywebview.api.agent_ask(state.project, q);
    const answer = $("agentAnswer");
    answer.hidden = false;
    if (!r || !r.ok) { $("agentStatusLine").textContent = "问答失败"; answer.textContent = "无法回答：" + ((r && r.error) || "未知错误"); return; }
    $("agentStatusLine").textContent = !r.answered ? "项目记忆中没有足够依据" : (r.grounded ? "回答完成（引用已校验）" : "回答完成（未通过引用校验）");
    const cites = (r.sources || []).map((s) => s.name).join("、");
    answer.innerHTML = `<div>${agentEsc(r.answer || "没有回答")}</div>` +
      `<div class="cites">依据：${agentEsc(cites || "无")}${r.answered && !r.grounded ? "（未通过事实引用校验）" : ""}</div>`;
  } finally { setAgentBusy(false); }
};
function renderAgentRun(r) {
  const plan = (r.plan || []).map((step, i) => `${i + 1}. ${step.goal || ""}${step.tool ? " → " + step.tool : ""}`).join(" · ");
  $("agentRunPlan").textContent = plan ? "计划：" + plan : "";
  const obs = r.observations || [];
  const box = $("agentRunSteps");
  box.className = obs.length ? "agent-list" : "agent-list muted";
  box.innerHTML = obs.length ? obs.map((o, i) =>
    `<div class="agent-item"><span class="agent-type">${i + 1}</span><span class="title">${agentEsc(o.tool)}</span>
     <div class="meta">${agentEsc(JSON.stringify(o.arguments || {}).slice(0, 500))}</div></div>`).join("")
    : "没有调用工具";
  const answer = $("agentRunAnswer");
  answer.hidden = false;
  const citations = (r.citations || []).map((c) =>
    c.kind === "turn" ? `${c.session_id}:${(c.turn_ids || []).join(",")}` : c.name).join("、");
  const stateText = !r.answered ? "没有足够依据" :
    r.grounded ? "回答完成（引用已校验）" : "回答完成（引用未通过校验）";
  answer.innerHTML = `<div>${agentEsc(r.answer || "没有回答")}</div>` +
    `<div class="cites">${agentEsc(stateText)}${citations ? " · 依据：" + agentEsc(citations) : ""}</div>`;
}
$("btnAgentRun").onclick = async () => {
  if (!state.project) { toast("先选择项目"); switchPage("project"); return; }
  const q = $("agentRunQuestion").value.trim();
  if (!q) { toast("请输入 Agent 问题"); return; }
  if (state.agentBusy) return;
  setAgentBusy(true, "Agent 正在规划并调用工具…");
  try {
    const r = await pywebview.api.agent_run(state.project, q, 8, "auto");
    if (!r || (!r.ok && !r.answer)) {
      $("agentStatusLine").textContent = "Agent 运行失败";
      toast("Agent 运行失败：" + ((r && (r.error || r.stop_reason)) || "未知错误"), 9000);
      return;
    }
    renderAgentRun(r);
    $("agentStatusLine").textContent =
      `Agent ${r.status}/${r.stop_reason} · ${r.steps || 0} 步 · ${r.tool_calls || 0} 次工具 · ` +
      (r.grounded ? "引用已校验" : "引用未校验");
    toast(r.grounded ? "Agent 完成，引用已校验" : "Agent 完成，但引用未通过校验", 8000);
  } catch (e) {
    $("agentStatusLine").textContent = "Agent 运行失败";
    toast("Agent 运行失败：" + e, 9000);
  } finally { setAgentBusy(false); }
};
$("btnMemory").onclick = async () => {
  if (!state.project) { toast("先选择项目"); return; }
  setAgentBusy(true, "检索项目记忆…");
  try {
    const r = await pywebview.api.agent_memory(state.project, $("agentMemoryQuery").value.trim());
    renderMemory(r && r.ok ? r.facts : []);
    $("agentStatusLine").textContent = "项目记忆已刷新";
  } finally { setAgentBusy(false); }
};

/* ---------- 设置 + LLM 服务 ---------- */
const PROVIDER_LABEL = { local: "本地模型", custom: "自定义 API" };
function applyProviderUi(provider) {
  $("cfgProvider").value = provider;
  $("provLocal").hidden = provider !== "local";
  $("provCustom").hidden = false;
}
function applyAsrUi(engine) {
  $("cfgAsr").value = engine;
  $("xfyunRow").hidden = engine === "local";
  $("xfyunResult").hidden = engine === "local";
  $("captionHelp").textContent = engine === "xfyun_rtasr"
    ? "讯飞RTASR临时字幕持续更新，句尾定稿后进入兴趣判断。"
    : "当前使用分块识别；字幕延迟包含音频块时长与推理耗时。";
}
async function refreshLlm() {
  const st = await pywebview.api.llm_status();
  $("llmStatus").textContent = st.up
    ? `● 运行中（${st.model || "模型已载入"}）`
    : (st.managed ? "○ 启动中…（首次加载约 30-60 秒）" : "○ 未运行");
  const cfg = state.cfg || {};
  const configured = !!(cfg.llm_base_url || "").trim() && !!(cfg.llm_model || "").trim();
  $("llmPill").textContent = cfg.interest_mode === "manual" ? "仅手动保存" : (configured ? "兴趣API 已配置" : "兴趣API 未配置");
  $("llmPill").classList.toggle("ok", configured && cfg.interest_mode !== "manual");
  $("composerModel").textContent = cfg.llm_provider === "custom"
    ? (cfg.llm_model || "自定义 API")
    : (st.up ? "本地 · " + (st.display_model || st.model || "模型已载入") : "本地模型");
  return st;
}
$("btnLlmStart").onclick = async () => {
  const r = await pywebview.api.llm_start();
  if (!r.ok) { toast("启动失败：" + (r.error || "")); return; }
  toast("LLM 服务启动中（首次加载模型约 30-60 秒）");
  let n = 0;
  const t = setInterval(async () => {
    const st = await refreshLlm();
    if (st.up || ++n > 40) { clearInterval(t); if (st.up) toast("LLM 服务已就绪"); }
  }, 3000);
};
$("btnLlmStop").onclick = async () => {
  const r = await pywebview.api.llm_stop();
  if (!r.ok) toast(r.error || "停止失败");
  refreshLlm();
};
$("cfgInterestMode").onchange = async () => {
  await pywebview.api.set_config({ interest_mode: $("cfgInterestMode").value });
  state.cfg = await pywebview.api.get_config();
  refreshLlm();
  toast("保存方式已设置，下次会议生效；不启用热词或相似度自动保存");
};
const SOURCE_LABEL = {
  both: "系统声音 + 麦克风",
  loopback: "仅系统声音",
  mic: "仅麦克风",
};
async function loadConfig() {
  const cfg = await pywebview.api.get_config();
  state.cfg = cfg;
  $("cfgPy").value = cfg.engine_python || "";
  $("cfgRepo").value = cfg.repo_dir || "";
  $("cfgDevice").value = cfg.device || "cuda";
  $("cfgGguf").value = cfg.llm_gguf || "";
  $("cfgInterestMode").value = cfg.interest_mode || "api";
  $("interestFocus").value = cfg.interest_focus || "";
  $("cfgSource").value = cfg.audio_source || "both";
  $("cfgXfyunDir").value = cfg.xfyun_dir || "";
  applyAsrUi(cfg.asr_engine || "xfyun_rtasr");
  $("cfgBaseUrl").value = cfg.llm_base_url || "";
  $("cfgModel").value = cfg.llm_model || "";
  $("cfgApiKey").value = cfg.llm_api_key || "";
  applyProviderUi(cfg.llm_provider || "local");
  if (cfg.last_project) {
    state.project = cfg.last_project;
    $("homeProject").textContent = cfg.last_project;
    $("projPath").textContent = cfg.last_project;
    $("rpProjName").textContent = cfg.last_project;
    scanProject(cfg.last_project); // 右栏项目树：启动即恢复上次项目上下文
  }
  updateComposer();
  refreshLlm();
  renderSidebar();
}
$("btnSaveCfg").onclick = async () => {
  await pywebview.api.set_config({
    engine_python: $("cfgPy").value.trim(), repo_dir: $("cfgRepo").value.trim(),
    device: $("cfgDevice").value, llm_gguf: $("cfgGguf").value.trim(),
    audio_source: $("cfgSource").value,
    xfyun_dir: $("cfgXfyunDir").value.trim(),
    llm_base_url: $("cfgBaseUrl").value.trim(),
    llm_model: $("cfgModel").value.trim(),
    llm_api_key: $("cfgApiKey").value.trim(),
  });
  state.cfg = await pywebview.api.get_config();
  refreshLlm();
  toast("配置已保存");
};

/* ---------- 识别引擎（本地 / 讯飞） ---------- */
$("cfgAsr").onchange = async () => {
  applyAsrUi($("cfgAsr").value);
  await pywebview.api.set_config({ asr_engine: $("cfgAsr").value });
  state.cfg = await pywebview.api.get_config();
  toast(({xfyun_rtasr: "已选择讯飞RTASR连续转写，下次开会生效", xfyun: "已选择讯飞IAT分块听写备用", local: "已选择本地FunASR分块备用"})[$("cfgAsr").value]);
};
$("btnXfyunDir").onclick = async () => {
  const p = await pywebview.api.pick_xfyun_dir();
  if (p) {
    $("cfgXfyunDir").value = p;
    await pywebview.api.set_config({ xfyun_dir: p });
    state.cfg = await pywebview.api.get_config();
    $("xfyunResult").textContent = "文件夹已保存，点「检测」检查对应服务凭证。";
  }
};
$("btnXfyunCheck").onclick = async () => {
  const dir = $("cfgXfyunDir").value.trim();
  await pywebview.api.set_config({ xfyun_dir: dir });
  state.cfg = await pywebview.api.get_config();
  $("xfyunResult").textContent = "检查配置中（不发送音频）…";
  const r = await pywebview.api.check_xfyun();
  if (!r.ok && !r.rtasr && !r.iat && !r.lfasr) {
    $("xfyunResult").textContent = "✗ " + (r.error || "未找到可用凭证");
    return;
  }
  const mark = (x) => x ? (x.valid ? (x.ok === false ? "✗ " : "✓ ") : "✗ ") : "—";
  const parts = [
    `${mark(r.rtasr)}RTASR连续实时转写` + (r.rtasr && r.rtasr.error ? `：${r.rtasr.error}` : ""),
    `${mark(r.iat)}IAT分块听写备用` + (r.iat && r.iat.error ? `：${r.iat.error}` : ""),
    `${mark(r.lfasr)}录音文件转写（文件转写工具）` + (r.lfasr && r.lfasr.error ? `：${r.lfasr.error}` : ""),
  ];
  if (r.notes && r.notes.length) parts.push("提示：" + r.notes.join("；"));
  $("xfyunResult").textContent = parts.join("　|　");
};

/* ---------- 模型厂商（本地 / 自定义 API） ---------- */
$("cfgProvider").onchange = async () => {
  applyProviderUi($("cfgProvider").value);
  await pywebview.api.set_config({ llm_provider: $("cfgProvider").value });
  state.cfg = await pywebview.api.get_config();
  refreshLlm();
  toast($("cfgProvider").value === "custom" ? "使用自定义 OpenAI 兼容 API" : "使用本地模型服务");
};
$("btnTestLlm").onclick = async () => {
  await pywebview.api.set_config({
    llm_base_url: $("cfgBaseUrl").value.trim(),
    llm_model: $("cfgModel").value.trim(),
    llm_api_key: $("cfgApiKey").value.trim(),
  });
  state.cfg = await pywebview.api.get_config();
  $("llmTestResult").textContent = "测试中…";
  const r = await pywebview.api.test_llm();
  $("llmTestResult").textContent = r.ok
    ? `✓ ${r.note || "连通"}` + (r.models && r.models.length ? `：${r.models.join(", ")}` : "")
    : "✗ " + (r.error || "失败");
  refreshLlm();
};

/* ---------- 文件转写工具（媒体文件 → 讯飞 → transcript.txt） ---------- */
let tfTimer = null;
async function pollTf() {
  const st = await pywebview.api.transcribe_media_status();
  const line = $("tfLine");
  if (st.running) {
    line.hidden = false;
    line.textContent = `文件转写中：${(st.file || "").split("\\").pop()} · ${st.progress || "…"}`;
    return;
  }
  if (!st.done) return;
  clearInterval(tfTimer); tfTimer = null;
  line.hidden = false;
  line.textContent = st.ok ? `✓ 转写完成：${st.out}` : `✗ 转写失败：${st.error}`;
  if (st.ok && st.out) toast("转写稿已生成：" + st.out.split("\\").pop(), 9000);
}
$("btnTf").onclick = async () => {
  if (state.running) { toast("会议进行中，先停止会议再做文件转写（避免抢云端配额）"); return; }
  const p = await pywebview.api.pick_media();
  if (!p) return;
  const r = await pywebview.api.transcribe_media_start(p);
  if (!r.ok) { toast("无法开始转写：" + (r.error || "")); return; }
  $("tfLine").hidden = false;
  $("tfLine").textContent = `文件转写中：${p.split("\\").pop()} · 启动…`;
  toast("已开始转写（讯飞云端，通常每分钟音频几秒出结果）");
  if (tfTimer) clearInterval(tfTimer);
  tfTimer = setInterval(pollTf, 2000);
};
/* ---------- 全库理解（项目页：预算 → 后台跑 → 勾选 → 写词表） ---------- */
let undTimer = null;
function resetUnderstand() {
  if (undTimer) { clearInterval(undTimer); undTimer = null; }
  $("undPlanLine").textContent = "";
  $("undProgress").textContent = "";
  $("undResult").hidden = true;
  $("undResult").innerHTML = "";
  $("btnUndGo").disabled = true;
  $("btnUndSmall").disabled = true;
  $("btnUndCancel").hidden = true;
}
async function undPoll() {
  const st = await pywebview.api.understand_status();
  if (st.running) {
    $("undProgress").textContent = `全库理解中：${st.progress || "…"}（可随时取消；已完成块会缓存，续跑不重复花算力）`;
    $("btnUndCancel").hidden = false;
    return;
  }
  $("btnUndCancel").hidden = true;
  if (!st.done) return;
  clearInterval(undTimer); undTimer = null;
  const r = st.result || {};
  const partial = !st.ok && (r.chunks_read || 0) > 0;
  $("undProgress").textContent = st.ok
    ? `✓ 读毕 ${r.chunks_read}/${r.chunks_total} 块 · 覆盖 ${r.covered_chars}/${r.unique_chars} 字（${r.coverage_pct}%）· 缓存命中 ${r.cache_hits} 块`
    : partial
      ? `⚠ 部分完成 ${r.chunks_read}/${r.chunks_total} 块 · 覆盖 ${r.coverage_pct}% · ${(r.failed_chunks || []).length} 块未读（已完成块保留，可重跑续读）`
      : `✗ 中断：${st.error || "未知错误"}（已完成块保留，可续跑）`;
  if (st.ok || partial) renderUnderstandResult(r, partial);
}
function renderUnderstandResult(r, partial = false) {
  const box = $("undResult");
  const terms = r.terms || [];
  const parts = [];
  if (partial) parts.push(`<div class="muted">部分完成：${(r.failed_chunks || []).length} 块未能读取；候选词只来自已读块，写入前请核对。</div>`);
  if (r.overview) parts.push(`<div class="muted">一句话理解：${esc(r.overview)}</div>`);
  parts.push(`<div class="row"><b>候选词</b><span class="muted">数字=文档中出现次数；勾选后只用于专业词纠错，不直接触发保存</span>
    <button class="btn btn-primary" id="btnUndWrite">写入项目词表</button></div>`);
  parts.push('<div class="chips und-chips">' + terms.map((t, i) =>
    `<label class="chip und-chip" title="${esc(t.why || "")}"><input type="checkbox" class="und-pick" data-term="${esc(t.term)}" checked> ${esc(t.term)} <span class="muted">x${t.count}</span></label>`
  ).join("") + "</div>");
  if ((r.persons || []).length) {
    parts.push('<div class="muted" style="margin-top:6px">人物：' +
      r.persons.map((p) => `${esc(p.name)}(x${p.count})`).join("、") + "</div>");
  }
  if ((r.events || []).length) {
    parts.push('<details style="margin-top:6px"><summary class="muted">事件摘录（' +
      r.events.length + ' 条）</summary>' + r.events.slice(0, 8).map((e) =>
        `<div class="muted">· ${esc(e.what)}${e.quote ? `（原句：${esc(e.quote)}）` : ""}</div>`).join("") + "</details>");
  }
  box.innerHTML = parts.join("");
  box.hidden = false;
  $("btnUndWrite").onclick = async () => {
    const picked = [...document.querySelectorAll(".und-pick:checked")].map((c) => c.dataset.term);
    if (!picked.length) { toast("没有勾选任何词"); return; }
    const w = await pywebview.api.terms_write(state.project, picked);
    if (!w.ok) { toast("写入失败：" + (w.error || "")); return; }
    toast(`已写入 ${w.count} 词 → 下次「开始会议」自动采用（${(w.out || "").split("\\").pop()}）`, 9000);
  };
}
$("btnUndPlan").onclick = async () => {
  if (!state.project) { toast("先选项目文件夹"); return; }
  $("undPlanLine").textContent = "计算中…";
  const p = await pywebview.api.understand_plan(state.project);
  if (!p.ok) { $("undPlanLine").textContent = "失败：" + (p.error || ""); return; }
  $("undProvider").textContent = p.label || (p.provider === "custom" ? "自定义 API" : "本地模型");
  $("undEgress").textContent = p.egress || "";
  const hrs = p.est_local_minutes >= 90 ? `${(p.est_local_minutes / 60).toFixed(1)} 小时` : `${p.est_local_minutes} 分钟`;
  // 摘要一行 + 明细折叠（曾被几十条去重说明糊成一面墙——实测教训）
  let line = `全库 ${p.chars} 字 / ${p.chunks.length} 块；本地模型预计 ~${hrs}`;
  if (p.notes_summary) line += `；${p.notes_summary}`;
  line += "；云端厂商通常快 10~50 倍（费用在厂商侧）";
  let extra = "";
  if (p.notes && p.notes.length) {
    extra = `<details class="rawterms"><summary class="muted">明细（${p.notes.length} 条）</summary>` +
      `<div class="muted">${p.notes.map(esc).join("<br>")}</div></details>`;
  }
  if (!p.ready && p.error) {
    extra = `<div class="pick-warn">✗ 模型未就绪：${esc(p.error)}</div>` + extra;
  }
  $("undPlanLine").innerHTML = esc(line) + extra;
  $("btnUndGo").disabled = !p.ready;
  $("btnUndSmall").disabled = !p.ready;
};
$("btnUndGo").onclick = async () => {
  if (state.running) { toast("会议进行中——全库理解会抢占模型资源，请先结束会议"); return; }
  const r = await pywebview.api.understand_start(state.project, 0);
  if (!r.ok) { toast("启动失败：" + (r.error || "")); return; }
  $("undResult").hidden = true;
  $("undProgress").textContent = "启动中…";
  if (undTimer) clearInterval(undTimer);
  undTimer = setInterval(undPoll, 2000);
};
$("btnUndSmall").onclick = async () => {
  if (state.running) { toast("会议进行中——请先结束会议再跑全库理解"); return; }
  const r = await pywebview.api.understand_start(state.project, 5);
  if (!r.ok) { toast("启动失败：" + (r.error || "")); return; }
  toast("小样试跑：只读前 5 块，用来先看产出质量");
  $("undResult").hidden = true;
  if (undTimer) clearInterval(undTimer);
  undTimer = setInterval(undPoll, 2000);
};
$("btnUndCancel").onclick = async () => {
  const r = await pywebview.api.understand_cancel();
  toast(r.ok ? "已取消（已完成块保留，可续跑）" : (r.error || "取消失败"));
};

$("btnTreeRefresh").onclick = () => { if (state.project) scanProject(state.project); };
/* ---------- 使用统计 ---------- */
function fmtTokens(n) {
  n = Number(n) || 0;
  if (n >= 1e8) return (n / 1e8).toFixed(1) + "亿";
  if (n >= 1e4) return (n / 1e4).toFixed(1) + "万";
  return String(n);
}
function dayKey(offset) {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() + offset);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function renderUsage() {
  const data = state.usage || {};
  const totals = data.totals || {};
  const days = data.days || [];
  const byDate = new Map(days.map((d) => [d.date, d]));
  const peak = days.reduce((m, d) => Math.max(m, Number(d.tokens) || 0), 0);
  const active = days.filter((d) => (Number(d.tokens) || 0) > 0).length;
  $("usageTotal").textContent = fmtTokens(totals.tokens);
  $("usageCalls").textContent = String(totals.calls || 0);
  $("usagePeak").textContent = fmtTokens(peak);
  $("usageActiveDays").textContent = String(active);
  $("usageErrors").textContent = String(totals.errors || 0);
  const heat = $("usageHeatmap");
  const maxDay = Math.max(1, ...days.map((d) => Number(d.tokens) || 0));
  const cells = [];
  for (let i = -83; i <= 0; i++) {
    const key = dayKey(i);
    const item = byDate.get(key) || { tokens: 0, calls: 0 };
    const value = Number(item.tokens) || 0;
    const level = value ? Math.max(1, Math.min(4, Math.ceil(value / maxDay * 4))) : 0;
    cells.push(`<span class="heat-cell${level ? " l" + level : ""}" title="${key} · ${fmtTokens(value)} tokens · ${item.calls || 0} 次"></span>`);
  }
  heat.innerHTML = cells.join("");

  const range = Math.max(2, state.usageRange || 7);
  const series = [];
  for (let i = range - 1; i >= 0; i--) {
    const key = dayKey(-i);
    const item = byDate.get(key) || { tokens: 0 };
    series.push({ date: key, tokens: Number(item.tokens) || 0 });
  }
  const max = Math.max(1, ...series.map((p) => p.tokens));
  const W = 800, H = 190, padX = 34, padY = 20;
  const x = (i) => padX + (series.length <= 1 ? 0 : i * (W - padX * 2) / (series.length - 1));
  const y = (v) => H - padY - (v / max) * (H - padY * 2);
  const points = series.map((p, i) => `${x(i).toFixed(1)},${y(p.tokens).toFixed(1)}`).join(" ");
  const area = `${padX},${H - padY} ${points} ${W - padX},${H - padY}`;
  const grid = [0, .25, .5, .75, 1].map((v) => {
    const yy = (H - padY - v * (H - padY * 2)).toFixed(1);
    return `<line x1="${padX}" y1="${yy}" x2="${W - padX}" y2="${yy}" stroke="#2c2c2c" stroke-width="1"/>`;
  }).join("");
  const labels = [0, Math.floor((series.length - 1) / 2), series.length - 1]
    .filter((v, i, a) => a.indexOf(v) === i)
    .map((i) => `<text x="${x(i).toFixed(1)}" y="${H - 2}" fill="#6f6f6f" font-size="10" text-anchor="middle">${series[i].date.slice(5)}</text>`).join("");
  const hasData = series.some((p) => p.tokens > 0);
  $("usageChart").innerHTML = hasData
    ? `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${grid}
       <polyline points="${points}" fill="none" stroke="#7aa2ff" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
       <polygon points="${area}" fill="rgba(122,162,255,.10)"/>
       <text x="${padX}" y="12" fill="#6f6f6f" font-size="10">${fmtTokens(max)}</text>${labels}</svg>`
    : "";
  $("usageEmpty").hidden = hasData;

  const models = (data.models || []).slice(0, 8);
  const modelTotal = models.reduce((s, m) => s + (Number(m.tokens) || 0), 0) || 1;
  $("usageModels").className = models.length ? "usage-models" : "usage-models muted";
  $("usageModels").innerHTML = models.length ? models.map((m) => {
    const pct = Math.round((Number(m.tokens) || 0) / modelTotal * 100);
    return `<div class="usage-model-row"><span>${esc(m.model || "unknown")}</span>
      <span class="usage-model-bar"><span style="width:${pct}%"></span></span>
      <span class="muted">${fmtTokens(m.tokens)} · ${pct}%</span></div>`;
  }).join("") : "暂无数据";
}
async function loadUsage() {
  try {
    const data = await pywebview.api.usage_stats();
    state.usage = data && data.ok ? data : { totals: {}, days: [], models: [] };
  } catch (e) {
    state.usage = { totals: {}, days: [], models: [] };
  }
  renderUsage();
}
$("usageRange7").onclick = () => { state.usageRange = 7; $("usageRange7").classList.add("active"); $("usageRange30").classList.remove("active"); renderUsage(); };
$("usageRange30").onclick = () => { state.usageRange = 30; $("usageRange30").classList.add("active"); $("usageRange7").classList.remove("active"); renderUsage(); };

/* ---------- 设置分区 ---------- */
function switchSettings(name) {
  document.querySelectorAll(".settings-tab").forEach((b) => b.classList.toggle("active", b.dataset.settings === name));
  document.querySelectorAll(".settings-pane").forEach((p) => p.classList.toggle("active", p.id === "settings-" + name));
  if (name === "usage") loadUsage();
}
document.querySelectorAll(".settings-tab").forEach((b) => { b.onclick = () => switchSettings(b.dataset.settings); });

$("btnTestEngine").onclick = async () => {
  $("testResult").textContent = "测试中…";
  const r = await pywebview.api.test_engine();
  $("testResult").textContent = r.ok ? `引擎正常：Python ${r.python} · meetingvar ${r.meetingvar}` : "失败：" + (r.error || "");
};
$("btnOpenCfg").onclick = () => pywebview.api.open_config_dir();

/* ---------- 导航/弹窗事件 ---------- */
$("btnAskOk").onclick = () => answerAsk($("askInput").value);
$("btnAskSkip").onclick = () => answerAsk("");
$("askInput").addEventListener("keydown", (e) => { if (e.key === "Enter") answerAsk($("askInput").value); });

/* ---------- 新壳交互 ---------- */
$("btnSidebarCollapse").onclick = () => {
  $("appShell").classList.toggle("sidebar-collapsed");
  $("btnSidebarCollapse").textContent = $("appShell").classList.contains("sidebar-collapsed") ? "›" : "‹";
};
$("btnBack").onclick = pageBack;
$("btnForward").onclick = pageForward;
$("btnNewTask").onclick = () => { switchPage("home"); newChat(); };
$("btnSideSearch").onclick = () => {
  switchPage("home");
  $("homeAsk").focus();
  toast("直接问：哪场会议提到过… / 谁负责… / 项目现在什么进度", 6000);
};
$("btnSideAddProject").onclick = async () => {
  const p = await pywebview.api.pick_folder();
  if (p) { await scanProject(p); switchPage("home"); }
};
$("btnSideRefresh").onclick = loadConversations;
$("composerProject").onclick = () => switchPage("project");
$("btnComposerNew").onclick = () => newChat();
function sendComposer() {
  const q = $("homeAsk").value.trim();
  if (!q) { $("homeAsk").focus(); return; }
  sendChat(q);
}
$("btnComposerSend").onclick = sendComposer;
$("homeAsk").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendComposer(); }
});
$("btnQuickMeeting").onclick = () => {
  switchPage("home");
  openMeetingConsole();
  $("btnRec").click();
};
$("btnQuickLibrary").onclick = () => sendChat("列出最近的会议，并总结每场的主要决定、行动项和未决问题。");
$("btnQuickUnderstand").onclick = () => sendChat("查看当前项目的进展、已保存事实和会前关注，告诉我现在最需要确认什么。");
$("btnQuickAgent").onclick = () => showSkills();
$("btnConsoleClose").onclick = () => { state.consolePinned = false; showMeetingConsole(false); };
$("btnSkills").onclick = () => showSkills();
$("btnOpenSkills").onclick = () => showSkills();
$("librarySearch").addEventListener("input", filterLibrary);
document.addEventListener("keydown", (e) => {
  if (!(e.ctrlKey || e.metaKey)) return;
  if (e.key.toLowerCase() === "n") { e.preventDefault(); $("btnNewTask").click(); }
  if (e.key.toLowerCase() === "k") { e.preventDefault(); $("btnSideSearch").click(); }
});

/* 管理页改为对话上方抽屉，不再替换整个工作区 */
document.querySelectorAll(".page").forEach((page) => {
  if (page.id === "page-home") return;
  page.classList.add("panel");
  const close = document.createElement("button");
  close.className = "panel-close";
  close.type = "button";
  close.textContent = "×";
  close.title = "关闭";
  close.onclick = () => switchPage("home");
  page.prepend(close);
});
$("panelScrim").onclick = () => switchPage("home");

$("fileViewerClose").onclick = () => { $("fileViewer").hidden = true; };
$("btnViewerOpen").onclick = async () => {
  if (!state.previewRel) return;
  const r = await pywebview.api.open_project_file(state.project, state.previewRel);
  if (!r || !r.ok) toast("打开失败：" + ((r && r.error) || "未知错误"));
};
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (!$("fileViewer").hidden) $("fileViewer").hidden = true;
    else if (document.body.classList.contains("panel-open")) switchPage("home");
  }
});

/* ---------- 启动 ---------- */
async function boot() {
  const info = await pywebview.api.bootstrap();
  state.mediaBase = info.media_base;
  $("ver").textContent = "v" + info.version;
  $("aboutVersion").textContent = "v" + info.version;
  const hour = new Date().getHours();
  $("greeting").textContent = hour < 6 ? "夜深了，先保存这一场"
    : hour < 12 ? "早上好呀，新的一天开始啦"
    : hour < 18 ? "下午好，继续留下重要证据"
    : "晚上好，今天要回看哪一场？";
  updateComposer();
  showMeetingConsole(!!info.meeting_running);
  setRunning(info.meeting_running, null);
  setTimeout(() => { if (!state.running && !state.consolePinned) showMeetingConsole(false); }, 1200);
  await loadConfig();
  setInterval(poll, 800);
  // JS 桥活体回执（自检/诊断用：证明前端引导完成且 API 往返正常）
  await pywebview.api.page_ready({ page: "home", version: info.version });
}
// pywebview 注入的桥可能在脚本加载之后才就绪——按官方模式等 pywebviewready
if (window.pywebview && window.pywebview.api) {
  boot();
} else {
  window.addEventListener("pywebviewready", boot);
}
