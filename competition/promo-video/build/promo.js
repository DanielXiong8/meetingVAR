/* meetingVAR · 宣传片场景定义
 * 全部画面都是 renderAt(t) 的纯函数，无 CSS transition / rAF，
 * 保证逐帧渲染可复现。
 */

const DUR = 236;

/* ---------------- helpers ---------------- */
const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const easeOut = (p) => 1 - Math.pow(1 - p, 3);
const easeInOut = (p) => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2);

function brandbar() {
  return `<div class="brandbar"><img src="assets/logo.png"><div class="wm">meeting<i>VAR</i></div></div>`;
}

function chapterTag(num, name) {
  return `<div class="chapter-tag"><span class="num">${num}</span><span class="bar"></span><span>${name}</span></div>`;
}

function blob(cls, x, y, w, h, rot, o = 1, drift = 16, phase = 0) {
  return `<div class="blob b-${cls}" style="left:${x}px;top:${y}px;width:${w}px;height:${h}px;opacity:${o}"
    data-rot="${rot}" data-drift="${drift}" data-phase="${phase}"></div>`;
}

function lines(arr, base, step = 0.13) {
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

/* ---------------- scenes ---------------- */
const S = [];
const scene = (start, end, html, hook, bg) => S.push({ start, end, html, hook, bg });

/* ============ CH1 · Introduction ============ */
scene(
  0,
  7.2,
  `${brandbar()}
   <div class="pad" style="left:64px;top:300px;width:1500px">
     <div class="kicker" data-in="0.25">meeting evidence layer</div>
     <div class="h1" style="margin-top:34px">${lines(["会议里最贵的，", "是那句<em class='em em-red'>没人记得</em>的话。"], 0.5)}</div>
   </div>
   <div class="pad" style="left:66px;top:760px" data-in="1.9">${wave(9, 96, 2)}</div>
   <div class="pad cap-sm" style="right:64px;bottom:70px" data-in="2.4">meetingVAR · 2026</div>`
);

scene(
  7.2,
  15.2,
  `${brandbar()}
   ${blob("peach", 1210, 80, 380, 380, -12, 0.92, 22, 0)}
   ${blob("sky", 1590, 330, 300, 300, 16, 0.86, 18, 1.4)}
   ${blob("butter", 1180, 500, 190, 190, 30, 0.62, 14, 2.6)}
   <div class="pad" style="left:64px;top:300px">
     <img src="assets/logo.png" style="width:126px;height:126px;border-radius:30px" data-in="0.15">
     <div class="h1" style="margin-top:38px;font-size:132px">meeting<i class="em" style="font-weight:700">VAR</i></div>
     <div class="cap" style="margin-top:26px;font-size:36px" data-in="0.7">本地优先的会议证据层 · personal evidence agent</div>
     <div class="cap-sm" style="margin-top:14px" data-in="0.95">只在内容命中时记录。记下来的，可以验证。</div>
   </div>`
);

scene(
  15.2,
  23.2,
  `${brandbar()}
   <div class="pad" style="left:64px;top:250px;width:1300px">
     <div class="h1">${lines(["纪要会漏。", "录音翻不完。"], 0.15)}</div>
     <div class="h1" style="margin-top:56px;font-size:104px" data-in="0.75">真正的问题是 —— <em class="em em-red">你没法证明。</em></div>
   </div>
   <div class="pad" style="right:64px;bottom:64px;text-align:right" data-in="1.3">
     <div class="cap-sm">摘要、纪要、转写工具已经够多了。</div>
   </div>`
);

scene(
  23.2,
  35.4,
  `${brandbar()}
   ${blob("mint", 1450, 120, 330, 330, 8, 0.85, 18, 0.7)}
   ${blob("lilac", 1230, 470, 250, 250, -18, 0.8, 15, 2.1)}
   <div class="pad" style="left:64px;top:250px;width:1240px">
     <div class="kicker" data-in="0.1">所以，换一个方向</div>
     <div class="h2" style="margin-top:30px">${lines(["把每一句<em class='em'>承诺</em>，", "变回<em class='em em-red'>证据</em>。"], 0.35)}</div>
     <div class="agenda" style="margin-top:66px">
       <div class="item" data-in="0.9">证据层</div>
       <div class="item" data-in="1.02">Evidence Pack</div>
       <div class="item" data-in="1.14">承诺账本</div>
       <div class="item" data-in="1.26">Find Moment</div>
     </div>
     <div class="cap" style="margin-top:52px;max-width:820px" data-in="1.5">接下来 4 分钟，不吹概念，只走一遍真实流程。</div>
   </div>`
);

/* ============ CH2 · 证据层 ============ */
scene(
  35.4,
  43.0,
  `${brandbar()}${chapterTag("01", "capture")}
   ${blob("peach", 1330, 110, 340, 340, -10, 0.92, 20, 0.3)}
   ${blob("sky", 1530, 400, 250, 250, 22, 0.85, 16, 1.8)}
   <div class="pad" style="left:64px;top:270px;width:1060px">
     <div class="kicker" data-in="0.15">01 / 证据层</div>
     <div class="h1" style="margin-top:30px;font-size:100px">${lines(["不是一直录音。", "只在<em class='em em-red'>命中</em>时记录。"], 0.4)}</div>
     <div class="cap" style="margin-top:44px;max-width:760px" data-in="1.15">命中的内容才留下，其余不进磁盘。</div>
   </div>
   <div class="shot-frame" style="left:1150px;top:648px;width:850px;height:404px;transform:rotate(-1.6deg)" data-in="0.9">
     <div class="inner" style="background-image:url('assets/workspace_next.png');background-size:1920px 1080px;background-position:-330px -700px"></div>
   </div>`
);

scene(
  43.0,
  50.5,
  `${brandbar()}${chapterTag("01", "workspace")}
   ${blob("peach", 1330, 60, 300, 300, -10, 0.55, 20, 0.4)}
   <div class="pad" style="left:64px;top:250px;width:560px">
     <div class="h2" style="font-size:66px">${lines(["一个工作区，", "装下所有证据。"], 0.2)}</div>
     <div class="cap" style="margin-top:36px" data-in="0.95">问一场会，答案带着出处回来。</div>
   </div>
   <div class="shot-frame" style="left:640px;top:200px;width:1180px;height:664px" data-in="0.4">
     <div class="inner" style="background-image:url('assets/workspace_next.png');background-size:1180px 664px"></div>
   </div>
   <div class="pill" style="left:560px;top:770px" data-in="1.5"><span class="dot"></span>本地模型</div>
   <div class="pill" style="left:1530px;top:300px" data-in="1.9"><span class="dot"></span>离线可查</div>`
);

scene(
  50.5,
  60.0,
  `${brandbar()}
   <div class="pad" style="left:64px;top:300px;width:620px">
     <div class="kicker" data-in="0.1">三路同时</div>
     <div class="h2" style="margin-top:26px;font-size:76px">${lines(["声音、屏幕、", "对齐一条时间轴。"], 0.35)}</div>
     <div class="cap" style="margin-top:38px" data-in="1.0">屏幕帧按秒采样，和转写落在同一个时间点。</div>
   </div>
   <div class="stack" style="left:880px;top:300px">
     ${["系统音频 · 会议软件里的声音", "麦克风 · 房间里说的话", "屏幕帧 · 共享的那块窗口"]
       .map(
         (t, i) => `<div class="src-card" data-in="${0.5 + i * 0.22}">
        <div class="ico">${["◉", "◍", "▦"][i]}</div>
        <div><div class="tt">${t.split(" · ")[0]}</div><div class="ss">${t.split(" · ")[1]}</div></div>
      </div>`
       )
       .join("")}
   </div>`
);

scene(
  60.0,
  66.5,
  `${brandbar()}
   ${blob("butter", 1400, 150, 300, 300, 14, 0.8, 16, 1.2)}
   <div class="pad" style="left:64px;top:372px;width:1380px">
     <div class="h1">${lines(["命中的，<em class='em'>留下来</em>。", "没命中的，<em class='em em-red'>不进磁盘</em>。"], 0.2, 0.16)}</div>
   </div>
   <div class="pad" style="left:64px;top:800px" data-in="1.3">${wave(9, 84, 3)}</div>`
);

scene(
  66.5,
  72.6,
  `${brandbar()}
   <div class="pad" style="left:64px;top:310px;width:760px">
     <div class="kicker" data-in="0.1">兴趣清单</div>
     <div class="h2" style="margin-top:26px;font-size:66px">${lines(["你想留下什么，", "由你决定。"], 0.35)}</div>
     <div class="cap" style="margin-top:36px;max-width:640px" data-in="1.1">判定看内容，不看关键词。清单随时可改。</div>
   </div>
   <div class="interest" style="left:880px;top:290px" data-in="0.5">
     <div class="ih">项目兴趣 · 某项目</div>
     ${[
       ["客户 A 什么时候交付第一版", "命中", true],
       ["测试风险和阻塞项", "命中", true],
       ["预算和续约价格", "命中", true],
       ["团建去哪吃饭", "忽略", false],
     ]
       .map(
         ([t, m, hit], i) => `<div class="irow ${hit ? "hit" : "miss"}" data-in="${0.62 + i * 0.16}">
        <span class="iname">${t}</span><span class="im">${hit ? "✓ " + m : "— " + m}</span></div>`
       )
       .join("")}
   </div>`
);

/* ============ CH3 · Evidence Pack ============ */
scene(
  72.6,
  80.0,
  `${brandbar()}${chapterTag("02", "evidence pack")}
   ${blob("sky", 1360, 120, 360, 360, -8, 0.9, 18, 0.5)}
   ${blob("peach", 1560, 420, 260, 260, 18, 0.82, 14, 2.2)}
   <div class="pad" style="left:64px;top:280px;width:1100px">
     <div class="kicker" data-in="0.15">02 / evidence pack</div>
     <div class="h1" style="margin-top:30px;font-size:100px">${lines(["一次命中，", "一个可<em class='em em-red'>校验</em>的包。"], 0.4)}</div>
     <div class="cap" style="margin-top:44px" data-in="1.1">ZIP + manifest + SHA256。离线可验。</div>
   </div>`
);

scene(
  80.0,
  91.0,
  `${brandbar()}${chapterTag("02", "evidence pack")}
   <div class="pad" style="left:64px;top:250px;width:760px">
     <div class="h2" style="font-size:64px">${lines(["证据包里，", "只有这一段的原件。"], 0.2)}</div>
     <div class="cap" style="margin-top:40px;max-width:660px" data-in="0.9">只复制已保存的片段，不会重新录制，也不会带上别的会议。</div>
   </div>
   <div class="panel" style="left:940px;top:200px;width:880px" data-in="0.35">
     <div class="panel-head"><span class="dotl"></span><span class="name">evidence_packs/s1_a1b2c3d4</span><span class="right">ZIP</span></div>
     <div class="panel-body">
       ${[
         ["▸", "manifest.json", "1.2 KB", false],
         ["▸", "SHA256SUMS.txt", "0.4 KB", false],
         ["▸", "summary.md", "3.1 KB", false],
         ["▸", "summary.html", "6.8 KB", false],
         ["▸", "audio.wav", "8.4 MB", false],
         ["▸", "transcript.txt", "2.3 KB", false],
         ["▾", "screens/", "", false],
         ["", "frame_0001.jpg", "184 KB", true],
         ["", "frame_0002.jpg", "179 KB", true],
         ["", "frame_0003.jpg", "181 KB", true],
       ]
         .map(
           ([ico, nm, sz, ind], i) =>
             `<div class="row${ind ? " indent" : ""}" data-in="${0.9 + i * 0.13}">
                <span class="ico">${ico}</span><span class="nm">${nm}</span><span class="sz">${sz}</span></div>`
         )
         .join("")}
     </div>
   </div>`
);

scene(
  91.0,
  100.0,
  `${brandbar()}${chapterTag("02", "manifest")}
   <div class="pad" style="left:64px;top:250px;width:700px">
     <div class="h2" style="font-size:64px">${lines(["每个文件，", "一条 SHA256。"], 0.2)}</div>
     <div class="cap" style="margin-top:40px;max-width:620px" data-in="1.0">哈希写在 manifest 里，也单独存成 SHA256SUMS.txt。</div>
   </div>
   <div class="panel" style="left:840px;top:210px;width:1000px" data-in="0.3">
     <div class="panel-head"><span class="dotl"></span><span class="name">manifest.json</span><span class="right">read-only</span></div>
     <div class="panel-body json">
       ${[
         ["p", "{"],
         ['k', '  "session_id"', '"s1"'],
         ['k', '  "clip_id"', '"a1b2c3d4"'],
         ['k', '  "trigger"', '"命中：交付时间"'],
         ['k', '  "window"', '"00:40:58 → 00:41:46"'],
         ['k', '  "consent"', '"local-capture"'],
         ["p", '  "files": ['],
         ['s', '    {"name": "audio.wav", "sha256": "9f2c1d…a41d"},'],
         ['s', '    {"name": "transcript.txt", "sha256": "b71e04…c8f2"},'],
         ['s', '    {"name": "frame_0001.jpg", "sha256": "4d90ab…17ee"}'],
         ["p", "  ],"],
         ['k', '  "generator"', '"meetingVAR 0.17.0"'],
         ["p", "}"],
       ]
         .map(([cls, k, v], i) => {
           const line =
             cls === "p"
               ? `<span class="p">${k}</span>`
               : cls === "k"
               ? `<span class="k">${k}</span><span class="p">: </span><span class="s">${v}</span><span class="p">,</span>`
               : `<span class="s">${k}</span>`;
           return `<div data-in="${0.8 + i * 0.11}">${line}</div>`;
         })
         .join("")}
     </div>
   </div>`
);

scene(
  100.0,
  108.6,
  `${brandbar()}${chapterTag("02", "verify")}
   <div class="pad" style="left:64px;top:316px;width:1500px">
     <div class="h1" style="font-size:104px">${lines(["改了任何一个字节，", "哈希就<em class='em em-red'>对不上</em>。"], 0.2)}</div>
   </div>
   <div class="cap" style="position:absolute;left:64px;top:580px;width:1500px" data-in="1.4">逐个文件重新计算，和 SHA256SUMS.txt 比对 —— 断网也能验。</div>
   <div class="panel" style="left:64px;top:680px;width:1000px" data-in="1.1">
     <div class="panel-body" style="padding:24px 30px">
       <div class="row" style="padding:6px 0"><span class="ico">✓</span><span class="nm">manifest.json</span><span class="sz">match</span></div>
       <div class="row" style="padding:6px 0"><span class="ico">✓</span><span class="nm">audio.wav</span><span class="sz">match</span></div>
       <div class="row" style="padding:6px 0"><span class="ico">✓</span><span class="nm">transcript.txt</span><span class="sz">match</span></div>
     </div>
   </div>`
);

/* ============ CH4 · 承诺账本 ============ */
scene(
  108.6,
  116.0,
  `${brandbar()}${chapterTag("03", "commitment ledger")}
   ${blob("lilac", 1350, 130, 350, 350, -14, 0.88, 18, 0.9)}
   ${blob("mint", 1560, 430, 240, 240, 24, 0.8, 14, 2.4)}
   <div class="pad" style="left:64px;top:280px;width:1100px">
     <div class="kicker" data-in="0.15">03 / commitment ledger</div>
     <div class="h1" style="margin-top:30px;font-size:100px">${lines(["谁答应过什么，", "谁<em class='em em-red'>改了口</em>。"], 0.4)}</div>
     <div class="cap" style="margin-top:44px" data-in="1.1">承诺有版本，改动有 diff。</div>
   </div>`
);

scene(
  116.0,
  126.0,
  `${brandbar()}${chapterTag("03", "ledger")}
   <div class="panel" style="left:360px;top:290px;width:1200px" data-in="0.3">
     <div class="panel-head"><span class="dotl"></span><span class="name">承诺账本</span><span class="right">s1 · 3 条</span></div>
     <div class="panel-body" style="padding:10px 0">
       ${[
         ["active", "周五交付第一版", "小林", "周一", 1],
         ["active", "补齐测试报告", "阿岚", "周三", 0],
         ["superseded", "先做导入，不做导出", "老周", "已过期", 0],
       ]
         .map(
           ([st, title, owner, due, hot], i) => `<div class="lrow${hot ? " hot" : ""}" data-in="${0.8 + i * 0.25}">
        <span class="chip ${st === "active" ? "lime" : "grey"}">[${st}]</span>
        <span class="lt">${title}</span>
        <span class="lo">${owner}</span>
        <span class="ld">${due}</span>
      </div>`
         )
         .join("")}
     </div>
   </div>
   <div class="cap" style="position:absolute;left:360px;top:690px;width:1200px" data-in="1.9">状态取 FactStore：active / superseded / expired / pending-conflict</div>`
);

scene(
  126.0,
  135.0,
  `${brandbar()}${chapterTag("03", "diff")}
   <div class="pad" style="left:64px;top:296px;width:640px">
     <div class="h2" style="font-size:62px">${lines(["改口不是八卦，", "是<em class='em'>状态变更</em>。"], 0.2)}</div>
     <div class="cap" style="margin-top:38px" data-in="1.0">due 和正文各留一版，谁在什么时候改的，都查得到。</div>
   </div>
   <div class="diffcard" data-in="0.5" style="left:820px;top:290px;width:800px">
     <div class="dhead"><span class="chip red">改口</span><span class="dtitle">周五交付第一版</span></div>
     <div class="drow"><span class="dl">due</span><span class="old">周五</span><span class="arrow">→</span><span class="new">周一</span></div>
     <div class="drow"><span class="dl">body</span><span class="old">上一版</span><span class="arrow">→</span><span class="new">已更新</span></div>
     <div class="dfoot" data-in="2.2">第 2 次会议上变更 · 08 月 12 日</div>
   </div>
   <div class="vtimeline" style="left:820px;top:700px" data-in="2.6">
     <div class="vnode"><span class="vd">08-05 · v1</span><span class="vt">第 1 次会议 · 周五</span></div>
     <div class="vlink"></div>
     <div class="vnode hot"><span class="vd">08-12 · v2</span><span class="vt">第 2 次会议 · 周一</span></div>
   </div>`
);

scene(
  135.0,
  143.6,
  `${brandbar()}${chapterTag("03", "original quote")}
   <div class="pad" style="left:64px;top:300px;width:900px">
     <div class="h2" style="font-size:60px">${lines(["点一下，", "回到<em class='em em-red'>那句话</em>。"], 0.2)}</div>
     <div class="cap" style="margin-top:36px;max-width:720px" data-in="1.0">每条承诺都挂着原话的 session、时间戳和 turn_id。</div>
   </div>
   <div class="quotecard" style="left:1000px;top:290px;width:840px" data-in="0.6">
     <div class="qtop"><span class="play">▶</span><span class="qt">00:12:40</span><span class="chip grey">speaker 2</span></div>
     <div class="qbody">“这版周五能交付，我先发第一版，测试报告周三补。”</div>
     <div class="qfoot"><span class="mono">turn_id 1284</span><span class="mono">某项目 / 周会</span></div>
   </div>`
);

/* ============ CH5 · Find Moment ============ */
scene(
  143.6,
  151.0,
  `${brandbar()}${chapterTag("04", "find moment")}
   ${blob("peach", 1370, 140, 340, 340, 10, 0.88, 18, 1.1)}
   ${blob("sky", 1580, 420, 230, 230, -20, 0.8, 15, 2.9)}
   <div class="pad" style="left:64px;top:280px;width:1140px">
     <div class="kicker" data-in="0.15">04 / find moment</div>
     <div class="h1" style="margin-top:30px;font-size:96px">${lines(["上次说的交付时间，", "是<em class='em em-red'>哪一段</em>？"], 0.4)}</div>
     <div class="cap" style="margin-top:44px" data-in="1.1">30–90 秒的原始片段，不是摘要。</div>
   </div>`
);

scene(
  151.0,
  162.0,
  `${brandbar()}${chapterTag("04", "query")}
   <div class="querybar" style="left:64px;top:210px;width:1290px" data-in="0.25">
     <span class="qico">⌕</span><span class="qtext" data-typed="谁负责周五交付？">谁负责周五交付？</span>
   </div>
   <div class="resultcard" style="left:64px;top:340px;width:1290px" data-in="1.15">
     <div class="rtop">
       <span class="chip lime">命中</span>
       <span class="rtime">00:41:12 → 00:41:48</span>
       <span class="chip grey">小林 · speaker 2</span>
     </div>
     <div class="rquote">“这版周五能交付，我先发第一版。”</div>
     <div class="rfoot">
       <span class="why">why：命中「周五」「交付」</span>
       <span class="mono">turn_ids 1284–1289 · clip a1b2c3d4</span>
     </div>
   </div>
   <div class="cap-sm" style="position:absolute;left:64px;top:640px" data-in="2.0">检索顺序：RAG 时间戳 → 会话关键词 → 邻近片段。</div>`
);

scene(
  162.0,
  173.6,
  `${brandbar()}${chapterTag("04", "playback")}
   ${blob("mint", 1440, 120, 300, 300, 12, 0.78, 16, 1.6)}
   <div class="pad" style="left:64px;top:200px;width:860px">
     <div class="h2" style="font-size:62px">${lines(["36 秒，", "就是那段原话。"], 0.2)}</div>
     <div class="cap" style="margin-top:36px" data-in="0.9">音频从片段池直接取出，不重新合成。</div>
   </div>
   <div class="player" style="left:64px;top:560px;width:1700px" data-in="0.55">
     <div class="ptime"><span class="mono" data-clock="1">00:41:12</span><span class="mono dim">/ 00:41:48</span></div>
     <div class="ptrack" data-track="1">
       ${Array.from({ length: 64 }, (_, i) => `<i data-h="${[18, 34, 56, 78, 94, 66, 42, 88, 52, 30][i % 10]}"></i>`).join("")}
       <div class="phead" data-head="1"></div>
     </div>
   </div>`
);

/* ============ CH6 · 边界 ============ */
scene(
  173.6,
  181.0,
  `${brandbar()}${chapterTag("05", "privacy")}
   ${blob("sky", 1380, 120, 340, 340, -10, 0.85, 18, 0.4)}
   ${blob("mint", 1590, 410, 220, 220, 20, 0.78, 14, 2.0)}
   <div class="pad" style="left:64px;top:280px;width:1100px">
     <div class="kicker" data-in="0.15">05 / 边界</div>
     <div class="h1" style="margin-top:30px;font-size:100px">${lines(["全部在本机。", "默认<em class='em em-red'>不联网</em>。"], 0.4)}</div>
     <div class="cap" style="margin-top:44px" data-in="1.1">本地 ASR、本地模型、本地证据。</div>
   </div>`
);

scene(
  181.0,
  188.0,
  `${brandbar()}${chapterTag("05", "local")}
   <div class="pad" style="left:64px;top:230px;width:700px">
     <div class="h2" style="font-size:62px">${lines(["本地优先，", "不是本地说说。"], 0.2)}</div>
   </div>
   <div class="stack" style="left:880px;top:230px">
     ${[
       ["离线可用", "断网也能转写、检索、校验"],
       ["不出本机", "证据和音频不经过云端"],
       ["可选云端", "愿意的话，ASR 也能接云端"],
     ]
       .map(
         ([t, s], i) => `<div class="src-card" data-in="${0.5 + i * 0.24}">
        <div class="ico">${["✓", "✓", "○"][i]}</div>
        <div><div class="tt">${t}</div><div class="ss">${s}</div></div>
      </div>`
       )
       .join("")}
   </div>`
);

scene(
  188.0,
  195.6,
  `${brandbar()}${chapterTag("05", "disk")}
   ${blob("butter", 1420, 180, 330, 330, 16, 0.75, 16, 1.3)}
   <div class="pad" style="left:64px;top:250px;width:900px">
     <div class="bigstat" data-in="0.2">≈<span data-count="90">0</span><span class="pct">%</span></div>
     <div class="h3" style="margin-top:10px" data-in="0.8">磁盘占用节省</div>
     <div class="cap" style="margin-top:22px" data-in="1.0">只存命中的片段，不存整场。此数字为估算，取决于命中率。</div>
   </div>`
);

/* ============ CH7 · What's Next ============ */
scene(
  195.6,
  203.0,
  `${brandbar()}${chapterTag("06", "next")}
   ${blob("lilac", 1400, 120, 330, 330, -12, 0.85, 18, 0.6)}
   ${blob("peach", 1600, 400, 230, 230, 18, 0.78, 14, 2.2)}
   <div class="pad" style="left:64px;top:280px;width:1100px">
     <div class="kicker" data-in="0.15">06 / what's next</div>
     <div class="h1" style="margin-top:30px;font-size:100px">${lines(["下一步：", "第二块<em class='em em-red'>屏</em>。"], 0.4)}</div>
     <div class="cap" style="margin-top:44px" data-in="1.1">把状态放到你不看主屏也能扫一眼的地方。</div>
   </div>`
);

scene(
  203.0,
  210.0,
  `${brandbar()}${chapterTag("06", "hud")}
   <div class="pad" style="left:64px;top:300px;width:620px">
     <div class="h2" style="font-size:60px">${lines(["8 英寸，", "扫一眼就够。"], 0.2)}</div>
     <div class="cap" style="margin-top:36px;max-width:560px" data-in="0.95">任何 Windows 副屏都能当状态卡：在记录、命中几条、当前项目。</div>
     <div class="cap-sm" style="margin-top:22px" data-in="1.6">标注：概念稿</div>
   </div>
   <div class="hud" data-in="0.6">
     <div class="hud-top"><span class="rec"></span>正在记录<span class="hud-proj">某项目</span></div>
     <div class="hud-mid"><span class="hud-num">3</span><span class="hud-unit">条命中</span></div>
     <div class="hud-bot"><span class="chip grey">本地</span><span class="mono">00:41:12</span></div>
   </div>`
);

scene(
  210.0,
  217.6,
  `${brandbar()}${chapterTag("06", "hardware")}
   ${blob("mint", 1470, 150, 300, 300, 10, 0.75, 16, 1.7)}
   <div class="pad" style="left:64px;top:344px;width:1180px">
     <div class="h2" style="font-size:66px">${lines(["Magic Bay-ready：", "标准设备，不碰私有协议。"], 0.2)}</div>
     <div class="cap" style="margin-top:44px;max-width:900px" data-in="1.0">通过 Windows 的设备类接入：标准 UVC 摄像头、标准显示器。</div>
     <div class="cap-sm" style="margin-top:26px" data-in="1.5">尚未在真机验证 —— 我们不把 ready 说成 integrated。</div>
   </div>`
);

/* ============ CH8 · The Verdict ============ */
scene(
  217.6,
  227.0,
  `${brandbar()}
   ${blob("peach", 1300, 90, 300, 300, -14, 0.85, 18, 0.9)}
   ${blob("sky", 1560, 330, 280, 280, 16, 0.8, 15, 2.5)}
   <div class="pad" style="left:64px;top:250px;width:1500px">
     <div class="h1" style="font-size:104px">${lines(["别人帮你记下来。", "meetingVAR 帮你<em class='em em-red'>证</em>出来。"], 0.25, 0.18)}</div>
     <div class="cap" style="margin-top:60px" data-in="1.3">每个回答，都回到已保存的会议证据。</div>
   </div>`
);

scene(
  227.0,
  236.0,
  `${brandbar()}
   ${blob("butter", 1380, 130, 320, 320, 12, 0.7, 16, 1.4)}
   ${blob("lilac", 1580, 400, 240, 240, -18, 0.65, 14, 2.8)}
   <div class="pad" style="left:64px;top:300px">
     <img src="assets/logo.png" style="width:104px;height:104px;border-radius:26px" data-in="0.1">
     <div class="h1" style="margin-top:34px;font-size:110px">meeting<i class="em" style="font-weight:700">VAR</i></div>
     <div class="cap" style="margin-top:22px;font-size:32px" data-in="0.6">会议证据层 · 本地优先</div>
     <div class="cta-row" style="margin-top:56px">
       <div class="cta solid" data-in="0.95">本地演示</div>
       <div class="cta ghost" data-in="1.08">离线可跑</div>
     </div>
     <div class="cap-sm" style="margin-top:44px" data-in="1.3">SDG Open Hack 2026 · Hack16 · Lenovo track</div>
   </div>
   <div class="pad" style="right:64px;bottom:64px" data-in="1.1">${wave(7, 84, 0)}</div>`
);

/* ---------------- renderer ---------------- */
const stage = document.getElementById("stage");
const railFill = document.querySelector(".rail .fill");
const pbarFill = document.querySelector(".pbar .pfill");
const pbar = document.querySelector(".pbar");

[0, 35.4, 72.6, 108.6, 143.6, 173.6, 195.6, 217.6].forEach((c) => {
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
    dur: parseFloat(n.dataset.dur) || 0.62,
    mask: n.dataset.mask === "1",
    y: n.dataset.y ? parseFloat(n.dataset.y) : 26,
    out: n.dataset.out ? parseFloat(n.dataset.out) : null,
  }));
  return { ...s, el, anim, wasOn: false };
});

