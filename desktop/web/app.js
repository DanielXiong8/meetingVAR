/* meetingVAR 桌面壳前端逻辑：轮询事件流 + 页面渲染 + 点名弹窗 */
"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  next: 0, running: false, startedAt: null, hits: 0,
  speakers: new Set(), project: "", scan: null, mediaBase: "",
  askQueue: [], askOpen: false, ticker: [], phase: "idle", follow: true, captionCount: 0,
  lastCaptionAt: null, screen: {}, starting: false, polling: false, partials: new Map(),
};

/* ---------- 小工具 ---------- */
function esc(s) {
  return String(s == null ? "" : s)
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}
// Labels accept both Windows and POSIX separators; paths passed to the bridge stay untouched.
function fileName(path) { return String(path || "").split(/[\\/]/).pop(); }
function uiIcon(name) { return `<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><use href="#icon-${name}"></use></svg>`; }
function badge(text, kind = "") { return `<span class="badge${kind ? " badge-" + kind : ""}">${esc(text)}</span>`; }
function showNotice(id, kind, title, detail = "", action = null) {
  const box = $(id);
  box.className = `notice notice-${kind}`;
  box.setAttribute("role", kind === "error" ? "alert" : "status");
  box.replaceChildren();
  const icon = document.createElement("span");
  icon.className = "notice-icon";
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = ({error: "!", success: "✓", warning: "!", info: "i"})[kind] || "";
  const content = document.createElement("div");
  content.className = "notice-content";
  const heading = document.createElement("div");
  heading.className = "notice-title";
  heading.textContent = title;
  content.appendChild(heading);
  if (detail) {
    const description = document.createElement("div");
    description.className = "notice-detail";
    description.textContent = detail;
    content.appendChild(description);
  }
  box.append(icon, content);
  if (action) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn-secondary btn-sm";
    button.textContent = action.label || "重试";
    button.onclick = async () => {
      button.disabled = true;
      try { await action.run(); }
      catch (e) { showNotice(id, "error", "操作未完成", String(e), action); }
      finally { if (button.isConnected) button.disabled = false; }
    };
    box.appendChild(button);
  }
  box.hidden = false;
  return content;
}
function hideNotice(id) { $(id).hidden = true; $(id).replaceChildren(); }

/* Viewport-aware help, available on hover, focus and click. No native title delay. */
let helpTrigger = null, helpTimer = null;
function hideHelp() {
  clearTimeout(helpTimer);
  if (helpTrigger) helpTrigger.removeAttribute("aria-describedby");
  helpTrigger = null;
  $("helpTooltip").hidden = true;
}
function showHelp(trigger) {
  const text = trigger.dataset.tooltip;
  if (!text) return;
  hideHelp();
  helpTrigger = trigger;
  trigger.setAttribute("aria-describedby", "helpTooltip");
  const tip = $("helpTooltip");
  tip.textContent = text;
  tip.hidden = false;
  const rect = trigger.getBoundingClientRect();
  const left = Math.max(16, Math.min(rect.left, window.innerWidth - tip.offsetWidth - 16));
  const below = rect.bottom + 8;
  const top = below + tip.offsetHeight <= window.innerHeight - 16
    ? below : Math.max(16, rect.top - tip.offsetHeight - 8);
  tip.style.left = `${left}px`;
  tip.style.top = `${top}px`;
}
function deferHideHelp() {
  clearTimeout(helpTimer);
  helpTimer = setTimeout(() => {
    if (helpTrigger !== document.activeElement && !$("helpTooltip").matches(":hover")) hideHelp();
  }, 150);
}
document.addEventListener("pointerover", (event) => {
  const trigger = event.target.closest(".info-button[data-tooltip]");
  if (trigger) showHelp(trigger);
  else if (event.target.closest("#helpTooltip")) clearTimeout(helpTimer);
});
document.addEventListener("pointerout", (event) => {
  if (event.target.closest(".info-button, #helpTooltip")) deferHideHelp();
});
document.addEventListener("focusin", (event) => {
  const trigger = event.target.closest(".info-button[data-tooltip]");
  if (trigger) showHelp(trigger); else hideHelp();
});
document.addEventListener("focusout", deferHideHelp);
document.addEventListener("click", (event) => {
  const trigger = event.target.closest(".info-button[data-tooltip]");
  if (trigger) showHelp(trigger);
  else if (!event.target.closest("#helpTooltip")) hideHelp();
});
document.addEventListener("keydown", (event) => { if (event.key === "Escape") hideHelp(); });
window.addEventListener("resize", hideHelp);
$("workspace").addEventListener("scroll", hideHelp);

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
function switchPage(name) {
  hideHelp();
  document.querySelectorAll(".page").forEach((p) => p.classList.toggle("active", p.id === "page-" + name));
  document.querySelectorAll(".navbtn").forEach((b) => b.classList.toggle("active", b.dataset.page === name));
  if (name === "library") refreshLibrary();
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
    else toast("全库理解中断：" + (ev.error || ""), 9000);
  } else if (ev.type === "transcribe_done") {
    if (ev.ok) {
      toast("文件转写完成：" + fileName(ev.out), 9000);
      pushTicker("", "系统", "文件转写完成 → " + (ev.out || ""), "sys");
    } else {
      toast("文件转写失败：" + (ev.error || "未知错误"), 9000);
      pushTicker("", "系统", "文件转写失败：" + (ev.error || ""), "err");
    }
  }
}

