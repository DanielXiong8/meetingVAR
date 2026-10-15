/* meetingVAR 桌面壳前端逻辑：轮询事件流 + 页面渲染 + 点名弹窗 */
"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  next: 0, running: false, startedAt: null, hits: 0,
  speakers: new Set(), project: "", scan: null, mediaBase: "",
  askQueue: [], askOpen: false, ticker: [],
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
  let res;
  try { res = await pywebview.api.poll_events(state.next); }
  catch (e) { return; }
  state.next = res.next;
  for (const ev of res.events) handleEvent(ev);
  if (res.running !== state.running) setRunning(res.running, res.started_at);
}

const SYS_LINE_RE = /\[rec\]|\[go\]|\[done\]|\[spk\]|\[calibration\]|归档|停止指令|就绪|Traceback|Error|error/i;

function handleEvent(ev) {
  if (ev.type === "line") {
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
  } else if (ev.type === "started") {
    setRunning(true, Date.now() / 1000);
  } else if (ev.type === "exit") {
    setRunning(false, null);
    let msg = "会议已结束（引擎退出码 " + ev.code + "）";
    if (ev.code === 0) msg = "会议已结束（正常收尾，纪要与证据已保存）";
    else if (ev.last_log) msg += " · 最后一条：" + ev.last_log.slice(0, 110);
    toast(msg, 9000);
    pushTicker("", "系统", msg, ev.code === 0 ? "sys" : "err");
  }
}

function pushTicker(t, spk, text, kind) {
  state.ticker.push({ t, spk, text, kind });
  if (state.ticker.length > 60) state.ticker.shift();
  const body = $("ticker");
  const div = document.createElement("div");
  div.className = "tk" + (kind ? " " + kind : "");
  div.innerHTML = `<span class="t">${esc(t)}</span><span class="s">${esc(spk)}</span>${esc(text)}`;
  body.appendChild(div);
  while (body.children.length > 14) body.removeChild(body.firstChild);
  body.scrollTop = body.scrollHeight;
}

