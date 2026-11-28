'use client'

import { useEffect, useRef, useState } from 'react'
import { call, getBridge, matchesChatRun, newRunId, type ChatRunIdentity } from './bridge'

export function formatClock(seconds: number) {
  const n = Math.max(0, Number(seconds) || 0)
  return `${String(Math.floor(n / 3600)).padStart(2, '0')}:${String(Math.floor(n / 60) % 60).padStart(2, '0')}:${String(Math.floor(n % 60)).padStart(2, '0')}`
}
export function projectLabel(path: string) { return String(path || '').replace(/[\\/]+$/, '').split(/[\\/]/).pop() || '选择项目' }
const SESSION_KINDS: Record<string, string> = { go: '实时会议', record: '录制', replay: '回放', transcribe: '导入的转写' }
export function sessionKindLabel(sid: string) { return SESSION_KINDS[String(sid || '').split('_')[0]] || 'Meeting' }
export function sessionTimestamp(session: any): Date | null {
  const seconds = Number(session?.mtime || 0)
  return seconds > 0 ? new Date(seconds * 1000) : null
}
export function formatSessionStamp(date: Date) {
  const monthDay = new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric' }).format(date)
  const time = new Intl.DateTimeFormat('zh-CN', { hour: 'numeric', minute: '2-digit' }).format(date)
  return `${monthDay} · ${time}`
}
export function sessionFallbackTitle(session: any) {
  const stamp = sessionTimestamp(session)
  const kind = sessionKindLabel(session?.sid)
  return stamp ? `${kind} · ${formatSessionStamp(stamp)}` : kind
}
export function sessionTitle(session: any) {
  const custom = String(session?.title || '').trim()
  return custom || session?.name || sessionFallbackTitle(session)
}