function pushTicker(t, spk, text, kind) {
  state.ticker.push({ t, spk, text, kind });
  const isLog = kind === "sys" || kind === "err";
  const body = $(isLog ? "eventLog" : "ticker");
  const div = document.createElement("div");
  div.className = "tk" + (kind ? " " + kind : "");
  div.innerHTML = `<span class="t">${esc(t)}</span><span class="s">${esc(spk)}</span>${esc(text)}`;
  body.appendChild(div);
  if (isLog) {
    $("logCount").textContent = body.children.length;
    body.scrollTop = body.scrollHeight;
  } else if (state.follow) body.scrollTop = body.scrollHeight;
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
  $("ticker").innerHTML = ""; $("eventLog").replaceChildren(); $("logCount").textContent = "0";
  hideNotice("meetingNotice");
  $("statHits").textContent = "命中 0";
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
  if (!r.ok) {
    state.phase = "idle"; $("captureStatus").textContent = "启动失败";
    showNotice("meetingNotice", "error", "会议未能启动", r.error || "请检查引擎与 API 配置", {run: () => $("btnRec").onclick()});
    return;
  }
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
    row.innerHTML =
      `<span class="tree-ico">${n.dir ? uiIcon("folder") : fileGlyph(n.name)}</span>` +
      `<span class="tree-name">${esc(n.name)}</span>` +
      (!n.dir ? `<span class="tree-size">${fmtSize(n.size)}</span>` : "");
    box.appendChild(row);
    if (n.dir) {
      const kids = document.createElement("div");
      kids.hidden = depth >= 1; // 顶层目录默认展开，更深层折叠
      renderTree(n.children || [], kids, depth + 1);
      row.setAttribute("role", "button");
      row.tabIndex = 0;
      row.setAttribute("aria-label", n.name);
      row.setAttribute("aria-expanded", String(!kids.hidden));
      const toggle = () => {
        kids.hidden = !kids.hidden;
        row.classList.toggle("collapsed", kids.hidden);
        row.setAttribute("aria-expanded", String(!kids.hidden));
      };
      row.onclick = toggle;
      row.onkeydown = (event) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle(); }
      };
      box.appendChild(kids);
    }
  }
}
async function scanProject(path) {
  if (undState.running || undState.starting || undState.planning) { toast("请先等待或取消当前理解任务"); return; }
  const scanVersion = (state.scanVersion || 0) + 1;
  state.scanVersion = scanVersion;
  state.project = path;
  state.scan = null;
  resetUnderstand();
  $("projPath").textContent = path;
  $("projManual").value = path;
  $("homeProject").textContent = path;
  $("rpProjName").textContent = path;
  $("scanMeta").replaceChildren();
  $("scanTerms").replaceChildren();
  $("sideTree").replaceChildren();
  $("treeStat").textContent = "";
  $("btnUseProject").disabled = true;
  $("btnScanInput").disabled = true;
  $("btnPick").disabled = true;
  showNotice("scanStatus", "busy", "正在读取项目资料");
  let d;
  try { d = await pywebview.api.scan_project(path); }
  catch (e) { d = {ok: false, error: String(e)}; }
  if (scanVersion !== state.scanVersion) return;
  state.scan = d;
  $("btnScanInput").disabled = false;
  $("btnPick").disabled = false;
  if (!d.ok) {
    showNotice("scanStatus", "error", "项目读取失败", d.error || "请检查文件夹路径", {run: () => scanProject(path)});
    syncUnderstandButtons();
    return;
  }
  hideNotice("scanStatus");
  renderTree(d.tree || [], $("sideTree"), 0);
  $("treeStat").textContent = `${d.doc_count || 0} 份`;
  let meta = badge(`${d.doc_count || 0} 份文档`);
  if (d.skipped && d.skipped.length) {
    meta += `<span class="badge">已跳过 ${d.skipped.length} 个非文本文件</span>`;
  }
  $("scanMeta").innerHTML = meta;
  const chips = (obj, n = 20) => Object.keys(obj).slice(0, n)
    .map((w) => `<span class="chip"><b>${esc(w)}</b></span>`).join("");
  let html = "";
  if (d.repo && d.repo.is_repo && Object.keys(d.repo.top || {}).length) html += `<details class="rawterms"><summary>代码符号</summary><div class="chips">${chips(d.repo.top)}</div></details>`;
  if (d.persons && d.persons.length) html += `<details class="rawterms"><summary>人物 · ${d.persons.length}</summary><div class="chips">${d.persons.map((person) => `<span class="chip">${esc(person)}</span>`).join("")}</div></details>`;
  if (d.terms && Object.keys(d.terms).length) {
    html += `<details class="rawterms"><summary>原始抽取 · ${d.term_count || 0} 个词（调试）</summary><div class="chips">${chips(d.terms, 40)}</div></details>`;
  }
  const notes = [...(d.notes || []), ...(d.skipped || []).map((name) => `跳过：${name}`)];
  if (notes.length) html += `<details class="rawterms"><summary>读取明细 · ${notes.length}</summary><div class="muted">${notes.map(esc).join("<br>")}</div></details>`;
  $("scanTerms").innerHTML = html
    ? `<details class="rawterms"><summary>项目索引与读取明细</summary><div>${html}</div></details>` : "";
  $("btnUseProject").disabled = false;
  syncUnderstandButtons();
  try { await pywebview.api.set_config({last_project: path}); }
  catch (e) { showNotice("scanStatus", "error", "项目已读取，但未能保存最近项目", String(e), {run: () => scanProject(path)}); }
}
$("btnPick").onclick = async () => {
  try {
    const path = await pywebview.api.pick_folder();
    if (path) await scanProject(path);
  } catch (e) { showNotice("scanStatus", "error", "无法打开文件夹选择器", String(e), {run: () => $("btnPick").onclick()}); }
};
$("btnScanInput").onclick = () => {
  const path = $("projManual").value.trim();
  if (path) return scanProject(path);
  $("projManual").focus();
};
$("projManual").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.isComposing && !$("btnScanInput").disabled) $("btnScanInput").click();
});
$("btnUseProject").onclick = () => { switchPage("home"); toast("已就绪——回主页按「开始会议」"); };