/* ---------- 运行状态 ---------- */
function setRunning(running, startedAt) {
  state.running = running;
  if (startedAt) state.startedAt = startedAt;
  const pill = $("statusPill");
  pill.textContent = running ? "● 会议中" : "● 待机";
  pill.classList.toggle("live", running);
  $("recLabel").textContent = running ? "结束会议" : "开始会议";
  $("btnRec").classList.toggle("live", running);
  if (!running) {
    $("statTime").textContent = "--";
  }
}
setInterval(() => {
  if (state.running && state.startedAt) {
    $("statTime").textContent = "已听 " + fmtClock(Date.now() / 1000 - state.startedAt);
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
  if (state.running) {
    const r = await pywebview.api.stop_meeting();
    if (!r.ok) toast("停止失败：" + (r.error || ""));
    return;
  }
  if (!state.project) { toast("还没有选项目——先去「项目」页选一个文件夹"); switchPage("project"); return; }
  const r = await pywebview.api.start_meeting(state.project, "go");
  if (!r.ok) { toast("启动失败：" + (r.error || "")); return; }
  state.hits = 0; state.speakers.clear(); state.ticker = [];
  $("ticker").innerHTML = ""; $("statHits").textContent = "命中 0";
  $("statSpeakers").textContent = "说话人 —";
  setRunning(true, r.started_at);
  pushTicker("", "系统", "正在启动引擎（首次加载模型约 40 秒）…", "sys");
  toast("会议开始：" + state.project);
};
$("btnVar").onclick = async () => {
  const r = await pywebview.api.manual_var();
  toast(r.ok ? "已标记（手动 VAR：回溯 300s）" : "会议未在运行");
};
$("btnOpenOut").onclick = () => pywebview.api.open_path("");

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
  let meta = `文档 ${d.doc_count} 份 · 监听词 ${d.term_count} 个` +
    (d.repo && d.repo.is_repo ? ` · 代码符号 ${d.repo.terms} 个` : "");
  if (d.skipped && d.skipped.length) {
    meta += ` · 跳过 ${d.skipped.length} 个非文本文件（${d.skipped.map(esc).join("、")}）`;
  }
  $("scanMeta").textContent = meta;
  const chips = (obj, n = 20) => Object.keys(obj).slice(0, n)
    .map((w) => `<span class="chip"><b>${esc(w)}</b></span>`).join("");
  let html = "";
  if (d.terms && Object.keys(d.terms).length) html += `<h3>抽取的监听词（基础层）</h3><div class="chips">${chips(d.terms)}</div>`;
  if (d.repo && d.repo.is_repo && Object.keys(d.repo.top || {}).length) html += `<h3>代码符号（L0）</h3><div class="chips">${chips(d.repo.top)}</div>`;
  if (d.persons && d.persons.length) html += `<h3>人物</h3><div class="chips">${d.persons.map((p) => `<span class="chip">${esc(p)}</span>`).join("")}</div>`;
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
  const clips = d.clips.map((c) => `
    <div class="clip">
      <div class="head"><span class="layer">${esc(c.layer)}</span><span>${esc(c.evidence)}</span>
        <span class="muted">@${fmtClock(c.at)} · ${c.lines} 句</span></div>
      ${c.audio_url ? `<audio controls preload="none" src="${esc(c.audio_url)}"></audio>` : ""}
      ${c.hd_url ? `<video controls preload="none" src="${esc(c.hd_url)}"></video>` : ""}
    </div>`).join("");
  $("sessionDetail").innerHTML =
    `<h2>${esc(sid)}</h2>` +
    `<div class="md">${d.minutes ? mdToHtml(d.minutes) : "<p class='muted'>无薄纪要文件</p>"}</div>` +
    (clips ? `<h2>证据片段</h2>${clips}` : "") +
    `<details><summary class="muted">厚纪要（VAR 报告原文）</summary><div class="md">${d.reports ? mdToHtml(d.reports) : "<p class='muted'>无</p>"}</div></details>`;
  $("sessionDetail").hidden = false;
  $("sessionDetail").scrollIntoView({ behavior: "smooth" });
}

/* ---------- 设置 + LLM 服务 ---------- */
async function refreshLlm() {
  const st = await pywebview.api.llm_status();
  $("llmStatus").textContent = st.up
    ? `● 运行中（${st.model || "模型已载入"}）`
    : (st.managed ? "○ 启动中…（首次加载约 30-60 秒）" : "○ 未运行");
  $("llmPill").textContent = st.up ? "LLM ●" : "LLM ○";
  $("llmPill").classList.toggle("on", !!st.up);
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
$("cfgUseLlm").onchange = async () => {
  await pywebview.api.set_config({ use_llm: $("cfgUseLlm").checked });
  toast($("cfgUseLlm").checked ? "开会时将叠加 LLM 语义层" : "已关闭 LLM 语义层");
};
async function loadConfig() {
  const cfg = await pywebview.api.get_config();
  $("cfgPy").value = cfg.engine_python || "";
  $("cfgRepo").value = cfg.repo_dir || "";
  $("cfgDevice").value = cfg.device || "cuda";
  $("cfgGguf").value = cfg.llm_gguf || "";
  $("cfgUseLlm").checked = !!cfg.use_llm;
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
  });
  toast("配置已保存");
};
$("btnTreeRefresh").onclick = () => { if (state.project) scanProject(state.project); };
$("btnTestEngine").onclick = async () => {
  $("testResult").textContent = "测试中…";
  const r = await pywebview.api.test_engine();
  $("testResult").textContent = r.ok ? `引擎正常：Python ${r.python} · meetingvar ${r.meetingvar}` : "失败：" + (r.error || "");
};
$("btnOpenCfg").onclick = () => pywebview.api.open_path("");

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
