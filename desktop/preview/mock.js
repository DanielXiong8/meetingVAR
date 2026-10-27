/* Injected ONLY by preview_ui.py. Never loaded by the native desktop app. */
(() => {
  "use strict";
  const project = "D:\\项目资料\\meetingVAR\\界面改版与会议记录";
  let config = {
    engine_python: "D:\\miniconda3\\envs\\meetingvar\\python.exe", repo_dir: "C:\\projects\\meetingVAR",
    device: "cuda", last_project: project, projects: [project], llm_provider: "custom",
    llm_base_url: "https://api.example.com/v1", llm_model: "preview-model", llm_api_key: "",
    interest_mode: "api", asr_engine: "xfyun_rtasr", audio_source: "both", xfyun_dir: "D:\\讯飞服务",
  };
  let scenario = "ready", task = null, media = null, running = false, seq = 0, phase = "idle";
  let queue = [], retryCount = 0;
  const calls = [];
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const delay = () => new Promise((resolve) => setTimeout(resolve, 180));
  const result = {
    ok: true, overview: "项目围绕会议记录与证据保存，重点关注识别质量、界面体验和跨平台协作。",
    chunks_read: 6, chunks_total: 6, covered_chars: 20466, unique_chars: 20466, coverage_pct: 100, cache_hits: 2,
    terms: [{term: "实时转写", count: 12, why: "核心语音识别能力"}, {term: "项目理解", count: 8}, {term: "回溯取证", count: 6}, {term: 'Windows "WebView2"', count: 4}],
    persons: [{name: "项目负责人", count: 3}], events: [{what: "完成实时字幕与项目理解界面联调", quote: "先验证基础链路，再做真实会议验收。"}],
  };
  const api = {
    bootstrap: async () => ({version: "0.1.2", config, meeting_running: false, media_base: "", llm: {up: false}}),
    get_config: async () => clone(config),
    set_config: async (updates) => { calls.push(["set_config", clone(updates)]); Object.assign(config, updates); return clone(config); },
    page_ready: async () => { switchPage("project"); return {ok: true}; },
    scan_project: async (folder) => {
      await delay(); calls.push(["scan_project", folder]);
      if (scenario === "scan-error") return {ok: false, error: "找不到该文件夹。请确认路径，或重新选择项目文件夹。"};
      return {ok: true, doc_count: 12, term_count: 34, terms: {RTASR: 2, WebView2: 3}, persons: ["项目负责人"], repo: {is_repo: true, top: {Session: 4, AudioCapture: 2}},
        notes: ["跳过 .git 和缓存目录。"], skipped: ["演示素材.zip", "会议截图.png"], tree: [
          {name: "docs", dir: true, children: [{name: "UI_HANDOFF_0.1.2.md", size: 11576}, {name: "会议评估方案.md", size: 3227}]},
          {name: "desktop", dir: true, children: [{name: "web", dir: true, children: [{name: "index.html", size: 9050}, {name: "style.css", size: 12675}, {name: "app.js", size: 28765}]}]},
          {name: "README.md", size: 6324}, {name: "SETUP.md", size: 9617},
        ]};
    },
    pick_folder: async () => project,
    understand_plan: async () => { await delay(); return {ok: true, chars: 20466, chunks: Array(6).fill({}), est_local_minutes: 18.4,
      provider: config.llm_provider, label: config.llm_provider === "local" ? "本地模型" : "自定义 API",
      egress: config.llm_provider === "local" ? "在本机处理，不发送项目文档。" : "项目文档块将发送至所选自定义 API。费用以厂商账单为准。",
      ready: scenario !== "not-ready", error: scenario === "not-ready" ? "当前项目理解模型未就绪。请先在设置页检查服务配置。" : "",
      notes: ["已按内容去重，重复资料仅计一次。", "文本按最多 4000 字切块；实际耗时随模型和网络而变化。"]}; },
    understand_start: async (folder, maxChunks) => { calls.push(["understand_start", folder, maxChunks]); await delay(); task = {folder, maxChunks, poll: 0, retry: ++retryCount}; return {ok: true}; },
    understand_status: async () => {
      if (!task) return {running: false, done: false};
      task.poll++;
      if (task.cancelled) return {folder: task.folder, done: true, ok: false, error: "已取消，已完成块保留。"};
      if (task.poll < 2) return {folder: task.folder, running: true, progress: `已读 2/${task.maxChunks || 6} 块：docs/会议评估方案.md`};
      if (scenario === "interrupted" && task.retry === 1) return {folder: task.folder, done: true, ok: false, error: "API 连接中断（请求超时）。已完成 2 块已缓存，可继续理解。"};
      return {folder: task.folder, done: true, ok: true, result: {...result, chunks_read: task.maxChunks || 6, chunks_total: 6, coverage_pct: task.maxChunks ? 83 : 100}};
    },
    understand_cancel: async () => { if (task) task.cancelled = true; return {ok: true}; },
    terms_write: async (folder, terms) => { calls.push(["terms_write", folder, clone(terms)]); return {ok: true, count: terms.length, out: project + "\\meetingvar\\terms.txt"}; },
    llm_status: async () => ({up: false, managed: false}), llm_start: async () => ({ok: true}), llm_stop: async () => ({ok: true}),
    check_xfyun: async () => ({ok: false, error: "未找到当前服务的可用凭证（预览合成状态）。"}),
    pick_xfyun_dir: async () => "D:\\讯飞服务", test_llm: async () => ({ok: true, note: "合成测试结果，未发送 API 请求"}),
    test_engine: async () => ({ok: true, python: "3.10（合成）", meetingvar: "0.1.2"}),
    start_meeting: async () => {
      running = true; phase = "ready";
      queue = [
        {type: "capture_state", phase: "ready", text: "合成采集已就绪"},
        {type: "log", text: "[rec] UI 预览，不打开真实设备。"},
        {type: "partial_line", id: "p1", text: "我们先确认界面的操作层级"},
        {type: "partial_line", id: "p1", text: ""},
        {type: "line", clock: "10:30:02", ts: 2, spk: "spk?", text: "我们先确认界面的操作层级，再验证 Windows 上的实际运行效果。"},
      ].map((event) => ({...event, i: ++seq}));
      return {ok: true, started_at: Date.now()/1000};
    },
    stop_meeting: async () => { running = false; phase = "idle"; queue.push({i: ++seq, type: "exit", code: 0, summary: {saved: false, clips: 0}}); return {ok: true}; },
    poll_events: async (since) => ({events: queue.filter((event) => event.i > since), next: seq, running, phase, screen: {}}),
    manual_var: async () => ({ok: true}), answer_prompt: async () => ({ok: true}),
    list_library: async () => [{project, name: "meetingVAR", latest: Date.now()/1000, sessions: [{sid: "preview_20261018", clips: 1, lines: 24, minutes: true, mtime: Date.now()/1000}]}],
    session_detail: async (sid) => ({sid, minutes: "# 界面评审\n\n- 确认操作层级与错误恢复。\n- Windows 实机验收待完成。", reports: "合成预览数据", clips: []}),
    pick_media: async () => "D:\\会议记录\\界面评审.wav",
    transcribe_media_start: async (path) => { media = path; return {ok: true}; },
    transcribe_media_status: async () => ({done: true, ok: true, out: media + ".transcript.txt"}),
    open_data_dir: async () => ({ok: true}), open_config_dir: async () => ({ok: true}),
  };
  window.pywebview = {api};
  window.__meetingvarPreview = {calls};
  const toolbar = document.createElement("div");
  toolbar.style.cssText = "display:flex;align-items:center;gap:12px;padding:8px 24px;background:#fef3c7;color:#78350f;flex:0 0 auto;font-size:12px";
  toolbar.innerHTML = '<strong>UI 预览 · 合成数据</strong><label for="previewScenario">场景</label><select id="previewScenario" style="font:inherit"><option value="ready">正常流程</option><option value="interrupted">理解中断 → 重试成功</option><option value="not-ready">模型未就绪</option><option value="scan-error">项目读取失败</option></select><span>不调用真实 API，不录音，不读写项目</span>';
  document.body.prepend(toolbar);
  document.getElementById("previewScenario").onchange = async (event) => {
    if (document.getElementById("undCard").getAttribute("aria-busy") === "true") {
      event.target.value = scenario;
      toast("请先等待或取消当前理解任务，再切换预览场景。");
      return;
    }
    scenario = event.target.value; retryCount = 0; task = null;
    switchPage("project"); await scanProject(project);
  };
})();