/* ---------- 会议库（按项目分组：项目 → 场次 → 详情） ---------- */
async function refreshLibrary() {
  const groups = await pywebview.api.list_library();
  $("sessionDetail").hidden = true;
  const box = $("libGroups");
  if (!groups.length) { box.innerHTML = `<div class="muted">还没有会议记录（零命中会话按设计不留任何文件）</div>`; return; }
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

/* ---------- 设置 + LLM 服务 ---------- */
const PROVIDER_LABEL = { local: "本地模型", custom: "自定义 API" };
function applyProviderUi(provider) {
  $("cfgProvider").value = provider;
  $("provLocal").hidden = provider !== "local";
  $("provCustom").hidden = false;
}
function applyAsrUi(engine) {
  $("cfgAsr").value = engine;
  $("xfyunSettings").hidden = engine === "local";
  $("captionHelp").dataset.tooltip = engine === "xfyun_rtasr"
    ? "讯飞RTASR临时字幕持续更新，句尾定稿后进入兴趣判断。"
    : "当前使用分块识别；字幕延迟包含音频块时长与推理耗时。";
}
async function refreshLlm() {
  const st = await pywebview.api.llm_status();
  $("llmStatus").className = "badge" + (st.up ? " badge-success" : "");
  $("llmStatus").textContent = st.up
    ? `● 运行中（${st.model || "模型已载入"}）`
    : (st.managed ? "○ 启动中…（首次加载约 30-60 秒）" : "○ 未运行");
  const cfg = state.cfg || {};
  const configured = !!(cfg.llm_base_url || "").trim() && !!(cfg.llm_model || "").trim();
  $("llmPill").textContent = cfg.interest_mode === "manual" ? "仅手动保存" : (configured ? "兴趣API 已配置" : "兴趣API 未配置");
  $("llmPill").classList.toggle("on", configured && cfg.interest_mode !== "manual");
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
  updateUnderstandProvider();
  if (cfg.last_project) {
    state.project = cfg.last_project;
    $("homeProject").textContent = cfg.last_project;
    $("projPath").textContent = cfg.last_project;
    $("rpProjName").textContent = cfg.last_project;
    scanProject(cfg.last_project); // 右栏项目树：启动即恢复上次项目上下文
  }
  refreshLlm();
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
  updateUnderstandProvider();
  invalidateUnderstandPlan();
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
    showNotice("xfyunResult", "info", "凭证目录已保存", "点击检测凭证检查配置格式。");
  }
};
async function checkXfyun() {
  $("btnXfyunCheck").disabled = true;
  showNotice("xfyunResult", "busy", "正在检查凭证格式");
  try {
    await pywebview.api.set_config({xfyun_dir: $("cfgXfyunDir").value.trim()});
    state.cfg = await pywebview.api.get_config();
    const result = await pywebview.api.check_xfyun();
    const currentKey = $("cfgAsr").value === "xfyun_rtasr" ? "rtasr" : "iat";
    const current = result[currentKey];
    const valid = !!(current && current.valid && current.ok !== false);
    const checks = [["rtasr", "RTASR"], ["iat", "IAT"], ["lfasr", "文件转写"]];
    const content = showNotice("xfyunResult", valid ? "success" : "error",
      valid ? "当前识别服务凭证格式通过" : "未找到当前服务的可用凭证",
      valid ? "格式检查不代表服务连接或配额验证通过。" : ((current && current.error) || result.error || "请核对所选服务和凭证目录。"),
      valid ? null : {run: checkXfyun});
    const metrics = document.createElement("div");
    metrics.className = "metric-row";
    metrics.innerHTML = checks.map(([key, label]) => {
      const item = result[key];
      const ok = item && item.valid && item.ok !== false;
      return badge(`${label} · ${ok ? "格式通过" : "未通过"}`, ok ? "success" : "error");
    }).join("");
    content.appendChild(metrics);
    if (result.notes && result.notes.length) {
      const notes = document.createElement("details");
      notes.className = "rawterms";
      notes.innerHTML = `<summary>检测明细</summary><div class="muted">${result.notes.map(esc).join("<br>")}</div>`;
      content.appendChild(notes);
    }
  } catch (e) { showNotice("xfyunResult", "error", "凭证检测未完成", String(e), {run: checkXfyun}); }
  finally { $("btnXfyunCheck").disabled = false; }
}
$("btnXfyunCheck").onclick = checkXfyun;