function setBlobs(el, t) {
  el.querySelectorAll(".blob").forEach((b) => {
    const rot = parseFloat(b.dataset.rot) || 0;
    const amp = parseFloat(b.dataset.drift) || 12;
    const ph = parseFloat(b.dataset.phase) || 0;
    const dx = Math.sin(t * 0.28 + ph) * amp;
    const dy = Math.cos(t * 0.22 + ph * 1.3) * amp * 0.8;
    const rz = rot + Math.sin(t * 0.16 + ph) * 3.2;
    b.style.transform = `translate(${dx.toFixed(2)}px,${dy.toFixed(2)}px) rotate(${rz.toFixed(2)}deg)`;
  });
}

function setWave(el, t, phase = 0) {
  const w = el.querySelector("[data-wave]");
  if (!w) return;
  const red = parseInt(w.dataset.red, 10);
  const bars = w.querySelectorAll(".bar");
  const H = parseFloat(w.style.height) || 100;
  bars.forEach((b, i) => {
    const amp = i === red ? 1 : 0.55 + 0.35 * Math.abs(Math.sin(i * 1.7));
    const v = 0.18 + 0.82 * Math.abs(Math.sin(t * 2.1 + i * 0.62 + phase)) * amp;
    b.style.height = (H * clamp(v, 0.12, 1)).toFixed(1) + "px";
  });
}

