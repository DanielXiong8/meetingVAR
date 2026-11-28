/* meetingVAR 宣传片 · 60 秒版
 * 与长版共用视觉系统（promo.css + components.css）。
 * 60 秒需要更多切点：10 个镜头，平均 6 秒一个。
 * 全部画面仍是 renderAt(t) 的纯函数。
 */

const DUR = 60;

const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const easeOut = (p) => 1 - Math.pow(1 - p, 3);

function brandbar() {
  return `<div class="brandbar"><img src="assets/logo.png"><div class="wm">meeting<i>VAR</i></div></div>`;
}
function chapterTag(num, name) {
  return `<div class="chapter-tag"><span class="num">${num}</span><span class="bar"></span><span>${name}</span></div>`;
}
function blob(cls, x, y, w, h, rot, o = 1, drift = 14, phase = 0) {
  return `<div class="blob b-${cls}" style="left:${x}px;top:${y}px;width:${w}px;height:${h}px;opacity:${o}"
    data-rot="${rot}" data-drift="${drift}" data-phase="${phase}"></div>`;
}
function lines(arr, base, step = 0.12) {
  return arr
    .map(
      (l, i) =>
        `<span class="line"><span class="li" data-in="${(base + i * step).toFixed(2)}" data-mask="1">${l}</span></span>`
    )
    .join("");
}
function wave(bars = 7, height = 120, redIndex = 1) {
  let h = `<div class="wave" style="height:${height}px" data-wave="1" data-red="${redIndex}">`;
  for (let i = 0; i < bars; i++) h += `<div class="bar${i === redIndex ? " red" : ""}"></div>`;
  return h + `</div>`;
}

const S = [];
const scene = (start, end, html) => S.push({ start, end, html });

/* ---------- 1 · 钩子 A 0.0–4.4 ---------- */
scene(
  0.0,
  4.4,
  `${brandbar()}
   <div class="pad" style="left:64px;top:300px;width:1650px">
     <div class="h1">${lines(["会议里最贵的，", "是那句<em class='em em-red'>没人记得</em>的话。"], 0.1)}</div>
   </div>
   <div class="pad" style="left:66px;top:790px" data-in="1.1">${wave(9, 80, 2)}</div>`
);

/* ---------- 2 · 钩子 B 4.4–8.8 ---------- */
scene(
  4.4,
  8.8,
  `${brandbar()}
   <div class="pad" style="left:64px;top:270px;width:1650px">
     <div class="h1" style="font-size:104px">${lines(["纪要会漏。录音翻不完。", "你还是没法<em class='em em-red'>证明</em>。"], 0.08)}</div>
     <div class="cap" style="margin-top:46px" data-in="0.9">这才是会议记录真正缺的那一层。</div>
   </div>`
);

/* ---------- 3 · 定名 8.8–14.8 ---------- */
scene(
  8.8,
  14.8,
  `${brandbar()}
   ${blob("peach", 1250, 90, 340, 340, -12, 0.9, 20, 0)}
   ${blob("sky", 1570, 350, 280, 280, 16, 0.85, 16, 1.5)}
   <div class="pad" style="left:64px;top:320px">
     <img src="assets/logo.png" style="width:112px;height:112px;border-radius:26px" data-in="0.06">
     <div class="h1" style="margin-top:30px;font-size:126px">meeting<i class="em" style="font-weight:700">VAR</i></div>
     <div class="cap" style="margin-top:22px;font-size:34px" data-in="0.5">本地优先的会议证据层</div>
   </div>`
);

/* ---------- 4 · 只在命中时记录 14.8–21.4 ---------- */
scene(
  14.8,
  21.4,
  `${brandbar()}${chapterTag("01", "capture")}
   ${blob("mint", 1500, 140, 300, 300, 12, 0.75, 16, 0.8)}
   <div class="pad" style="left:64px;top:300px;width:1250px">
     <div class="h1" style="font-size:98px">${lines(["不是一直录音。", "只在<em class='em em-red'>命中</em>时记录。"], 0.2)}</div>
     <div class="cap" style="margin-top:40px" data-in="0.85">命中的才留下，其余不进磁盘。</div>
   </div>`
);

/* ---------- 5 · 证据包 21.4–28.2 ---------- */
scene(
  21.4,
  28.2,
  `${brandbar()}${chapterTag("02", "evidence pack")}
   <div class="pad" style="left:64px;top:250px;width:720px">
     <div class="h2" style="font-size:66px">${lines(["一次命中，", "一个可<em class='em em-red'>校验</em>的包。"], 0.14)}</div>
     <div class="cap" style="margin-top:38px;max-width:640px" data-in="0.8">每个文件一条 SHA256，离线也能验。</div>
   </div>
   <div class="panel" style="left:840px;top:190px;width:1000px" data-in="0.22">
     <div class="panel-head"><span class="dotl"></span><span class="name">evidence_packs/s1_a1b2c3d4</span><span class="right">ZIP</span></div>
     <div class="panel-body">
       ${[
         ["▸", "manifest.json", "1.2 KB"],
         ["▸", "SHA256SUMS.txt", "0.4 KB"],
         ["▸", "audio.wav", "8.4 MB"],
         ["▸", "transcript.txt", "2.3 KB"],
         ["▾", "screens/", "12 帧"],
       ]
         .map(
           ([ico, nm, sz], i) =>
             `<div class="row" data-in="${0.6 + i * 0.14}">
                <span class="ico">${ico}</span><span class="nm">${nm}</span><span class="sz">${sz}</span></div>`
         )
         .join("")}
     </div>
   </div>`
);

