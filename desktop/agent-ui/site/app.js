/* meetingVAR — static site interactions.
   No framework, no network calls. All demo data is local and labelled. */

(function () {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function showToast(message) {
    const toast = $("#toast");
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add("is-visible");
    clearTimeout(showToast.timer);
    showToast.timer = setTimeout(() => toast.classList.remove("is-visible"), 3600);
  }

  /* ------------------------------------------------------------- nav / scroll */

  const nav = $("#nav");
  const progress = $("#scrollProgress");

  function updateScrollUi() {
    const y = window.scrollY || document.documentElement.scrollTop;
    const max = document.documentElement.scrollHeight - window.innerHeight;
    const ratio = max > 0 ? Math.min(Math.max(y / max, 0), 1) : 0;
    if (progress) progress.style.transform = "scaleX(" + ratio + ")";
    if (nav) nav.classList.toggle("is-scrolled", y > 28);
  }

  window.addEventListener("scroll", updateScrollUi, { passive: true });
  window.addEventListener("resize", updateScrollUi);
  updateScrollUi();

  const navToggle = $("#navToggle");
  if (navToggle && nav) {
    navToggle.addEventListener("click", () => {
      const open = nav.classList.toggle("is-open");
      navToggle.setAttribute("aria-expanded", String(open));
      navToggle.setAttribute("aria-label", open ? "关闭导航" : "打开导航");
    });
    $$("#navLinks a").forEach((link) => {
      link.addEventListener("click", () => {
        nav.classList.remove("is-open");
        navToggle.setAttribute("aria-expanded", "false");
        navToggle.setAttribute("aria-label", "打开导航");
      });
    });
  }

  /* active section in nav */

  const navAnchors = $$("#navLinks a");
  const sectionIds = navAnchors
    .map((a) => a.getAttribute("href"))
    .filter((href) => href && href.startsWith("#"))
    .map((href) => href.slice(1));

  function updateActiveLink() {
    const marker = window.innerHeight * 0.42;
    let active = null;
    sectionIds.forEach((id) => {
      const section = document.getElementById(id);
      if (!section) return;
      const rect = section.getBoundingClientRect();
      if (rect.top <= marker && rect.bottom > marker) active = id;
    });
    navAnchors.forEach((anchor) => {
      anchor.classList.toggle("is-current", anchor.getAttribute("href") === "#" + active);
    });
  }

  window.addEventListener("scroll", updateActiveLink, { passive: true });
  window.addEventListener("resize", updateActiveLink);
  updateActiveLink();

  /* -------------------------------------------------------------- scroll reveal */

  const revealItems = $$(".reveal");
  if ("IntersectionObserver" in window && !reduceMotion) {
    const revealObserver = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          entry.target.classList.add("is-visible");
          revealObserver.unobserve(entry.target);
        });
      },
      { threshold: 0.12, rootMargin: "0px 0px -7% 0px" }
    );
    revealItems.forEach((item) => revealObserver.observe(item));
  } else {
    revealItems.forEach((item) => item.classList.add("is-visible"));
  }

  /* ------------------------------------------------------------- workspace name */

  const workspaceInput = $("#workspaceName");
  const workspaceForm = $("#workspaceForm");

  function readWorkspace() {
    try {
      return window.localStorage.getItem("meetingvar.site.workspace") || "";
    } catch (error) {
      return "";
    }
  }

  function saveWorkspace(name) {
    try {
      window.localStorage.setItem("meetingvar.site.workspace", name);
    } catch (error) {
      /* localStorage may be unavailable — the demo still works. */
    }
  }

  function setWorkspace(name) {
    $$("[data-workspace]").forEach((node) => {
      node.textContent = name;
    });
  }

  const storedWorkspace = readWorkspace();
  if (workspaceInput && storedWorkspace) workspaceInput.value = storedWorkspace;
  setWorkspace(storedWorkspace || "证据工作区");

  if (workspaceForm && workspaceInput) {
    workspaceForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const name = workspaceInput.value.trim() || "证据工作区";
      saveWorkspace(name);
      setWorkspace(name);
      activateTab("ledger");
      showToast("工作区「" + name + "」已就绪 · 这是官网演示");
      const evidence = $("#evidence");
      if (evidence) {
        window.setTimeout(() => {
          evidence.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
        }, 120);
      }
    });
  }

  /* ---------------------------------------------------------------- how it works */

  const howBtn = $("#howBtn");
  const manifesto = $("#manifesto");
  const hero = $(".hero");
  if (howBtn && manifesto) {
    howBtn.addEventListener("click", () => {
      const open = manifesto.classList.toggle("is-open");
      howBtn.setAttribute("aria-expanded", String(open));
      manifesto.setAttribute("aria-hidden", String(!open));
      if (hero) hero.classList.toggle("is-expanded", open);
      if (open) {
        window.setTimeout(() => {
          const box = manifesto.getBoundingClientRect();
          if (box.bottom > window.innerHeight) {
            manifesto.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "nearest" });
          }
        }, 380);
      }
    });
  }

  /* ---------------------------------------------------------------------- tabs */

  const tabs = $$('[role="tab"]');

  function activateTab(name, focusTab) {
    if (name !== "moment" && typeof resetPlayer === "function") resetPlayer();
    tabs.forEach((tab) => {
      const active = tab.dataset.tab === name;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
      const panel = document.getElementById(tab.getAttribute("aria-controls"));
      if (panel) {
        panel.hidden = !active;
        panel.classList.toggle("is-active", active);
      }
      if (active && focusTab) tab.focus();
    });
  }

  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => activateTab(tab.dataset.tab));
    tab.addEventListener("keydown", (event) => {
      let next = null;
      if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
      else if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = tabs.length - 1;
      if (next === null) return;
      event.preventDefault();
      activateTab(tabs[next].dataset.tab, true);
    });
  });

  /* ------------------------------------------------------------ commitment ledger */

  const ledgerRows = $$(".ledger-row");
  const quoteCard = $("#quoteCard");
  const quoteAvatar = $("#quoteAvatar");
  const quoteSpeaker = $("#quoteSpeaker");
  const quoteTime = $("#quoteTime");
  const quoteText = $("#quoteText");
  const diffLabel = $("#diffLabel");
  const diffOld = $("#diffOld");
  const diffNew = $("#diffNew");
  const jumpBtn = $("#jumpBtn");
  const avatarTone = { 林晓: "avatar-blue", 周牧: "avatar-amber", 陈屿: "avatar-green" };
  let selectedRow = ledgerRows[0] || null;

  function selectLedgerRow(row) {
    if (!row) return;
    selectedRow = row;
    ledgerRows.forEach((item) => {
      const active = item === row;
      item.classList.toggle("is-selected", active);
      item.setAttribute("aria-pressed", String(active));
    });

    const speaker = row.dataset.speaker || "";
    const time = row.dataset.time || "";
    if (quoteAvatar) {
      quoteAvatar.textContent = speaker.slice(0, 1);
      quoteAvatar.className = "avatar " + (avatarTone[speaker] || "avatar-blue");
    }
    if (quoteSpeaker) quoteSpeaker.textContent = speaker;
    if (quoteTime) quoteTime.textContent = time;
    if (quoteText) quoteText.textContent = row.dataset.quote || "";
    if (diffLabel) diffLabel.textContent = row.dataset.diffLabel || "变更";
    if (diffOld) diffOld.textContent = row.dataset.diffOld || "";
    if (diffNew) diffNew.textContent = row.dataset.diffNew || "";

    if (quoteCard && !reduceMotion) {
      quoteCard.classList.remove("is-swap");
      void quoteCard.offsetWidth;
      quoteCard.classList.add("is-swap");
      window.setTimeout(() => quoteCard.classList.remove("is-swap"), 380);
    }
  }

  ledgerRows.forEach((row) => {
    row.addEventListener("click", () => selectLedgerRow(row));
  });

  if (jumpBtn) {
    jumpBtn.addEventListener("click", () => {
      if (!selectedRow) return;
      showToast("已定位到 " + (selectedRow.dataset.time || "该片段") + "（演示不会播放本地音频）");
    });
  }

  /* ------------------------------------------------------------------ find moment */

  const moments = {
    release: {
      range: "00:31:07 – 00:31:49",
      duration: "42 秒",
      speaker: "说话人 2 · 陈屿",
      start: 31 * 60 + 7,
      end: 31 * 60 + 49,
      quote: "“发布还是放到十二月。十一月的版本先给内测用户，别为了赶这个节点把证据链做薄了。”",
      why: "命中「发布时间 / 十二月 / 内测」；片段里同时包含决定、理由和生效范围。",
      transcript: [
        ["00:31:07", "陈屿：发布还是放到十二月。"],
        ["00:31:24", "林晓：那十一月的版本呢？"],
        ["00:31:36", "陈屿：先给内测用户，别为了赶节点把证据链做薄了。"]
      ]
    },
    design: {
      range: "00:18:42 – 00:19:16",
      duration: "34 秒",
      speaker: "说话人 1 · 林晓",
      start: 18 * 60 + 42,
      end: 19 * 60 + 16,
      quote: "“视觉稿不用赶周五，先把两版方向做出来，下周二我们一起看。”",
      why: "命中「视觉稿 / 交付 / 周五」；片段里包含期限变更和新的验收时间。",
      transcript: [
        ["00:18:42", "陈屿：周五能出两版视觉稿吗？"],
        ["00:18:55", "林晓：不用赶周五，先把两版方向做出来。"],
        ["00:19:08", "林晓：下周二我们一起看，再定最终版。"]
      ]
    },
    verify: {
      range: "00:42:16 – 00:42:52",
      duration: "36 秒",
      speaker: "说话人 3 · 周牧",
      start: 42 * 60 + 16,
      end: 42 * 60 + 52,
      quote: "“离线校验这一步得写进说明里，不然评委拿到 ZIP 不知道从哪开始。”",
      why: "命中「离线 / 校验 / 说明」；片段里给出了交付物和验收标准。",
      transcript: [
        ["00:42:16", "周牧：离线校验这一步得写进说明里。"],
        ["00:42:31", "陈屿：放在 README 还是 manifest 里？"],
        ["00:42:40", "周牧：两边都放，拿到 ZIP 的人不用问我们。"]
      ]
    }
  };

  const momentForm = $("#momentForm");
  const momentQuery = $("#momentQuery");
  const momentSubmit = $("#momentSubmit");
  const momentCard = $("#momentCard");
  const momentRange = $("#momentRange");
  const momentSpeaker = $("#momentSpeaker");
  const momentQuote = $("#momentQuote");
  const momentWhy = $("#momentWhy");
  const transcript = $("#transcript");
  let currentMomentKey = "release";

  function fmtClock(totalSeconds) {
    const value = Math.max(0, Math.round(totalSeconds));
    const h = Math.floor(value / 3600);
    const m = Math.floor((value % 3600) / 60);
    const s = value % 60;
    return [h, m, s].map((part) => String(part).padStart(2, "0")).join(":");
  }

  function renderMoment(key) {
    const moment = moments[key] || moments.release;
    currentMomentKey = key in moments ? key : "release";

    if (momentRange) {
      momentRange.textContent = moment.range + " ";
      const em = document.createElement("em");
      em.textContent = moment.duration;
      momentRange.appendChild(em);
    }
    if (momentSpeaker) momentSpeaker.textContent = moment.speaker;
    if (momentQuote) momentQuote.textContent = moment.quote;
    if (momentWhy) momentWhy.textContent = moment.why;
    if (transcript) {
      transcript.innerHTML = "";
      moment.transcript.forEach(([time, text]) => {
        const li = document.createElement("li");
        const timeNode = document.createElement("time");
        timeNode.textContent = time;
        const textNode = document.createElement("span");
        textNode.textContent = text;
        li.append(timeNode, textNode);
        transcript.appendChild(li);
      });
    }
    resetPlayer();
  }

  function pickMoment(query) {
    if (/发布|十二月|上线/.test(query)) return "release";
    if (/视觉|稿|设计|周五/.test(query)) return "design";
    if (/离线|校验|说明|ZIP/i.test(query)) return "verify";
    return "release";
  }

  if (momentForm && momentQuery) {
    momentForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const query = momentQuery.value.trim();
      if (!query) {
        momentQuery.focus();
        return;
      }
      if (momentSubmit) {
        momentSubmit.disabled = true;
        momentSubmit.textContent = "检索中…";
      }
      window.setTimeout(() => {
        const key = pickMoment(query);
        renderMoment(key);
        if (momentCard && !reduceMotion) {
          momentCard.classList.add("is-updating");
          window.setTimeout(() => momentCard.classList.remove("is-updating"), 260);
        }
        if (momentSubmit) {
          momentSubmit.disabled = false;
          momentSubmit.textContent = "找回那一刻";
        }
        showToast("找到 1 个片段 · " + moments[key].duration + "（示例数据）");
      }, 460);
    });
  }

  $$(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      if (!momentQuery || !momentForm) return;
      momentQuery.value = chip.dataset.q || "";
      if (typeof momentForm.requestSubmit === "function") momentForm.requestSubmit();
      else momentForm.dispatchEvent(new Event("submit", { cancelable: true }));
    });
  });

  /* ------------------------------------------------------------------ player */

  const wave = $("#wave");
  const playBtn = $("#playBtn");
  const playFill = $("#playFill");
  const playClock = $("#playClock");
  const BAR_COUNT = 46;
  const PLAY_MS = 12000;
  const playerState = { playing: false, timer: 0, startedAt: 0, offset: 0, bars: [] };

  if (wave) {
    for (let index = 0; index < BAR_COUNT; index += 1) {
      const bar = document.createElement("i");
      const height = 16 + Math.abs(Math.sin(index * 1.34 + 0.6)) * 64 + (index % 4) * 3;
      bar.style.setProperty("--h", Math.min(height, 94) + "%");
      wave.appendChild(bar);
    }
    playerState.bars = $$("i", wave);
  }

  function playerMoment() {
    return moments[currentMomentKey] || moments.release;
  }

  function setPlayerProgress(progressValue) {
    const value = Math.min(Math.max(progressValue, 0), 1);
    if (playFill) playFill.style.width = value * 100 + "%";
    if (playClock) {
      const moment = playerMoment();
      playClock.textContent = fmtClock(moment.start + (moment.end - moment.start) * value);
    }
    playerState.bars.forEach((bar, index) => {
      bar.classList.toggle("is-played", index / playerState.bars.length < value);
    });
    const lines = transcript ? $$("li", transcript) : [];
    const activeIndex = value >= 1 ? lines.length - 1 : Math.floor(value * lines.length);
    lines.forEach((line, index) => line.classList.toggle("is-current", index === activeIndex && value > 0));
  }

  function setPlayingUi(playing) {
    playerState.playing = playing;
    if (!playBtn) return;
    playBtn.classList.toggle("is-playing", playing);
    playBtn.setAttribute("aria-label", playing ? "暂停示例片段" : "播放示例片段");
  }

  function resetPlayer() {
    clearInterval(playerState.timer);
    playerState.offset = 0;
    playerState.startedAt = 0;
    setPlayingUi(false);
    setPlayerProgress(0);
    const lines = transcript ? $$("li", transcript) : [];
    lines.forEach((line) => line.classList.remove("is-current"));
  }

  function tick() {
    if (!playerState.playing) return;
    const elapsed = playerState.offset + (performance.now() - playerState.startedAt);
    const value = Math.min(elapsed / PLAY_MS, 1);
    setPlayerProgress(value);
    if (value >= 1) {
      clearInterval(playerState.timer);
      setPlayingUi(false);
      playerState.offset = 0;
    }
  }

  if (playBtn) {
    playBtn.addEventListener("click", () => {
      if (playerState.playing) {
        playerState.offset += performance.now() - playerState.startedAt;
        clearInterval(playerState.timer);
        setPlayingUi(false);
        return;
      }
      if (reduceMotion) {
        setPlayerProgress(1);
        showToast("已展示完整片段位置（已开启减少动态效果）");
        return;
      }
      if (playerState.offset >= PLAY_MS) playerState.offset = 0;
      if (playerState.offset === 0) setPlayerProgress(0);
      playerState.startedAt = performance.now();
      setPlayingUi(true);
      clearInterval(playerState.timer);
      playerState.timer = setInterval(tick, 100);
    });
  }

  resetPlayer();
  if (momentRange) {
    momentRange.textContent = moments.release.range + " ";
    const em = document.createElement("em");
    em.textContent = moments.release.duration;
    momentRange.appendChild(em);
  }

  /* ------------------------------------------------------------------ evidence pack */

  const packState = {
    expected: {},
    current: {},
    manifestText: "",
    root: "",
    tampered: false,
    base: {
      audio: "MEETINGVAR SAMPLE AUDIO\nclip=0031\nstart=00:31:07\nend=00:31:49\nformat=m4a\n",
      transcript:
        "1\n00:00:00,000 --> 00:00:12,000\n陈屿：发布还是放到十二月。\n\n" +
        "2\n00:00:17,000 --> 00:00:25,000\n林晓：那十一月的版本呢？\n\n" +
        "3\n00:00:29,000 --> 00:00:42,000\n陈屿：先给内测用户，别为了赶节点把证据链做薄了。\n",
      frame: "MEETINGVAR SAMPLE FRAME\nclip=0031\ntime=00:31:12\nresolution=2560x1440\n"
    }
  };

  const verifyBtn = $("#verifyBtn");
  const tamperBtn = $("#tamperBtn");
  const verifyNote = $("#verifyNote");
  const verifyResult = $("#verifyResult");
  const manifestCode = $("#manifestCode");
  const packRows = {
    manifest: $('.file-row[data-key="manifest"]'),
    audio: $('.file-row[data-key="audio"]'),
    transcript: $('.file-row[data-key="transcript"]'),
    frame: $('.file-row[data-key="frame"]')
  };
  const packLabels = {
    manifest: "manifest.json",
    audio: "audio/clip-0031.m4a",
    transcript: "transcript/clip-0031.srt",
    frame: "frames/clip-0031-01.jpg"
  };

  function canHash() {
    return Boolean(window.crypto && window.crypto.subtle && window.TextEncoder);
  }

  async function sha256(text) {
    const bytes = new TextEncoder().encode(text);
    const digest = await window.crypto.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(digest))
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join("");
  }

  function shortHash(hash) {
    return hash.slice(0, 12) + "…" + hash.slice(-6);
  }

  function transcriptContent() {
    return (
      packState.base.transcript +
      (packState.tampered ? "\n4\n00:00:42,000 --> 00:00:44,000\n（这一行是演示时后加的）\n" : "")
    );
  }

  function renderHashes(hashes) {
    Object.keys(packRows).forEach((key) => {
      const row = packRows[key];
      if (!row) return;
      const node = row.querySelector("[data-hash]");
      if (!node || !hashes[key]) return;
      node.textContent = shortHash(hashes[key]);
      node.title = hashes[key];
    });
  }

  function clearMarks() {
    Object.values(packRows).forEach((row) => {
      if (!row) return;
      row.classList.remove("is-ok", "is-bad");
      const mark = row.querySelector("[data-mark]");
      if (mark) mark.textContent = "";
    });
    if (verifyResult) {
      verifyResult.hidden = true;
      verifyResult.classList.remove("is-bad");
      verifyResult.textContent = "";
    }
  }

  async function initPack() {
    if (!canHash()) {
      if (verifyNote) verifyNote.textContent = "当前环境无法计算 SHA256；请通过 localhost 或 https 打开本站。";
      if (verifyBtn) verifyBtn.disabled = true;
      if (tamperBtn) tamperBtn.disabled = true;
      Object.values(packRows).forEach((row) => {
        const node = row && row.querySelector("[data-hash]");
        if (node) node.textContent = "不可用";
      });
      if (manifestCode) manifestCode.textContent = "此环境不支持 Web Crypto，无法生成示例 manifest。";
      return;
    }

    const audioHash = await sha256(packState.base.audio);
    const transcriptHash = await sha256(packState.base.transcript);
    const frameHash = await sha256(packState.base.frame);

    const manifest = {
      pack: "evidence-pack-0031",
      created_at: "2026-11-24T09:31:07+08:00",
      project: "产品周会",
      clip: { start: "00:31:07", end: "00:31:49", duration_seconds: 42 },
      files: [
        { path: "audio/clip-0031.m4a", sha256: audioHash },
        { path: "transcript/clip-0031.srt", sha256: transcriptHash },
        { path: "frames/clip-0031-01.jpg", sha256: frameHash }
      ]
    };

    packState.manifestText = JSON.stringify(manifest, null, 2);
    packState.expected = {
      manifest: await sha256(packState.manifestText),
      audio: audioHash,
      transcript: transcriptHash,
      frame: frameHash
    };
    packState.root = await sha256(
      Object.keys(packState.expected)
        .map((key) => packState.expected[key])
        .join("")
    );

    if (manifestCode) manifestCode.textContent = packState.manifestText;
    renderHashes(packState.expected);
  }

  async function verifyPack() {
    if (!canHash() || !verifyBtn) return;
    verifyBtn.disabled = true;
    verifyBtn.textContent = "校验中…";
    await sleep(320);

    packState.current = {
      manifest: await sha256(packState.manifestText),
      audio: await sha256(packState.base.audio),
      transcript: await sha256(transcriptContent()),
      frame: await sha256(packState.base.frame)
    };

    const bad = [];
    Object.keys(packState.expected).forEach((key) => {
      const row = packRows[key];
      if (!row) return;
      const ok = packState.current[key] === packState.expected[key];
      if (!ok) bad.push(key);
      row.classList.toggle("is-ok", ok);
      row.classList.toggle("is-bad", !ok);
      const mark = row.querySelector("[data-mark]");
      if (mark) mark.textContent = ok ? "✓" : "×";
      const hashNode = row.querySelector("[data-hash]");
      if (hashNode) {
        hashNode.textContent = shortHash(packState.current[key]);
        hashNode.title = packState.current[key];
      }
    });

    const currentRoot = await sha256(
      Object.keys(packState.current)
        .map((key) => packState.current[key])
        .join("")
    );

    if (verifyResult) {
      verifyResult.hidden = false;
      verifyResult.classList.toggle("is-bad", bad.length > 0);
      if (bad.length) {
        verifyResult.innerHTML =
          "<strong>" +
          (4 - bad.length) +
          " / 4 文件一致</strong><br><code>" +
          bad.map((key) => packLabels[key]).join(", ") +
          "</code> 与 manifest 不匹配，根哈希也已改变。";
      } else {
        verifyResult.innerHTML =
          "<strong>4 / 4 文件一致</strong><br>根哈希 <code>" +
          shortHash(currentRoot) +
          "</code> 与记录一致。";
      }
    }

    verifyBtn.disabled = false;
    verifyBtn.textContent = "重新校验";
    showToast(bad.length ? "校验未通过：有文件被改动" : "校验通过：4 个文件与 manifest 一致");
  }

  if (verifyBtn) verifyBtn.addEventListener("click", verifyPack);

  if (tamperBtn) {
    tamperBtn.addEventListener("click", () => {
      packState.tampered = !packState.tampered;
      tamperBtn.textContent = packState.tampered ? "恢复原始文件" : "模拟篡改一个文件";
      clearMarks();
      renderHashes(packState.expected);
      if (verifyBtn) verifyBtn.textContent = "一键校验 SHA256";
      if (verifyNote) {
        verifyNote.textContent = packState.tampered
          ? "已改动 transcript/clip-0031.srt。点「一键校验」看看 manifest 能不能发现。"
          : "哈希在浏览器内实时计算。示例内容，不是真实会议文件。";
      }
      showToast(
        packState.tampered
          ? "已改动 transcript：点「一键校验」看看会发生什么"
          : "已恢复原始文件，可以重新校验"
      );
    });
  }

  initPack().catch(() => {
    if (verifyNote) verifyNote.textContent = "示例 manifest 生成失败；请通过 localhost 或 https 打开本站。";
    if (verifyBtn) verifyBtn.disabled = true;
  });

  /* ------------------------------------------------------------------- dialogs */

  $$("[data-dialog-open]").forEach((button) => {
    button.addEventListener("click", () => {
      const dialog = document.getElementById(button.dataset.dialogOpen);
      if (!dialog) return;
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
      document.body.classList.add("dialog-open");
    });
  });

  $$(".dialog").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
    dialog.addEventListener("close", () => {
      document.body.classList.remove("dialog-open");
      const scrollTarget = dialog.querySelector("[data-scroll-to]");
      if (scrollTarget && dialog.dataset.scrolled === "true") {
        const target = document.getElementById(scrollTarget.dataset.scrollTo);
        if (target) {
          window.setTimeout(() => {
            target.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
          }, 80);
        }
        dialog.dataset.scrolled = "false";
      }
    });
  });

  $$("[data-dialog-close]").forEach((button) => {
    button.addEventListener("click", () => {
      const dialog = button.closest("dialog");
      if (!dialog) return;
      if (button.dataset.scrollTo) dialog.dataset.scrolled = "true";
      dialog.close();
    });
  });

  /* --------------------------------------------------------------------- misc */

  $$('a[href^="#"]').forEach((link) => {
    link.addEventListener("click", (event) => {
      const id = link.getAttribute("href").slice(1);
      if (!id) return;
      event.preventDefault();
      if (id === "top") {
        window.scrollTo({ top: 0, behavior: reduceMotion ? "auto" : "smooth" });
      } else {
        const target = document.getElementById(id);
        if (!target) return;
        target.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
      }
      try {
        if (history.replaceState) history.replaceState(null, "", "#" + id);
      } catch (error) {
        /* file:// may reject replaceState; navigation already happened. */
      }
    });
  });

  renderMoment("release");
})();