function renderAt(t) {
  if (railFill) railFill.style.height = (clamp(t / DUR) * 100).toFixed(2) + "%";
  if (pbarFill) pbarFill.style.width = (clamp(t / DUR) * 100).toFixed(3) + "%";

  nodes.forEach((s) => {
    const on = t >= s.start && t < s.end;
    if (on !== s.wasOn) {
      s.el.classList.toggle("on", on);
      s.wasOn = on;
    }
    if (!on) return;

    const lt = t - s.start;

    // 极缓的推镜，让静止画面也有呼吸
    const camK = clamp(lt / Math.max(0.001, s.end - s.start));
    s.el.style.transform = `scale(${(1 + 0.016 * camK).toFixed(5)})`;

    for (const a of s.anim) {
      const p = clamp((lt - a.at) / a.dur);
      const e = easeOut(p);
      a.n.style.opacity = String(e);

      if (a.mask) {
        a.n.style.transform = `translateY(${((1 - e) * 104).toFixed(2)}%)`;
      } else {
        a.n.style.transform = `translateY(${((1 - e) * a.y).toFixed(2)}px)`;
      }

      // optional exit
      if (a.out !== null) {
        const q = clamp((lt - a.out) / 0.45);
        a.n.style.opacity = String(e * (1 - q));
      }
    }

    setBlobs(s.el, t);
    setWave(s.el, t);

    // full-bleed screenshot pan
    const shot = s.el.querySelector("[data-pan]");
    if (shot) {
      const k = clamp(lt / (s.end - s.start));
      const sc = 1.045;
      const dx = -18 - 46 * easeInOut(k);
      const dy = -10 + 16 * easeInOut(k);
      shot.style.transform = `scale(${sc}) translate(${dx.toFixed(2)}px,${dy.toFixed(2)}px)`;
    }

    // ledger hot row emphasis
    // (pure CSS handles the highlight)

    // verify panel: sequential ticks
    // handled by data-in stagger

    // find-moment clock + playhead
    const clock = s.el.querySelector("[data-clock]");
    if (clock) {
      const k = clamp((lt - 1.2) / 7.0);
      const total = 36;
      const sec = Math.floor(k * total);
      const base = 41 * 60 + 12 + sec; // 00:41:12
      const mm = String(Math.floor(base / 60)).padStart(2, "0");
      const ss = String(base % 60).padStart(2, "0");
      clock.textContent = `00:${mm}:${ss}`;
      const head = s.el.querySelector("[data-head]");
      if (head) head.style.left = (k * 100).toFixed(2) + "%";
      s.el.querySelectorAll("[data-track] i").forEach((bar, i) => {
        const h = parseFloat(bar.dataset.h) || 40;
        const played = i / 64 <= k;
        bar.style.height = h * 0.74 + "%";
        bar.style.opacity = played ? "1" : "0.28";
        bar.style.background = played ? "#e0432f" : "#3a3a42";
      });
    }

    // stat count-up
    const counter = s.el.querySelector("[data-count]");
    if (counter) {
      const target = parseInt(counter.dataset.count, 10);
      const p = easeInOut(clamp((lt - 0.4) / 1.6));
      counter.textContent = String(Math.round(target * p));
    }
  });
}

/* boot */
async function boot() {
  // 时间轴完整性检查：任何空隙都会渲染成空白帧
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