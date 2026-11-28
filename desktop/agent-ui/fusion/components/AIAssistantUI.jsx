"use client"

import React, { useEffect, useMemo, useRef, useState } from "react"
import { LayoutGrid, Play, Radio, PackageCheck } from "lucide-react"
import Sidebar from "./Sidebar"
import Header from "./Header"
import ChatPane from "./ChatPane"
import GhostIconButton from "./GhostIconButton"
import ThemeToggle from "./ThemeToggle"
import { INITIAL_TEMPLATES, INITIAL_FOLDERS } from "./mockData"
import MeetingClips from "./MeetingClips"
import EvidenceModal from "./EvidenceModal"
import MeetingSettings from "./MeetingSettings"
import { LiveMeetingConsole, StartMeetingDialog } from "./LiveMeeting"
import { ProjectExplorer, ProjectFileViewer } from "./ProjectExplorer"
import { useMeetingVAR, projectLabel } from "../../lib/meetingvar"
import { PROVIDER_PRESETS, contextLimit, loadModels, loadProviderId, reasoningSupported, saveModels, saveProviderId } from "./model-options"

function createConversation(id, title = "新会议") {
  return { id, title, updatedAt: new Date().toISOString(), messageCount: 0, preview: "询问原话、决定和它们发生的时刻……", pinned: false, folder: "会议", messages: [] }
}