/* ---------- 模型厂商（本地 / 自定义 API） ---------- */
$("cfgProvider").onchange = async () => {
  applyProviderUi($("cfgProvider").value);
  await pywebview.api.set_config({ llm_provider: $("cfgProvider").value });
  state.cfg = await pywebview.api.get_config();
  refreshLlm();
  updateUnderstandProvider();
  invalidateUnderstandPlan();
  toast($("cfgProvider").value === "custom" ? "使用自定义 OpenAI 兼容 API" : "使用本地模型服务");
};
async function testLlm() {
  $("btnTestLlm").disabled = true;
  showNotice("llmTestResult", "busy", "正在测试生成");
  try {
    await pywebview.api.set_config({llm_base_url: $("cfgBaseUrl").value.trim(), llm_model: $("cfgModel").value.trim(), llm_api_key: $("cfgApiKey").value.trim()});
    state.cfg = await pywebview.api.get_config();
    const result = await pywebview.api.test_llm();
    showNotice("llmTestResult", result.ok ? "success" : "error", result.ok ? "API 生成测试通过" : "API 生成测试失败",
      result.ok ? (result.note || "已收到生成结果") : (result.error || "请检查 API 地址、模型和凭证"), result.ok ? null : {run: testLlm});
    refreshLlm();
    invalidateUnderstandPlan();
  } catch (e) { showNotice("llmTestResult", "error", "API 测试未完成", String(e), {run: testLlm}); }
  finally { $("btnTestLlm").disabled = false; }
}
$("btnTestLlm").onclick = testLlm;