/* ---------- 6 · 承诺账本 28.2–34.8 ---------- */
scene(
  28.2,
  34.8,
  `${brandbar()}${chapterTag("03", "ledger")}
   <div class="pad" style="left:64px;top:250px;width:640px">
     <div class="h2" style="font-size:62px">${lines(["谁答应过什么，", "谁<em class='em em-red'>改了口</em>。"], 0.14)}</div>
     <div class="cap" style="margin-top:36px" data-in="0.8">承诺有版本，改动有 diff。</div>
   </div>
   <div class="diffcard" style="left:800px;top:250px;width:940px" data-in="0.3">
     <div class="dhead"><span class="chip red">改口</span><span class="dtitle">周五交付第一版</span></div>
     <div class="drow"><span class="dl">due</span><span class="old">周五</span><span class="arrow">→</span><span class="new">周一</span></div>
     <div class="drow"><span class="dl">body</span><span class="old">上一版</span><span class="arrow">→</span><span class="new">已更新</span></div>
     <div class="dfoot" data-in="1.35">第 2 次会议上变更 · 小林 / 周一</div>
   </div>`
);

/* ---------- 7 · 防篡改 34.8–40.4 ---------- */
scene(
  34.8,
  40.4,
  `${brandbar()}${chapterTag("02", "verify")}
   ${blob("sky", 1520, 190, 280, 280, -8, 0.6, 14, 1.1)}
   <div class="pad" style="left:64px;top:280px;width:1180px">
     <div class="h1" style="font-size:96px">${lines(["改了任何一个字节，", "哈希就<em class='em em-red'>对不上</em>。"], 0.14)}</div>
   </div>
   <div class="panel" style="left:64px;top:760px;width:1000px" data-in="0.85">
     <div class="panel-body" style="padding:20px 30px">
       <div class="row" style="padding:5px 0"><span class="ico">✓</span><span class="nm">manifest.json</span><span class="sz">match</span></div>
       <div class="row" style="padding:5px 0"><span class="ico">✓</span><span class="nm">audio.wav</span><span class="sz">match</span></div>
     </div>
   </div>`
);

/* ---------- 8 · Find Moment 40.4–46.8 ---------- */
scene(
  40.4,
  46.8,
  `${brandbar()}${chapterTag("04", "find moment")}
   <div class="pad" style="left:64px;top:250px;width:1700px">
     <div class="h2" style="font-size:62px">${lines(["30–90 秒的原始片段，", "不是摘要。"], 0.14)}</div>
     <div class="cap" style="margin-top:28px" data-in="0.75">答案精确到秒，带着出处回来。</div>
   </div>
   <div class="resultcard" style="left:64px;top:640px;width:1700px" data-in="0.78">
     <div class="rtop">
       <span class="chip lime">命中</span>
       <span class="rtime">00:41:12 → 00:41:48</span>
       <span class="chip grey">小林 · speaker 2</span>
       <span class="why" style="margin-left:auto;font-size:16px">why：命中「周五」「交付」</span>
     </div>
     <div class="rquote">“这版周五能交付，我先发第一版。”</div>
   </div>`
);

/* ---------- 9 · 边界 46.8–52.4 ---------- */
scene(
  46.8,
  52.4,
  `${brandbar()}${chapterTag("05", "privacy")}
   ${blob("butter", 1440, 170, 300, 300, 14, 0.7, 15, 1.2)}
   <div class="pad" style="left:64px;top:300px;width:1300px">
     <div class="h1" style="font-size:96px">${lines(["全部在本机，", "默认<em class='em em-red'>不联网</em>。"], 0.14)}</div>
     <div class="cap" style="margin-top:42px;max-width:920px" data-in="0.8">
       本地 ASR、本地模型、本地证据。只存命中的片段，磁盘约省 90%<span class="cap-sm" style="font-size:20px">（估算）</span>。
     </div>
   </div>`
);