export function useMeetingVAR() {
  const [desktop, setDesktop] = useState<boolean | null>(null)
  const [project, setProject] = useState('')
  const [config, setConfig] = useState<any>({})
  const [sessions, setSessions] = useState<any[]>([])
  const [projects, setProjects] = useState<string[]>([])
  const [tree, setTree] = useState<any[]>([])
  const [treeLoading, setTreeLoading] = useState(false)
  const [fileViewer, setFileViewer] = useState<any>(null)
  const [packs, setPacks] = useState<any[]>([])
  const [ledger, setLedger] = useState<any[]>([])
  const [moments, set时刻] = useState<any[]>([])
  const [recording, set录制] = useState(false)
  const [notice, setNotice] = useState('')
  const [pending, setPending] = useState(false)
  const [activeRun, setActiveRun] = useState<{ id: string; events: any[]; live: string } | null>(null)
  const [lastUsage, setLastUsage] = useState<any>(null)
  const [live, setLive] = useState<any>({ startedAt: 0, phase: 'idle', screen: {}, partials: {}, lines: [], speakers: [], asks: [], hits: [], interest: '', asr: null, audio: null, summary: null, warnings: [] })
  const projectRef = useRef('')
  const conversationByViewRef = useRef<Record<string, string>>({})
  const epochRef = useRef(0)
  const activeRef = useRef<ChatRunIdentity | null>(null)
  const eventRef = useRef(0)
  const desktopRef = useRef(false)

  async function refreshEvidence(folder = projectRef.current) {
    if (!folder || !desktopRef.current) return
    const [a, b] = await Promise.all([call('evidence_packs', folder), call('commitment_ledger', folder)])
    if (a?.ok) setPacks(a.packs || [])
    if (b?.ok) setLedger(b.rows || b.items || [])
  }
  async function refreshSessions() {
    if (!desktopRef.current) return
    const library = await call('list_library')
    if (Array.isArray(library)) setSessions(library.flatMap((g: any) => (g.sessions || []).map((s: any) => ({ ...s, project: g.project }))))
  }
  async function refreshTree(folder = projectRef.current) {
    if (!folder || !desktopRef.current) { setTree([]); return null }
    setTreeLoading(true)
    try {
      const result = await call('scan_project', folder)
      if (result?.ok) {
        const nodes = Array.isArray(result.tree) ? result.tree : result.tree?.children || []
        setTree(nodes)
        return nodes
      }
      setNotice(result?.error || '无法读取项目文件。')
      return null
    } finally { setTreeLoading(false) }
  }
  async function selectProject(folder: string) {
    if (!folder || !desktopRef.current) return
    epochRef.current += 1; projectRef.current = folder; setProject(folder); setProjects((previous) => [folder, ...previous.filter((item) => item !== folder)]); conversationByViewRef.current = {}; set时刻([]); setTree([])
    const cfg = await call('get_config')
    if (cfg) { setConfig(cfg); setProjects(Array.from(new Set([folder, ...(Array.isArray(cfg.projects) ? cfg.projects : [])]))) }
    await Promise.all([refreshEvidence(folder), refreshSessions(), refreshTree(folder)])
  }
  async function chooseProject() {
    if (!desktopRef.current) { setNotice('请打开 meetingVAR 桌面端以选择本地项目。'); return }
    const result = await call('pick_folder')
    const folder = typeof result === 'string' ? result : result?.path || result?.folder
    if (folder) await selectProject(folder)
  }
  async function openProjectFile(relative: string) {
    if (!projectRef.current || !desktopRef.current) return null
    const result = await call('read_project_file', projectRef.current, relative)
    if (result?.ok) setFileViewer({ ...result, rel: relative, project: projectRef.current })
    else setNotice(result?.error || '文件预览失败。')
    return result
  }
  async function openProjectFileExternal(relative: string) {
    if (!projectRef.current || !desktopRef.current) return null
    const result = await call('open_project_file', projectRef.current, relative)
    if (!result?.ok) setNotice(result?.error || '无法打开文件。')
    return result
  }
  async function send(text: string, conversationKey: string) {
    const content = text.trim()
    if (!content || activeRef.current) return null
    if (!desktopRef.current) { setNotice('桌面引擎未连接。当前只是界面预览，不是 Agent 回复。'); return { role: 'assistant', id: `offline-${Date.now()}`, content: '连接 meetingVAR 桌面端后才能查询真实会议。没有发送 Agent 请求。', createdAt: new Date().toISOString() } }
    if (!projectRef.current) { await chooseProject(); if (!projectRef.current) return null }
    let conversation = conversationByViewRef.current[conversationKey] || ''
    if (!conversation) { const created = await call('chat_new', projectRef.current); conversation = created?.conversation_id || ''; conversationByViewRef.current[conversationKey] = conversation }
    if (!conversation) { setNotice('无法创建对话，请检查桌面引擎。'); return null }
    const identity = { folder: projectRef.current, conversation_id: conversation, run_id: newRunId() }
    const epoch = epochRef.current; activeRef.current = identity; setActiveRun({ id: identity.run_id, events: [], live: '' })
    let liveRun = true; let cursor = 0
    try {
      const baseline = await call('poll_events', 0, false); cursor = Number(baseline?.next || 0)
      const poll = (async () => { while (liveRun && epoch === epochRef.current) { await new Promise((resolve) => setTimeout(resolve, 350)); try { const events = await call('poll_events', cursor, false); if (!liveRun || activeRef.current !== identity) return; cursor = Number(events?.next || cursor); const progress = (events?.events || []).filter((e: any) => e?.type === 'agent_progress' && matchesChatRun(e, identity)); if (progress.length) setActiveRun((previous) => previous ? { ...previous, events: [...previous.events, ...progress].slice(-60), live: previous.live + progress.filter((e: any) => e.phase === 'answer_delta').map((e: any) => String(e.text || '')).join('') } : previous) } catch {} } })()
      let result: any
      try { result = await call('chat_send', identity.folder, conversation, content, 'agent', identity.run_id) } finally { liveRun = false; await poll }
      if (!matchesChatRun(result, identity)) throw new Error('回复与当前项目和对话不匹配。')
      const reply = result?.message || {}
      const segments = reply.segments || result?.segments || []
      if (segments.length) set时刻((previous) => [...previous, ...segments])
      const message = { ...reply, id: `assistant-${identity.run_id}`, role: 'assistant', content: String(reply.content || reply.text || result?.error || '没有返回回答。'), createdAt: new Date(Number(reply.ts || Date.now() / 1000) * 1000).toISOString(), citations: reply.citations || [], segments, ledger: reply.ledger || result?.ledger || [], actions: reply.actions || [], usage: reply.usage || {} }
      if (reply.usage) setLastUsage(reply.usage)
      await refreshEvidence(identity.folder)
      return message
    } catch (error: any) { setNotice(error?.message || String(error)); return { id: `failed-${identity.run_id}`, role: 'assistant', content: `请求失败：${error?.message || error}`, createdAt: new Date().toISOString(), answer_status: 'failed' } } finally { liveRun = false; activeRef.current = null; setActiveRun(null) }
  }
  async function cancel() { if (activeRef.current) await call('chat_cancel', activeRef.current.folder, activeRef.current.conversation_id, activeRef.current.run_id) }
  async function executeAction(action: any) {
    if (!desktopRef.current) { setNotice('桌面引擎未连接，未执行操作。'); return null }
    const conversation = Object.values(conversationByViewRef.current)[0]
    if (!conversation || !projectRef.current) { setNotice('请先开始对话，再批准操作。'); return null }
    const result = await call('chat_action', projectRef.current, conversation, action.id)
    const reply = result?.message || {}
    return { ...reply, id: `action-${action.id}-${Date.now()}`, role: 'assistant', content: String(reply.content || result?.status || '操作状态已返回。'), createdAt: new Date().toISOString(), actions: reply.actions || [], citations: reply.citations || [], segments: reply.segments || [] }
  }
  async function startMeeting(interestFocus = '', askSpeakers = true) {
    if (!desktopRef.current) return { ok: false, error: '浏览器预览中无法录制，请使用桌面应用。' }
    if (!projectRef.current) await chooseProject()
    if (!projectRef.current) return { ok: false, error: '开始会议前请先选择项目。' }
    setPending(true)
    try {
      await call('set_config', { interest_focus: interestFocus.trim() })
      const result = await call('start_meeting', projectRef.current, 'go', Boolean(askSpeakers))
      if (result?.ok) {
        set录制(true)
        setLive({ startedAt: Number(result.started_at ? result.started_at * 1000 : Date.now()), phase: 'loading', screen: {}, partials: {}, lines: [], speakers: [], asks: [], hits: [], interest: '', asr: null, audio: null, summary: null, warnings: [] })
        const baseline = await call('poll_events', 0, false)
        eventRef.current = Number(baseline?.next || 0)
      } else setNotice(result?.error || '无法开始会议')
      return result
    } catch (error: any) { const message = String(error?.message || error); setNotice(message); return { ok: false, error: message } } finally { setPending(false) }
  }
  async function stopMeeting() {
    setPending(true)
    try { const result = await call('stop_meeting'); if (!result?.ok) setNotice(result?.error || '无法结束会议'); return result } finally { setPending(false) }
  }
  async function saveMoment() {
    const result = await call('manual_var')
    setNotice(result?.ok ? '已发送手动标记指令，等待片段回执；尚未验证已保存。' : result?.error || '手动标记失败')
    return result
  }
  async function answerSpeaker(name: string) {
    if (!desktopRef.current) return { ok: false, error: '需要桌面引擎' }
    const result = await call('answer_prompt', name)
    if (result?.ok) setLive((previous: any) => ({ ...previous, asks: previous.asks.slice(1) }))
    else setNotice(result?.error || '说话人姓名保存失败')
    return result
  }
  async function verify(pack: any) { if (!desktopRef.current) return { ok: false, error: '桌面引擎未连接，没有校验证据。' }; return call('evidence_verify', projectRef.current, pack.name) }
  async function refreshConfig() {
    if (!desktopRef.current) return null
    const cfg = await call('get_config')
    if (cfg) setConfig(cfg)
    return cfg
  }
  async function updateConfig(updates: Record<string, any>) {
    setConfig((previous: any) => ({ ...previous, ...updates }))
    if (!desktopRef.current) return null
    const result = await call('set_config', updates)
    if (result && !result.error) setConfig(result)
    return result
  }
  async function sessionDetail(sid: string) { if (!desktopRef.current) return { sid, clips: [], minutes: '' }; return call('session_detail', sid) }
  async function renameSession(sid: string, title: string) {
    const clean = String(title || '').trim().slice(0, 80)
    if (!sid) return { ok: false, error: '缺少会议 ID' }
    if (!desktopRef.current) {
      setSessions((previous) => previous.map((item) => item.sid === sid ? { ...item, title: clean } : item))
      return { ok: true, preview_only: true }
    }
    const result = await call('rename_session', sid, clean)
    if (result?.ok) setSessions((previous) => previous.map((item) => item.sid === sid ? { ...item, title: clean } : item))
    else setNotice(result?.error || '无法重命名这场会议。')
    return result
  }

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const api = await getBridge(); if (cancelled) return
      desktopRef.current = Boolean(api); setDesktop(Boolean(api))
      if (!api) return
      const [info, cfg, library] = await Promise.all([call('bootstrap'), call('get_config'), call('list_library')])
      if (cancelled) return
      setConfig(cfg || {}); set录制(Boolean(info?.meeting_运行Medium)); setProjects(Array.isArray(cfg?.projects) ? cfg.projects : [])
      const path = cfg?.last_project || cfg?.projects?.[0] || ''; projectRef.current = path; setProject(path)
      if (Array.isArray(library)) setSessions(library.flatMap((group: any) => (group.sessions || []).map((session: any) => ({ ...session, project: group.project }))))
      if (path) { await refreshEvidence(path); await refreshTree(path) }
    })()
    return () => { cancelled = true }
  }, [])
  useEffect(() => {
    if (!desktop || !recording) return
    let cancelled = false
    const timer = setInterval(async () => {
      const result = await call('poll_events', eventRef.current, false)
      if (cancelled || !result) return
      eventRef.current = result.next || eventRef.current
      setLive((previous: any) => {
        const next: any = { ...previous, partials: { ...previous.partials } }
        for (const event of result.events || []) {
          if (event.type === 'capture_state') next.phase = event.phase || next.phase
          else if (event.type === 'partial_line') {
            if (event.clear) next.partials = {}
            else if (event.text) next.partials[String(event.id || 'current')] = String(event.text)
            else if (event.id) delete next.partials[String(event.id)]
          } else if (event.type === 'line') {
            const line = { clock: event.clock || '', spk: event.spk || 'speaker', text: event.text || '', id: `line-${Date.now()}-${next.lines.length}` }
            next.lines = [...next.lines, line].slice(-300)
            if (line.spk && !next.speakers.includes(line.spk)) next.speakers = [...next.speakers, line.spk]
            next.partials = {}
          } else if (event.type === 'ask') {
            if (!next.asks.some((ask: any) => ask.spk === event.spk)) next.asks = [...next.asks, { spk: event.spk, sample: event.sample || '' }]
          } else if (event.type === 'interest_state') next.interest = event.message || event.state || ''
          else if (event.type === 'asr_state') next.asr = event
          else if (event.type === 'asr_stream_state') next.asr = { message: event.message, state: event.state }
          else if (event.type === 'audio_state') next.audio = event
          else if (event.type === 'recording_warning') next.warnings = [...next.warnings.slice(-3), event.text || event.message || '录制警告']
          else if (event.type === 'meeting_summary') { next.summary = event; void refreshEvidence() }
          else if (event.type === 'exit') next.phase = 'idle'
        }
        if (result.screen) next.screen = { ...next.screen, ...result.screen }
        if (result.phase) next.phase = result.phase
        return next
      })
      try { const screen = await call('screen_state'); if (!cancelled && screen?.ok) setLive((previous: any) => ({ ...previous, screen: screen.screen || previous.screen, phase: screen.phase || previous.phase })) } catch {}
      if (!result.运行Medium && result.phase === 'idle') {
        set录制(false)
        await refreshSessions()
        await refreshEvidence()
      }
    }, 650)
    return () => { cancelled = true; clearInterval(timer) }
  }, [desktop, recording, project])
  return { desktop, project, config, projects, tree, treeLoading, fileViewer, setFileViewer, sessions, packs, ledger, moments, recording, pending, notice, activeRun, lastUsage, live, setNotice, chooseProject, selectProject, refreshTree, openProjectFile, openProjectFileExternal, send, cancel, executeAction, startMeeting, stopMeeting, saveMoment, answerSpeaker, verify, sessionDetail, renameSession, refreshEvidence, refreshSessions, refreshConfig, updateConfig }
}