/* ---------- 文件转写工具（媒体文件 → 讯飞 → transcript.txt） ---------- */
let tfTimer = null, tfPath = "", tfPolling = false;
async function pollTf() {
  if (tfPolling) return;
  tfPolling = true;
  try {
    const status = await pywebview.api.transcribe_media_status();
    if (status.running) { showNotice("tfLine", "busy", "文件转写中", `${fileName(status.file)} · ${status.progress || "正在处理"}`); return; }
    if (!status.done) return;
    clearInterval(tfTimer); tfTimer = null;
    $("btnTf").disabled = false;
    showNotice("tfLine", status.ok ? "success" : "error", status.ok ? "文件转写完成" : "文件转写失败",
      status.ok ? status.out : status.error, status.ok ? null : {run: () => startTranscription(tfPath)});
  } catch (e) {
    showNotice("tfLine", "error", "无法读取转写进度", String(e), {label: "重试连接", run: pollTf});
  } finally { tfPolling = false; }
}
async function startTranscription(path) {
  if (state.running) { toast("请先结束会议，再进行文件转写"); return; }
  tfPath = path;
  $("btnTf").disabled = true;
  try {
    const result = await pywebview.api.transcribe_media_start(path);
    if (!result.ok) throw new Error(result.error || "无法启动转写任务");
    showNotice("tfLine", "busy", "文件转写中", fileName(path));
    if (tfTimer) clearInterval(tfTimer);
    tfTimer = setInterval(pollTf, 2000);
  } catch (e) {
    $("btnTf").disabled = false;
    showNotice("tfLine", "error", "文件转写未能启动", String(e), {run: () => startTranscription(path)});
  }
}
$("btnTf").onclick = async () => {
  if (state.running) { toast("请先结束会议，再进行文件转写"); return; }
  const path = await pywebview.api.pick_media();
  if (path) await startTranscription(path);
};