/* ---------- 10 · 落幕 52.4–60.0 ---------- */
scene(
  52.4,
  60.0,
  `${brandbar()}
   ${blob("lilac", 1480, 120, 300, 300, -12, 0.62, 14, 2.0)}
   <div class="pad" style="left:64px;top:250px;width:1500px">
     <div class="h1" style="font-size:96px">${lines(["别人帮你记下来。", "meetingVAR 帮你<em class='em em-red'>证</em>出来。"], 0.12, 0.16)}</div>
     <div class="cta-row" style="margin-top:48px">
       <div class="cta solid" data-in="0.8">本地演示</div>
       <div class="cta ghost" data-in="0.9">离线可跑</div>
       <div class="cap-sm" style="margin-left:20px" data-in="1.05">SDG Open Hack 2026 · Hack16 · Lenovo</div>
     </div>
   </div>`
);

/* ---------------- renderer（与长版一致） ---------------- */
const CHAPTERS = [0, 4.4, 8.8, 14.8, 21.4, 28.2, 34.8, 40.4, 46.8, 52.4];

const stage = document.getElementById("stage");
const pbarFill = document.querySelector(".pbar .pfill");
const pbar = document.querySelector(".pbar");

CHAPTERS.forEach((c) => {
  if (c <= 0 || !pbar) return;
  const tick = document.createElement("div");
  tick.className = "ptick";
  tick.style.left = (c / DUR) * 100 + "%";
  pbar.appendChild(tick);
});

const nodes = S.map((s) => {
  const el = document.createElement("div");
  el.className = "scene";
  el.innerHTML = s.html;
  stage.appendChild(el);
  const anim = Array.from(el.querySelectorAll("[data-in]")).map((n) => ({
    n,
    at: parseFloat(n.dataset.in) || 0,
    dur: parseFloat(n.dataset.dur) || 0.55,
    mask: n.dataset.mask === "1",
    y: n.dataset.y ? parseFloat(n.dataset.y) : 26,
  }));
  return { ...s, el, anim, wasOn: false };
});

function setBlobs(el, t) {
  el.querySelectorAll(".blob").forEach((b) => {
    const rot = parseFloat(b.dataset.rot) || 0;
    const amp = parseFloat(b.dataset.drift) || 12;
    const ph = parseFloat(b.dataset.phase) || 0;
    const dx = Math.sin(t * 0.32 + ph) * amp;
    const dy = Math.cos(t * 0.26 + ph * 1.3) * amp * 0.8;
    const rz = rot + Math.sin(t * 0.19 + ph) * 3;
    b.style.transform = `translate(${dx.toFixed(2)}px,${dy.toFixed(2)}px) rotate(${rz.toFixed(2)}deg)`;
  });
}

function setWave(el, t) {
  const w = el.querySelector("[data-wave]");
  if (!w) return;
  const red = parseInt(w.dataset.red, 10);
  const H = parseFloat(w.style.height) || 100;
  w.querySelectorAll(".bar").forEach((b, i) => {
    const amp = i === red ? 1 : 0.55 + 0.35 * Math.abs(Math.sin(i * 1.7));
    const v = 0.18 + 0.82 * Math.abs(Math.sin(t * 2.6 + i * 0.62)) * amp;
    b.style.height = (H * clamp(v, 0.12, 1)).toFixed(1) + "px";
  });
}

function renderAt(t) {
  if (pbarFill) pbarFill.style.width = (clamp(t / DUR) * 100).toFixed(3) + "%";

  nodes.forEach((s) => {
    const on = t >= s.start && t < s.end;
    if (on !== s.wasOn) {
      s.el.classList.toggle("on", on);
      s.wasOn = on;
    }
    if (!on) return;

    const lt = t - s.start;
    const camK = clamp(lt / Math.max(0.001, s.end - s.start));
    s.el.style.transform = `scale(${(1 + 0.016 * camK).toFixed(5)})`;

    // 切点即到位：主文案跟镜头一起"硬切"进来，避免每次换镜都闪一次空白
    const sceneIn = clamp(lt / 0.18);

    for (const a of s.anim) {
      const p = clamp((lt - a.at) / a.dur);
      const e0 = easeOut(p);
      const e = a.at <= 0.32 ? Math.max(e0, sceneIn) : e0;
      a.n.style.opacity = String(e);
      a.n.style.transform = a.mask
        ? `translateY(${((1 - e) * 30).toFixed(2)}%)`
        : `translateY(${((1 - e) * a.y).toFixed(2)}px)`;
    }

    setBlobs(s.el, t);
    setWave(s.el, t);
  });
}

async function boot() {
  const gaps = [];
  let prev = 0;
  S.slice()
    .sort((a, b) => a.start - b.start)
    .forEach((s) => {
      if (s.start - prev > 0.01) gaps.push([+prev.toFixed(2), s.start]);
      prev = Math.max(prev, s.end);
    });
  if (prev < DUR) gaps.push([+prev.toFixed(2), DUR]);
  window.__gaps = gaps;

  await document.fonts.ready;
  renderAt(0);
  window.renderAt = renderAt;
  window.DURATION = DUR;
  window.__ready = true;
}
boot();