export default function AIAssistantUI() {
  const engine = useMeetingVAR()
  const [theme, setTheme] = useState(() => {
    const saved = typeof window !== "undefined" && localStorage.getItem("theme")
    if (saved) return saved
    return "dark"
  })
  useEffect(() => {
    try {
      if (theme === "dark") document.documentElement.classList.add("dark")
      else document.documentElement.classList.remove("dark")
      document.documentElement.setAttribute("data-theme", theme)
      document.documentElement.style.colorScheme = theme
      localStorage.setItem("theme", theme)
    } catch {}
  }, [theme])
  useEffect(() => {
    const media = window.matchMedia?.("(prefers-color-scheme: dark)")
    const listener = (e) => { if (!localStorage.getItem("theme")) setTheme(e.matches ? "dark" : "light") }
    media?.addEventListener("change", listener)
    return () => media?.removeEventListener("change", listener)
  }, [])
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(() => {
    try { const raw = localStorage.getItem("sidebar-collapsed"); return raw ? JSON.parse(raw) : { pinned: true, recent: false, folders: true, templates: true } } catch { return { pinned: true, recent: false, folders: true, templates: true } }
  })
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => { try { return JSON.parse(localStorage.getItem("sidebar-collapsed-state") || "false") } catch { return false } })
  useEffect(() => { try { localStorage.setItem("sidebar-collapsed", JSON.stringify(collapsed)); localStorage.setItem("sidebar-collapsed-state", JSON.stringify(sidebarCollapsed)) } catch {} }, [collapsed, sidebarCollapsed])
  const [conversations, setConversations] = useState(() => [createConversation("c1", "会议证据")])
  const [selectedId, setSelectedId] = useState("c1")
  const [showClips, setShowClips] = useState(false)
  const [templates, setTemplates] = useState(INITIAL_TEMPLATES)
  const [folders, setFolders] = useState(INITIAL_FOLDERS)
  const [query, setQuery] = useState("")
  const [modal, setModal] = useState(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [models, setModels] = useState([])
  const [showStart, setShowStart] = useState(false)
  const [showLive, setShowLive] = useState(false)
  const [showProjectTree, setShowProjectTree] = useState(true)
  const [session, setSession] = useState(null)
  const [sessionData, setSessionData] = useState(null)
  const [actionsBusy, setActionsBusy] = useState(false)
  const searchRef = useRef(null)
  const composerRef = useRef(null)
  useEffect(() => { setModels(loadModels(engine.config?.llm_model)) }, [engine.config?.llm_model])
  useEffect(() => { if (engine.recording) setShowLive(true) }, [engine.recording])
  function changeModels(next) { saveModels(next); setModels(next) }
  function changeModel(next, nextProvider = providerId) {
    if (!next) return
    const preset = PROVIDER_PRESETS.find((item) => item.id === nextProvider) || PROVIDER_PRESETS[0]
    saveProviderId(preset.id)
    if (preset.id === "local") void engine.updateConfig({ llm_model: next, llm_provider: "local" })
    else void engine.updateConfig({ llm_model: next, llm_provider: "custom", ...(preset.base ? { llm_base_url: preset.base } : {}) })
  }
  function changeReasoning(next) { void engine.updateConfig({ llm_reasoning_effort: next }) }

  useEffect(() => {
    const title = sessionStorage.getItem("fusion-meeting")
    if (title) setConversations((prev) => prev.map((item, index) => index === 0 ? { ...item, title } : item))
  }, [])
  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "n") { e.preventDefault(); createNewChat() }
      if (!e.metaKey && !e.ctrlKey && e.key === "/" && !["input", "textarea"].includes(document.activeElement?.tagName?.toLowerCase())) { e.preventDefault(); searchRef.current?.focus() }
      if (e.key === "Escape") { setSidebarOpen(false); setModal(null); setSession(null) }
    }
    window.addEventListener("keydown", onKey); return () => window.removeEventListener("keydown", onKey)
  }, [conversations])
  const filtered = useMemo(() => { const q = query.toLowerCase(); return conversations.filter((c) => !q || c.title.toLowerCase().includes(q) || c.preview.toLowerCase().includes(q)) }, [conversations, query])
  const pinned = filtered.filter((c) => c.pinned)
  const recent = filtered.filter((c) => !c.pinned).slice(0, 10)
  const folderCounts = useMemo(() => Object.fromEntries(folders.map((f) => [f.name, conversations.filter((c) => c.folder === f.name).length])), [folders, conversations])
  const selected = conversations.find((c) => c.id === selectedId) || null
  const selectedUsage = Number(engine.lastUsage?.prompt_tokens || 0)
  const estimatedUsage = selected ? Math.ceil(selected.messages.reduce((total, item) => total + String(item.content || "").length, 0) / 3) : 0
  const contextUsed = selectedUsage || estimatedUsage
  const contextTotal = contextLimit(engine.config)
  const providerId = loadProviderId(engine.config)
  const contextEstimated = !selectedUsage && Boolean(estimatedUsage)
  function togglePin(id) { setConversations((prev) => prev.map((c) => c.id === id ? { ...c, pinned: !c.pinned } : c)) }
  function createNewChat() { const item = createConversation(Math.random().toString(36).slice(2)); setConversations((prev) => [item, ...prev]); setSelectedId(item.id); setShowClips(false); setSidebarOpen(false) }
  function createFolder() { const name = prompt("文件夹名称"); if (!name) return; if (folders.some((f) => f.name.toLowerCase() === name.toLowerCase())) return alert("文件夹已存在。"); setFolders((prev) => [...prev, { id: Math.random().toString(36).slice(2), name }]) }
  async function sendMessage(convId, content) {
    if (!content.trim() || engine.activeRun) return
    const userMsg = { id: Math.random().toString(36).slice(2), role: "user", content, createdAt: new Date().toISOString() }
    setConversations((prev) => prev.map((c) => c.id !== convId ? c : { ...c, messages: [...c.messages, userMsg], messageCount: c.messages.length + 1, preview: content.slice(0, 80), updatedAt: new Date().toISOString() }))
    const response = await engine.send(content, convId)
    if (response) setConversations((prev) => prev.map((c) => c.id !== convId ? c : { ...c, messages: [...c.messages, response], messageCount: c.messages.length + 1, preview: response.content.slice(0, 80), updatedAt: new Date().toISOString() }))
  }
  function editMessage(convId, messageId, newContent) { setConversations((prev) => prev.map((c) => c.id !== convId ? c : { ...c, messages: c.messages.map((m) => m.id === messageId ? { ...m, content: newContent, editedAt: new Date().toISOString() } : m), preview: newContent.slice(0, 80) })) }
  function resendMessage(convId, messageId) { const msg = conversations.find((c) => c.id === convId)?.messages.find((m) => m.id === messageId); if (msg) void sendMessage(convId, msg.content) }
  async function openSession(item) {
    setSession(item); setModal("session"); setSessionData(null)
    const result = await engine.sessionDetail(item.sid); setSessionData(result)
  }
  async function executeAction(action) {
    if (!engine.desktop || actionsBusy) return
    setActionsBusy(true)
    try {
      const result = await engine.executeAction(action)
      if (result) setConversations((prev) => prev.map((c) => c.id === selectedId ? { ...c, messages: [...c.messages, result] } : c))
    } finally { setActionsBusy(false) }
  }
  return <div className="h-screen w-full bg-zinc-50 text-zinc-900 dark:bg-zinc-950 dark:text-zinc-100">
    <div className="md:hidden sticky top-0 z-40 flex items-center gap-2 border-b border-zinc-200/60 bg-white/80 px-3 py-2 backdrop-blur dark:border-zinc-800 dark:bg-zinc-900/70"><div className="ml-1 flex items-center gap-2 text-sm font-semibold tracking-tight"><span>✱</span> meetingVAR</div><div className="ml-auto flex items-center gap-2"><GhostIconButton label="会议库" onClick={() => setShowClips(true)}><LayoutGrid className="h-4 w-4" /></GhostIconButton><ThemeToggle theme={theme} setTheme={setTheme} /></div></div>
    <div className="flex h-screen w-full overflow-hidden"><Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} theme={theme} setTheme={setTheme} collapsed={collapsed} setCollapsed={setCollapsed} sidebarCollapsed={sidebarCollapsed} setSidebarCollapsed={setSidebarCollapsed} conversations={conversations} pinned={pinned} recent={recent} folders={folders} folderCounts={folderCounts} selectedId={selectedId} onSelect={(id) => { setSelectedId(id); setShowClips(false) }} togglePin={togglePin} query={query} setQuery={setQuery} searchRef={searchRef} createFolder={createFolder} createNewChat={createNewChat} templates={templates} setTemplates={setTemplates} onUseTemplate={(template) => composerRef.current?.insertTemplate(template.content)} onOpenSettings={() => setSettingsOpen(true)} projects={engine.projects} project={engine.project} onSelectProject={(path) => { if (engine.recording) { engine.setNotice("切换项目前请先结束当前会议。"); return } void engine.selectProject(path) }} onChooseProject={() => void engine.chooseProject()} />
      <main className="relative flex min-w-0 flex-1 flex-col"><Header createNewChat={createNewChat} sidebarCollapsed={sidebarCollapsed} setSidebarOpen={setSidebarOpen} openClips={() => setShowClips(true)} project={projectLabel(engine.project)} onProject={() => void engine.chooseProject()} onToggleProjectTree={() => setShowProjectTree((value) => !value)} />
        {engine.desktop === false ? <div className="mx-4 mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">当前仅为界面预览。启动 meetingVAR 桌面端后，才能使用本地项目、录制和真实 Agent 工具。</div> : null}
        <div className="flex flex-wrap gap-2 px-4 pt-3 sm:px-8"><button className="inline-flex items-center gap-2 rounded-full border border-zinc-200 px-3 py-1.5 text-xs dark:border-zinc-800" onClick={() => engine.recording ? setShowLive(true) : setShowStart(true)} disabled={engine.pending}>{engine.recording ? <Radio className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}{engine.recording ? "打开实时会议" : "开始会议"}</button><button className="inline-flex items-center gap-2 rounded-full border border-zinc-200 px-3 py-1.5 text-xs dark:border-zinc-800" onClick={() => setModal("evidence")}><PackageCheck className="h-3.5 w-3.5" />证据 <span>{engine.packs.length + engine.ledger.length + engine.moments.length}</span></button></div>
        {engine.notice ? <div className="mx-4 mt-2 flex items-center justify-between rounded-lg bg-zinc-100 px-3 py-2 text-xs text-zinc-600 dark:bg-zinc-900 dark:text-zinc-400 sm:mx-8"><span>{engine.notice}</span><button onClick={() => engine.setNotice("")}>关闭</button></div> : null}
        {showLive ? <LiveMeetingConsole live={engine.live} recording={engine.recording} pending={engine.pending} project={engine.project} config={engine.config} on停止={() => void engine.stopMeeting()} onVar={() => void engine.saveMoment()} onAnswer={(name) => void engine.answerSpeaker(name)} onBack={() => setShowLive(false)} /> : showClips ? <MeetingClips sessions={engine.sessions} desktop={engine.desktop} project={engine.project} projects={engine.projects} onBack={() => setShowClips(false)} onSelect={openSession} onChooseProject={() => void engine.chooseProject()} onSelectProject={(path) => { if (engine.recording) { engine.setNotice("切换项目前请先结束当前会议。"); return } void engine.selectProject(path) }} onRename={engine.renameSession} /> : <ChatPane ref={composerRef} conversation={selected} onSend={(content) => selected && sendMessage(selected.id, content)} onEditMessage={(messageId, newContent) => selected && editMessage(selected.id, messageId, newContent)} on重发Message={(messageId) => selected && resendMessage(selected.id, messageId)} isThinking={Boolean(engine.activeRun)} onPauseThinking={() => void engine.cancel()} liveEvents={engine.activeRun?.events} onAction={executeAction} models={models} model={engine.config?.llm_model || ""} onModelChange={changeModel} provider={providerId} onOpenSettings={() => setSettingsOpen(true)} reasoning={engine.config?.llm_reasoning_effort || ""} onReasoningChange={changeReasoning} reasoningAvailable={reasoningSupported(engine.config)} contextUsed={contextUsed} contextTotal={contextTotal} contextEstimated={contextEstimated} />}
      </main>
      {showProjectTree ? <ProjectExplorer project={engine.project} tree={engine.tree} loading={engine.treeLoading} onClose={() => setShowProjectTree(false)} onRefresh={() => void engine.refreshTree()} onOpenFile={(rel) => void engine.openProjectFile(rel)} onChooseProject={() => void engine.chooseProject()} /> : null}
    </div>
    <ProjectFileViewer file={engine.fileViewer} onClose={() => engine.setFileViewer(null)} onOpenExternal={(rel) => void engine.openProjectFileExternal(rel)} />
    <StartMeetingDialog open={showStart} project={engine.project} config={engine.config} pending={engine.pending} onClose={() => setShowStart(false)} onChooseProject={() => void engine.chooseProject()} onStart={async (focus, askSpeakers) => { const result = await engine.startMeeting(focus, askSpeakers); if (result?.ok) { setShowStart(false); setShowLive(true) } return result }} />
    <MeetingSettings open={settingsOpen} onClose={() => setSettingsOpen(false)} onNotice={engine.setNotice} config={engine.config} onSaved={() => void engine.refreshConfig()} models={models} onModelsChange={changeModels} />
    {modal ? <EvidenceModal type={modal} packs={engine.packs} ledger={engine.ledger} moments={engine.moments} session={session} sessionData={sessionData} onClose={() => { setModal(null); setSession(null) }} onVerify={engine.verify} onSelectSession={openSession} /> : null}
  </div>
}