/* ---------- Understanding: plan, run, resume, review. Backend contract unchanged. ---------- */
let undTimer = null, undPolling = false;
const undState = {plan: null, planning: false, starting: false, running: false, maxChunks: 0, cancelled: false, resultReady: false, termsWritten: false};
function syncProjectJourney() {
  const sourceReady = !!(state.scan && state.scan.ok);
  const budgetReady = !!(undState.plan && undState.plan.ready);
  const active = undState.starting || undState.running;
  const resultReady = undState.resultReady && !active;
  const complete = [sourceReady, budgetReady, resultReady, undState.termsWritten];
  const allComplete = complete.every(Boolean);
  const current = !sourceReady ? 0 : !budgetReady ? 1 : !resultReady ? 2 : 3;
  const available = [!active && !undState.planning, sourceReady, budgetReady || active, resultReady];
  document.querySelectorAll(".journey-step").forEach((step, index) => {
    step.dataset.state = complete[index] ? "complete" : index === current ? "current" : "pending";
    if (index === current && !allComplete) step.setAttribute("aria-current", "step");
    else step.removeAttribute("aria-current");
    step.disabled = !available[index];
    step.querySelector(".step-number").innerHTML = complete[index] ? uiIcon("check") : String(index + 1);
  });
}
document.querySelectorAll(".journey-step").forEach((step) => {
  step.onclick = () => {
    const targets = {source: "projManual", budget: "btnUndPlan", read: undState.running ? "btnUndCancel" : "btnUndGo", confirm: "btnUndWrite"};
    const target = $(targets[step.dataset.step]);
    if (!target) return;
    target.scrollIntoView({behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center"});
    if (!target.disabled) target.focus({preventScroll: true});
  };
});
function syncUnderstandButtons() {
  const busy = undState.planning || undState.starting || undState.running;
  const ready = !!(undState.plan && undState.plan.ready && state.scan && state.scan.ok);
  $("btnUndGo").disabled = busy || !ready;
  $("btnUndSmall").disabled = busy || !ready;
  $("btnUndPlan").disabled = busy || !state.project || !(state.scan && state.scan.ok);
  $("btnUndCancel").hidden = !undState.running;
  $("btnPick").disabled = busy;
  $("btnScanInput").disabled = busy;
  $("projManual").disabled = busy;
  $("btnTreeRefresh").disabled = busy;
  $("undCard").setAttribute("aria-busy", String(busy));
  $("btnUndPlan").dataset.loading = String(undState.planning);
  $("btnUndPlan").textContent = undState.planning ? "计算中…" : "先算预算";
  $("btnUndGo").dataset.loading = String(undState.starting || undState.running);
  $("btnUndGo").textContent = undState.starting ? "正在启动…" : undState.running ? "理解中…" : undState.resultReady ? "重新理解" : "开始全库理解";
  syncProjectJourney();
}
function updateUnderstandProvider() {
  const provider = (state.cfg || {}).llm_provider || "local";
  $("undProvider").textContent = PROVIDER_LABEL[provider] || provider;
  $("undProviderHelp").dataset.tooltip = provider === "custom"
    ? "项目文档块将发送至设置中的自定义 API。预算不包含厂商报价，实际用量以厂商账单为准。"
    : "本地模型在本机处理项目资料。模型服务需先在设置页启动。";
}
function resetUnderstand() {
  if (undTimer) clearInterval(undTimer);
  undTimer = null;
  undState.plan = null;
  undState.running = false;
  undState.cancelled = false;
  undState.resultReady = false;
  undState.termsWritten = false;
  $("undPlanLine").hidden = true;
  $("undPlanLine").replaceChildren();
  hideNotice("undProgress");
  hideNotice("undFeedback");
  $("undResult").hidden = true;
  $("undResult").replaceChildren();
  $("btnUndGo").textContent = "开始全库理解";
  updateUnderstandProvider();
  syncUnderstandButtons();
}
function invalidateUnderstandPlan() {
  if (undState.running || undState.starting || undState.planning) return;
  undState.plan = null;
  $("undPlanLine").hidden = true;
  hideNotice("undFeedback");
  syncUnderstandButtons();
}
async function undPoll() {
  if (undPolling) return;
  undPolling = true;
  try {
    const status = await pywebview.api.understand_status();
    if (status.folder && status.folder !== state.project) return;
    if (status.running) {
      const content = showNotice("undProgress", "busy", "正在理解项目资料", status.progress || "等待模型返回");
      const progress = document.createElement("progress");
      progress.className = "progress-track";
      progress.setAttribute("aria-label", "项目理解进度");
      const match = (status.progress || "").match(/(\d+)\s*\/\s*(\d+)\s*块/);
      if (match && Number(match[2]) > 0) { progress.max = Number(match[2]); progress.value = Number(match[1]); }
      content.appendChild(progress);
      return;
    }
    if (!status.done) return;
    clearInterval(undTimer); undTimer = null;
    undState.running = false;
    syncUnderstandButtons();
    hideNotice("undProgress");
    const result = status.result || {};
    if (status.ok) {
      showNotice("undFeedback", "success", undState.maxChunks ? "小样试跑完成" : "项目理解完成");
      $("undFeedback").querySelector(".notice-content").insertAdjacentHTML("beforeend", `<div class="metric-row">${badge(`已读 ${result.chunks_read || 0}/${result.chunks_total || 0} 块`)}${badge(`覆盖 ${result.coverage_pct || 0}%`)}${badge(`缓存命中 ${result.cache_hits || 0} 块`)}</div>`);
      renderUnderstandResult(result);
      $("btnUndGo").textContent = "重新理解";
    } else {
      showNotice("undFeedback", undState.cancelled ? "info" : "error", undState.cancelled ? "理解任务已取消" : "全库理解已中断",
        status.error || "已完成块保留，可继续运行。", {label: undState.cancelled ? "继续理解" : "重试", run: () => startUnderstand(undState.maxChunks)});
    }
  } catch (e) {
    showNotice("undFeedback", "error", "无法读取理解进度", String(e), {label: "重试连接", run: undPoll});
  } finally { undPolling = false; }
}
function renderUnderstandResult(result) {
  const terms = result.terms || [];
  undState.resultReady = true;
  undState.termsWritten = false;
  const parts = [];
  if (result.overview) parts.push(`<div class="result-overview">${esc(result.overview)}</div>`);
  parts.push(`<div class="result-heading"><div class="section-heading"><h3>确认词表</h3><button type="button" class="info-button" aria-label="关于确认词表" data-tooltip="数字表示词语在文档中出现的次数。勾选后写入项目词表，下次会议用于专业词纠错；不会直接触发保存。">i</button></div><button class="btn btn-primary" id="btnUndWrite" ${terms.length ? "" : "disabled"}>写入项目词表</button></div>`);
  if (terms.length) parts.push(`<div class="selection-toolbar"><span id="termSelectionCount" class="badge" role="status">已选 ${terms.length} / ${terms.length}</span><button type="button" id="btnTermsAll" class="btn btn-ghost btn-sm">全选</button><button type="button" id="btnTermsNone" class="btn btn-ghost btn-sm">清空</button></div>`);
  parts.push('<div class="chips und-chips">' + terms.map((term) =>
    `<label class="chip und-chip selected" title="${esc(term.why || "")}"><input type="checkbox" class="und-pick" data-term="${esc(term.term)}" checked> ${esc(term.term)} <span class="muted">×${esc(term.count || 0)}</span></label>`
  ).join("") + '</div><div id="termsFeedback" hidden></div>');
  if ((result.persons || []).length) parts.push(`<details class="rawterms"><summary>人物 · ${result.persons.length}</summary><div class="chips">${result.persons.map((person) => `<span class="chip">${esc(person.name)} ${badge(`×${person.count}`)}</span>`).join("")}</div></details>`);
  if ((result.events || []).length) parts.push(`<details class="rawterms"><summary>事件摘录 · ${result.events.length}</summary><div class="muted">${result.events.slice(0, 8).map((event) => `<p>${esc(event.what)}${event.quote ? `（原句：${esc(event.quote)}）` : ""}</p>`).join("")}</div></details>`);
  $("undResult").innerHTML = parts.join("");
  $("undResult").hidden = false;
  const inputs = [...$("undResult").querySelectorAll(".und-pick")];
  const updateSelection = () => {
    const count = inputs.filter((input) => input.checked).length;
    inputs.forEach((input) => input.closest("label").classList.toggle("selected", input.checked));
    if ($("termSelectionCount")) $("termSelectionCount").textContent = `已选 ${count} / ${terms.length}`;
    $("btnUndWrite").disabled = count === 0;
    undState.termsWritten = false;
    hideNotice("termsFeedback");
    syncProjectJourney();
  };
  inputs.forEach((input) => input.addEventListener("change", updateSelection));
  if (terms.length) {
    $("btnTermsAll").onclick = () => { inputs.forEach((input) => { input.checked = true; }); updateSelection(); };
    $("btnTermsNone").onclick = () => { inputs.forEach((input) => { input.checked = false; }); updateSelection(); };
  }
  $("btnUndWrite").onclick = async () => {
    const picked = inputs.filter((input) => input.checked).map((input) => input.dataset.term);
    if (!picked.length) return;
    const project = state.project;
    const button = $("btnUndWrite");
    button.disabled = true;
    try {
      const written = await pywebview.api.terms_write(project, picked);
      if (state.project !== project || !button.isConnected) return;
      if (!written.ok) throw new Error(written.error || "写入失败");
      undState.termsWritten = true;
      syncProjectJourney();
      showNotice("termsFeedback", "success", `已写入 ${written.count} 个专业词`, "下次开始会议时自动采用。");
    } catch (e) {
      if (state.project === project && button.isConnected) showNotice("termsFeedback", "error", "词表写入失败", String(e), {run: () => button.onclick()});
    } finally { if (button.isConnected) button.disabled = !inputs.some((input) => input.checked); }
  };
  syncUnderstandButtons();
}
async function planUnderstand() {
  if (!state.project || undState.planning || undState.running || undState.starting) return;
  undState.planning = true;
  undState.plan = null;
  syncUnderstandButtons();
  hideNotice("undFeedback");
  $("undPlanLine").hidden = true;
  showNotice("undProgress", "busy", "正在计算预算");
  try {
    const plan = await pywebview.api.understand_plan(state.project);
    if (!plan.ok) throw new Error(plan.error || "无法读取预算");
    undState.plan = plan;
    $("undProvider").textContent = plan.label || PROVIDER_LABEL[plan.provider] || "本地模型";
    $("undProviderHelp").dataset.tooltip = plan.egress || $("undProviderHelp").dataset.tooltip;
    const chunks = (plan.chunks || []).length;
    let metrics = badge(`${plan.chars || 0} 字`) + badge(`${chunks} 块`);
    if (plan.provider === "local" && Number.isFinite(plan.est_local_minutes)) {
      const minutes = plan.est_local_minutes;
      const duration = minutes >= 90 ? `${(minutes / 60).toFixed(1)} 小时` : `${minutes} 分钟`;
      metrics += badge(`预计约 ${duration}`);
    }
    const notes = plan.notes || [];
    $("undPlanLine").innerHTML = `<div class="metric-row">${metrics}</div>` + (notes.length ? `<details class="rawterms"><summary>预算明细 · ${notes.length}</summary><div class="muted">${notes.map(esc).join("<br>")}</div></details>` : "");
    $("undPlanLine").hidden = false;
    if (!plan.ready) showNotice("undFeedback", "error", "项目理解模型尚未就绪", plan.error || "请检查设置中的模型与服务。", {run: planUnderstand});
  } catch (e) { showNotice("undFeedback", "error", "预算计算失败", String(e), {run: planUnderstand}); }
  finally { undState.planning = false; hideNotice("undProgress"); syncUnderstandButtons(); }
}
async function startUnderstand(maxChunks = 0) {
  if (undState.running || undState.starting || undState.planning) return;
  if (state.running) { showNotice("undFeedback", "warning", "会议进行中", "请先结束会议，再理解项目资料。"); return; }
  if (!undState.plan || !undState.plan.ready) { await planUnderstand(); if (!undState.plan || !undState.plan.ready) return; }
  undState.starting = true;
  undState.maxChunks = maxChunks;
  undState.cancelled = false;
  syncUnderstandButtons();
  hideNotice("undFeedback");
  showNotice("undProgress", "busy", maxChunks ? "正在启动小样试跑" : "正在启动全库理解");
  try {
    const result = await pywebview.api.understand_start(state.project, maxChunks);
    if (!result.ok) throw new Error(result.error || "无法启动理解任务");
    undState.running = true;
    undState.resultReady = false;
    undState.termsWritten = false;
    $("undResult").hidden = true;
    if (undTimer) clearInterval(undTimer);
    undTimer = setInterval(undPoll, 2000);
  } catch (e) {
    hideNotice("undProgress");
    showNotice("undFeedback", "error", "理解任务未能启动", String(e), {run: () => startUnderstand(maxChunks)});
  } finally { undState.starting = false; syncUnderstandButtons(); }
}
$("btnUndPlan").onclick = planUnderstand;
$("btnUndGo").onclick = () => startUnderstand(0);
$("btnUndSmall").onclick = () => startUnderstand(5);
$("btnUndCancel").onclick = async () => {
  $("btnUndCancel").disabled = true;
  try {
    const result = await pywebview.api.understand_cancel();
    if (!result.ok) throw new Error(result.error || "取消失败");
    undState.cancelled = true;
    showNotice("undProgress", "busy", "正在取消理解任务", "已完成块会保留。");
  } catch (e) { showNotice("undFeedback", "error", "无法取消任务", String(e), {run: () => $("btnUndCancel").onclick()}); }
  finally { $("btnUndCancel").disabled = false; }
};

$("btnTreeRefresh").onclick = () => { if (state.project) scanProject(state.project); };
async function testEngine() {
  $("btnTestEngine").disabled = true;
  showNotice("testResult", "busy", "正在检查引擎");
  try {
    const result = await pywebview.api.test_engine();
    showNotice("testResult", result.ok ? "success" : "error", result.ok ? "引擎检查通过" : "引擎检查失败",
      result.ok ? `Python ${result.python} · meetingVAR ${result.meetingvar}` : result.error, result.ok ? null : {run: testEngine});
  } catch (e) { showNotice("testResult", "error", "引擎检查未完成", String(e), {run: testEngine}); }
  finally { $("btnTestEngine").disabled = false; }
}
$("btnTestEngine").onclick = testEngine;
$("btnOpenCfg").onclick = () => pywebview.api.open_config_dir();

/* ---------- 导航/弹窗事件 ---------- */
document.querySelectorAll(".navbtn").forEach((b) => { b.onclick = () => switchPage(b.dataset.page); });
$("btnAskOk").onclick = () => answerAsk($("askInput").value);
$("btnAskSkip").onclick = () => answerAsk("");
$("askInput").addEventListener("keydown", (e) => { if (e.key === "Enter") answerAsk($("askInput").value); });

/* ---------- 启动 ---------- */
async function boot() {
  syncUnderstandButtons();
  const info = await pywebview.api.bootstrap();
  state.mediaBase = info.media_base;
  $("ver").textContent = "v" + info.version;
  setRunning(info.meeting_running, null);
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
