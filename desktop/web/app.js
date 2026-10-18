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
function switchPage(name) {
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
      box.appendChild(kids);
    }
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
  $("undProgress").textContent = st.ok
    ? `✓ 读毕 ${r.chunks_read}/${r.chunks_total} 块 · 覆盖 ${r.covered_chars}/${r.unique_chars} 字（${r.coverage_pct}%）· 缓存命中 ${r.cache_hits} 块`
    : `✗ 中断：${st.error || "未知错误"}（已完成块保留，可续跑）`;
  if (st.ok) renderUnderstandResult(r);
}
function renderUnderstandResult(r) {
  const box = $("undResult");
  const terms = r.terms || [];
  const parts = [];
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
$("btnTestEngine").onclick = async () => {
  $("testResult").textContent = "测试中…";
  const r = await pywebview.api.test_engine();
  $("testResult").textContent = r.ok ? `引擎正常：Python ${r.python} · meetingvar ${r.meetingvar}` : "失败：" + (r.error || "");
};
$("btnOpenCfg").onclick = () => pywebview.api.open_config_dir();

/* ---------- 导航/弹窗事件 ---------- */
document.querySelectorAll(".navbtn").forEach((b) => { b.onclick = () => switchPage(b.dataset.page); });
$("btnAskOk").onclick = () => answerAsk($("askInput").value);
$("btnAskSkip").onclick = () => answerAsk("");
$("askInput").addEventListener("keydown", (e) => { if (e.key === "Enter") answerAsk($("askInput").value); });

/* ---------- 启动 ---------- */
async function boot() {
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
