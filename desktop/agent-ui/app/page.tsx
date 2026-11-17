'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { call, getBridge, isDesktop, matchesChatRun, newRunId, type ChatRunIdentity, type AnswerMetadata, answerMetadata, subscribeWorkspace, sameProject } from '@/lib/bridge'
import { MarkdownText } from '@/components/MarkdownText'
import { summarizeWorkspaceIssues } from '@/lib/workspace-issues'
import {
  Activity,
  ArrowRight,
  Bell,
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Clock3,
  Code2,
  Crosshair,
  Database,
  Eye,
  FileText,
  Folder,
  FolderOpen,
  Hexagon,
  LayoutDashboard,
  ListChecks,
  LoaderCircle,
  LockKeyhole,
  Maximize2,
  MessageSquare,
  Mic,
  MoreHorizontal,
  Network,
  Orbit,
  PackageCheck,
  Paperclip,
  Pause,
  Play,
  Plus,
  Radio,
  RefreshCw,
  RotateCcw,
  Search,
  Send,
  Settings,
  ShieldCheck,
  Sparkles,
  Square,
  Terminal,
  Users,
  Volume2,
  Waves,
  X,
  Zap,
} from 'lucide-react'

const initialMessages: UiMessage[] = [{ role: 'assistant', text: '我是你的会议证据协作者。\n选择项目，或者发起一场会议；我会把重要结论连回原话、画面和时间戳。', ts: Date.now() / 1000 }]

type NavItem = [string, string, string, any]
type NavGroup = { label: string; items: NavItem[] }
const navGroups: NavGroup[] = [
  { label: 'MISSION CONTROL', items: [
    ['工作台', '总览控制台', '01', LayoutDashboard],
    ['会议对话', 'AI 协作者', '02', MessageSquare],
    ['会议纪要', '会议回放', '03', Clock3],
    ['录屏', '实时信号', '04', Mic],
  ] },
  { label: 'EVIDENCE NETWORK', items: [
    ['证据包', '证据保险库', '05', PackageCheck],
    ['承诺账本', '行动与承诺', '06', ListChecks],
    ['找回 30 秒', '时光回溯', '07', Search],
  ] },
  { label: 'PROJECT INTELLIGENCE', items: [
    ['项目', '项目空间', '08', FolderOpen],
    ['项目架构', '系统蓝图', '09', Network],
  ] },
] as const

type UiFile = { name: string; type: 'folder' | 'file'; dir?: boolean; rel?: string; indent?: number; kind?: string; suffix?: string; size?: number; kindLabel?: string; open?: boolean; active?: boolean; children?: UiFile[] }
type UiMessage = AnswerMetadata & {
  id?: string
  role: 'assistant' | 'user'
  text: string
  actions?: any[]
  citations?: any[]
  facts?: any[]
  focus?: any[]
  ts?: number
  thinking?: boolean
  thinkingMode?: 'agent' | 'chat'
  startedAt?: number
  liveEvents?: any[]
  liveAnswer?: string
  liveThought?: string
  plan?: any[]
  observations?: any[]
}

function projectName(path: string): string {
  return String(path || '').replace(/[\\/]+$/, '').split(/[\\/]/).pop() || '未命名项目'
}
function relativeTime(ts: number): string {
  if (!ts) return ''
  const seconds = Math.max(0, Math.round(Date.now() / 1000 - Number(ts)))
  if (seconds < 60) return `${seconds} 秒`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时`
  return `${Math.floor(seconds / 86400)} 天`
}
function sessionLabel(session: any): string {
  return String(session?.title || session?.name || session?.sid || '会议回放')
}
function formatBytes(size: number): string {
  if (!size) return '0 KB'
  return size > 1048576 ? `${(size / 1048576).toFixed(1)} MB` : `${Math.round(size / 1024)} KB`
}
function formatTime(value: number | string): string {
  const seconds = Math.max(0, Number(value) || 0)
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`
}
function flattenTree(nodes: any[], depth = 0): UiFile[] {
  const rows: UiFile[] = []
  for (const node of nodes || []) {
    const dir = Boolean(node.dir)
    rows.push({ name: String(node.name || ''), type: dir ? 'folder' : 'file', open: depth < 1, indent: depth, rel: node.rel || node.name, suffix: node.actual_suffix || node.suffix || '', size: Number(node.size) || 0, kindLabel: node.kind_label || '' })
    if (dir) rows.push(...flattenTree(node.children || [], depth + 1))
  }
  return rows
}
function bridgeText(message: any): string {
  if (!message) return ''
  let text = String(message.content || '')
  if (Array.isArray(message.focus) && message.focus.length) {
    text += '\n\n会前关注\n' + message.focus.map((item: any) => `· ${item.title}${item.why ? ` — ${item.why}` : ''}`).join('\n')
  }
  if (Array.isArray(message.facts) && message.facts.length) {
    text += '\n\n会后事实\n' + message.facts.map((item: any) => `· [${item.fact_type || '事实'}] ${item.title}${item.owner ? ` / ${item.owner}` : ''}${item.due ? ` / ${item.due}` : ''}`).join('\n')
  }
  if (Array.isArray(message.citations) && message.citations.length) {
    const lines = message.citations.slice(0, 12).map((c: any) => {
      const quote = String(c.quote || '').replace(/\s+/g, ' ').trim().slice(0, 140)
      if (c.kind === 'turn') return `- 会议 ${c.session_id || ''}${Array.isArray(c.turn_ids) ? ` / ${c.turn_ids.join(',')}` : ''}${quote ? `：${quote}` : ''}`
      if (c.kind === 'document' || c.path) {
        const loc = (c.page ?? c.page_start) != null ? `第${c.page ?? c.page_start}页` : c.line_start != null ? `第${c.line_start}行` : ''
        return `- 文件 ${c.title || c.path || c.source_id || c.chunk_id || '资料'}${loc ? `（${loc}）` : ''}${quote ? `：${quote}` : ''}`
      }
      return `- ${c.name || c.title || c.kind || '依据'}${quote ? `：${quote}` : ''}`
    })
    if (lines.length) text += '\n\n依据：\n' + lines.join('\n')
  }
  return text
}
function IconButton({ label, onClick, children, className = '' }: { label: string; onClick?: () => void; children: React.ReactNode; className?: string }) {
  return <button className={`hud-icon ${className}`} aria-label={label} title={label} onClick={onClick}>{children}</button>
}

function LiveSignal({ recording, active = false }: { recording: boolean; active?: boolean }) {
  return <div className={`signal-orbit ${recording ? 'is-live' : ''} ${active ? 'compact' : ''}`} aria-hidden="true">
    <div className="orbit orbit-one" /><div className="orbit orbit-two" />
    <div className="orbit-core"><Waves size={active ? 19 : 34} /></div>
    {Array.from({ length: 24 }).map((_, index) => <i key={index} style={{ '--i': index } as React.CSSProperties} />)}
  </div>
}

function EvidenceStatus({ value, tone = 'neutral' }: { value: string; tone?: string }) {
  return <span className={`status-tag ${tone}`}><i />{value}</span>
}

function AutoProjectCard({ state, onRefresh, onSettings, modelInfo, onVerifyModel }: { state: any; onRefresh: () => void; onSettings?: () => void; modelInfo?: any; onVerifyModel?: () => void }) {
  const [open, setOpen] = useState(false)
  useEffect(() => { setOpen(false) }, [state?.folder])
  const result = { ...(state?.result || {}), ...state }
  const errors = summarizeWorkspaceIssues(result?.errors || state?.errors || [])
  const coverage = result?.coverage || {}
  const map = coverage?.map || {}
  const totals = result?.map_totals || {}
  const statusLabel = state?.job_status === 'oriented' ? '项目地图已就绪' : state?.job_status === 'running' ? '正在建立认知' : state?.job_status === 'paused_budget' ? '探索已暂停，可继续' : state?.job_status === 'mapped_awaiting_model' ? '地图完成，等待模型' : state?.job_status === 'paused_model' ? '模型输出异常，可重试' : state?.job_status === 'paused_provider' ? '模型服务待恢复' : '认知需要补充'
  return <section className={`auto-project intelligence-strip ${open ? '' : 'collapsed'}`}>
    <div className="auto-project-head">
      <div className="auto-project-icon"><Orbit size={15} /></div>
      <div className="auto-project-title"><b>{statusLabel}</b><small>关键认知是带来源的暂定理解，不代表全库逐字读完。</small></div>
      <span className="auto-running">{state?.job_status === 'running' ? 'SYNCING' : state?.job_status === 'oriented' ? 'ONLINE' : 'ATTENTION'}</span>
      <button className="auto-project-toggle" onClick={() => setOpen((value) => !value)}>{open ? '收起' : '详情'}<ChevronDown size={13} /></button>
      <button className="module-button auto-refresh" onClick={onRefresh}>同步</button>
    </div>
    {open ? <div className="auto-project-body">
      <div className="auto-line"><span>项目</span><div>{state?.folder || '未选择项目'}</div></div>
      <div className="auto-line"><span>进度</span><div>{state?.stage || state?.job_status} · {state?.progress || '—'}</div></div>
      <div className="auto-stats"><b>{totals.current_sources || map.processed || 0} 个来源</b><b>{totals.explored_units || 0} 已读</b><b>{totals.unseen_units || 0} 待查</b><b>{totals.failed_units || 0} 失败</b></div>
      {result?.overview ? <div className="auto-overview">{result.overview}</div> : null}
      {modelInfo?.error ? <div className="auto-warn">{modelInfo.error}</div> : null}
      {onVerifyModel ? <button className="module-button" onClick={onVerifyModel}>验证模型后继续</button> : null}
      {onSettings ? <button className="module-button" onClick={onSettings}>模型设置</button> : null}
      {errors.groups.map((group: any) => <div className="auto-warn" key={group.label}>{group.label} × {group.count}</div>)}
      {state?.error ? <div className="auto-warn">{state.error}</div> : null}
      <details className="activity-plan"><summary>原始状态（调试）</summary><pre>{JSON.stringify({ coverage, errors: result.errors || [] }, null, 2)}</pre></details>
    </div> : null}
  </section>
}

function ArchitecturePanel({ project }: { project: string }) {
  const [data, setData] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [owners, setOwners] = useState<Record<string, string>>({})
  const [manual, setManual] = useState<any[]>([])
  const [showAdd, setShowAdd] = useState(false)
  const [selected, setSelected] = useState('')
  const [draft, setDraft] = useState({ name: '', purpose: '', layer: 'core', owner: '', files: '' })
  const autoGenerateTried = useRef(false)
  function apply(result: any) {
    setData(result)
    setOwners(result?.owners || {})
    setManual(Array.isArray(result?.manual_modules) ? result.manual_modules : [])
  }
  async function load() {
    if (!project || !isDesktop()) { setData({ ok: true, architecture_status: 'not_built', groups: [], edges: [], generation: { mode: 'none' } }); return }
    setBusy(true); setError('')
    try {
      const result = await call('architecture', project)
      apply(result)
      if (!result?.ok) setError(result?.error || '项目架构读取失败')
      if (result?.ok && result?.architecture_status === 'complete' && result?.generation?.mode !== 'ai' && (result?.coverage?.total || 0) > 0 && !autoGenerateTried.current) {
        autoGenerateTried.current = true
        void generate()
      }
    } catch (issue: any) { setError(String(issue?.message || issue)) } finally { setBusy(false) }
  }
  useEffect(() => { autoGenerateTried.current = false; void load() }, [project])
  async function generate() {
    if (!project || !isDesktop() || generating) { if (!project) setError('先选择项目文件夹并建立地图，再生成架构。'); return }
    setGenerating(true); setError(''); setNotice('正在分析仓库地图并归纳模块…')
    try {
      const result = await call('architecture_generate', project)
      if (!result?.ok) { setError(result?.error || 'AI 架构生成失败'); setNotice(''); return }
      apply(result); setNotice(`AI 已归纳 ${result.groups?.length || 0} 个模块。`)
    } catch (issue: any) { setError(String(issue?.message || issue)); setNotice('') } finally { setGenerating(false) }
  }
  async function save(nextManual = manual, nextOwners = owners) {
    if (!project || !isDesktop() || saving) return
    setSaving(true); setError('')
    try {
      const result = await call('architecture_save', project, JSON.stringify({ owners: nextOwners, modules: nextManual }))
      if (!result?.ok) { setError(result?.error || '架构保存失败'); return }
      apply(result); setNotice('已保存模块与负责人。')
    } catch (issue: any) { setError(String(issue?.message || issue)) } finally { setSaving(false) }
  }
  function addManual() {
    if (!draft.name.trim()) { setError('请先填写模块名称'); return }
    const module = { ...draft, id: `manual-${Date.now().toString(36)}`, name: draft.name.trim(), files: draft.files.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean) }
    const next = [...manual, module]
    setManual(next); setShowAdd(false); setDraft({ name: '', purpose: '', layer: 'core', owner: '', files: '' })
    void save(next, { ...owners, [module.id]: module.owner })
  }
  const groups = Array.isArray(data?.groups) ? data.groups : []
  const edges = Array.isArray(data?.edges) ? data.edges : []
  const generation = data?.generation || {}
  const byId: Record<string, any> = Object.fromEntries(groups.map((group: any) => [group.id, group]))
  const positions: Record<string, { x: number; y: number }> = {}
  groups.forEach((group: any, index: number) => {
    const columns = Math.min(4, Math.max(2, Math.ceil(Math.sqrt(groups.length))))
    positions[group.id] = { x: 100 + (index % columns) * (640 / Math.max(1, columns - 1)), y: 90 + Math.floor(index / columns) * 125 }
  })
  const diagramHeight = Math.max(360, Math.ceil(groups.length / 4) * 125 + 100)
  return <div className="arch-view">
    <div className="arch-head"><div className="arch-stats"><b>{groups.length} 个模块</b><b>{edges.length} 条依赖</b><b>{data?.coverage?.total ?? 0} 个来源</b><b>负责人 {groups.filter((g: any) => g.owner && g.owner !== '未指定').length}/{groups.length}</b></div>
      <div className="arch-actions"><button className="module-button arch-ai-button" onClick={() => void generate()} disabled={busy || generating || saving}>{generating ? '模型分析中…' : 'AI 生成架构'}</button><button className="module-button" onClick={() => setShowAdd((v) => !v)}>添加模块</button><button className="module-button" onClick={() => void save()} disabled={saving}>{saving ? '保存中…' : '保存修改'}</button><button className="module-button" onClick={() => void load()}>{busy ? '读取中…' : '刷新'}</button></div></div>
    {notice ? <div className="arch-notice">{notice}</div> : null}{error ? <div className="module-empty error-text">{error}</div> : null}
    <div className={`arch-generation ${generation.stale ? 'stale' : ''}`}><span>{generation.mode === 'ai' ? `AI 归纳 · ${generation.model || '当前模型'} · ${generation.generated_at || ''}` : '静态地图投影 · 尚未调用模型'}</span>{generation.stale ? <b>地图已有更新，建议重新生成</b> : null}</div>
    {generation.summary ? <div className="arch-summary">{generation.summary}</div> : null}
    {data?.architecture_status === 'not_built' ? <div className="module-empty">项目地图尚未建立；先在项目空间点“同步”。</div> : null}
    {showAdd ? <div className="arch-form"><input className="module-input" placeholder="模块名称" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /><input className="module-input" placeholder="模块职责" value={draft.purpose} onChange={(e) => setDraft({ ...draft, purpose: e.target.value })} /><div className="arch-form-row"><input className="module-input" placeholder="负责人" value={draft.owner} onChange={(e) => setDraft({ ...draft, owner: e.target.value })} /><input className="module-input" placeholder="文件路径，逗号分隔" value={draft.files} onChange={(e) => setDraft({ ...draft, files: e.target.value })} /></div><button className="module-button" onClick={addManual}>添加到架构</button></div> : null}
    {groups.length ? <div className="blueprint-shell"><div className="blueprint-label"><Crosshair size={13} /> SYSTEM TOPOLOGY <span>点击节点查看模块</span></div><svg viewBox={`0 0 840 ${diagramHeight}`} className="blueprint-canvas" role="img" aria-label="项目模块依赖图">
      <defs><pattern id="blueprint-grid" width="28" height="28" patternUnits="userSpaceOnUse"><path d="M 28 0 L 0 0 0 28" fill="none" stroke="#243c40" strokeWidth=".5" /></pattern><marker id="blue-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0 0L10 5L0 10z" fill="#56d6c9" /></marker></defs>
      <rect width="840" height={diagramHeight} fill="url(#blueprint-grid)" />
      {edges.slice(0, 40).map((edge: any, i: number) => { const a = positions[edge.source]; const b = positions[edge.target]; return a && b ? <path key={`${edge.source}-${edge.target}-${i}`} d={`M${a.x} ${a.y + 20} C${a.x} ${(a.y + b.y) / 2},${b.x} ${(a.y + b.y) / 2},${b.x} ${b.y - 20}`} className="blueprint-edge" markerEnd="url(#blue-arrow)" /> : null })}
      {groups.map((group: any) => { const pos = positions[group.id]; return <g key={group.id} transform={`translate(${pos.x},${pos.y})`} className={`blueprint-node ${selected === group.id ? 'selected' : ''}`} onClick={() => setSelected(selected === group.id ? '' : group.id)}><circle r="27" /><circle r="19" /><text textAnchor="middle" y="4">{String(group.code_files || group.file_count || 0).padStart(2, '0')}</text><text textAnchor="middle" y="48" className="node-title">{String(group.name || group.label).slice(0, 18)}</text><text textAnchor="middle" y="62" className="node-sub">{group.layer_label || group.layer}</text></g> })}
    </svg></div> : null}
    <div className="arch-columns">{groups.map((group: any) => <div className={`arch-card ${selected === group.id ? 'selected' : ''}`} key={group.id}><div className="arch-card-title"><b>{group.name || group.label}</b>{group.source === 'user' ? <span className="arch-badge">手动</span> : <span className="arch-badge arch-badge-ai">AI</span>}</div>{group.purpose ? <div className="arch-purpose">{group.purpose}</div> : null}<small>{group.file_count} 个文件 · {group.layer_label || group.layer}</small><label className="arch-owner"><span>负责人</span><input className="arch-owner-input" placeholder="未指定" value={owners[group.id] ?? (group.owner !== '未指定' ? group.owner : '')} onChange={(event) => setOwners({ ...owners, [group.id]: event.target.value })} /></label><details><summary>文件</summary><ul>{(group.files || []).slice(0, 20).map((file: string) => <li key={file}>{file}</li>)}</ul></details>{group.source === 'user' ? <button className="arch-remove" onClick={() => { const next = manual.filter((item) => item.id !== group.id); setManual(next); void save(next, owners) }}>移除手动模块</button> : null}</div>)}</div>
    {edges.length ? <div className="arch-deps"><div className="arch-layer-title">模块依赖</div>{edges.slice(0, 40).map((edge: any) => <div className="arch-edge" key={`${edge.source}-${edge.target}`}><span>{byId[edge.source]?.name || edge.source}</span><ArrowRight size={12} /><span>{byId[edge.target]?.name || edge.target}</span><small>{edge.label || (edge.count > 1 ? `${edge.count} 次引用` : '')}</small></div>)}</div> : null}
  </div>
}

function ThinkingCard({ startedAt, events = [], answer = '', thought = '', mode = 'agent', onCancel }: { startedAt: number; events?: any[]; answer?: string; thought?: string; mode?: 'agent' | 'chat'; onCancel?: () => void }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer) }, [])
  return <div className="thinking-card"><div className="thinking-head"><LoaderCircle size={13} className="spin" />{mode === 'chat' ? '正在回复…' : '正在检索与核对证据…'}<span className="thinking-time">{Math.max(0, Math.floor((now - startedAt) / 1000))}s</span>{onCancel ? <button className="thinking-stop" onClick={onCancel}>停止</button> : null}</div><div className="thinking-sub">检索会议/项目 → 调用工具 → 核对原文</div>{thought ? <div className="thinking-thought">{thought}</div> : null}{answer ? <div className="thinking-answer">{answer}</div> : null}{events.length ? <div className="activity-feed">{events.filter((event) => event.phase === 'tool').slice(-6).map((event, index) => <div className="activity-row" key={event.call_id || index}><Terminal size={12} /><span className="activity-label">{event.tool || event.name || '工具'}</span><span className="activity-detail">{event.detail || event.path || ''}</span><span className="activity-meta">{event.status === 'done' ? '✓' : event.status === 'error' ? '失败' : '运行中'}</span></div>)}</div> : null}</div>
}

export default function Home() {
  const [activeNav, setActiveNav] = useState('工作台')
  const [message, setMessage] = useState('')
  const [messages, setMessages] = useState<UiMessage[]>(initialMessages)
  const [showSettings, setShowSettings] = useState(false)
  const [showHelp, setShowHelp] = useState(false)
  const [mobileNav, setMobileNav] = useState(false)
  const [showInspector, setShowInspector] = useState(true)
  const [rightTab, setRightTab] = useState<'files' | 'notes'>('notes')
  const [files, setFiles] = useState<UiFile[]>([])
  const [projects, setProjects] = useState<any[]>([])
  const [sessionDetail, setSessionDetail] = useState<any>(null)
  const [sessionFilter, setSessionFilter] = useState('all')
  const [loadedProject, setLoadedProject] = useState('')
  const [currentProject, setCurrentProject] = useState('')
  const [meetingTitle, setMeetingTitle] = useState('新会议')
  const [recording, setRecording] = useState(false)
  const [finishing, setFinishing] = useState(false)
  const [speakerCount, setSpeakerCount] = useState(0)
  const [busy, setBusy] = useState(false)
  const [chatMode, setChatMode] = useState<'agent' | 'chat'>('agent')
  const [modeMenu, setModeMenu] = useState(false)
  const [chatCancelable, setChatCancelable] = useState(false)
  const [conversationId, setConversationId] = useState('')
  const [notes, setNotes] = useState('')
  const [liveCaptions, setLiveCaptions] = useState<Record<string, string>>({})
  const [sessions, setSessions] = useState<any[]>([])
  const [libraryGroups, setLibraryGroups] = useState<any[]>([])
  const [screenState, setScreenState] = useState<any>({})
  const [screenPhase, setScreenPhase] = useState('')
  const [selectedSession, setSelectedSession] = useState<any>(null)
  const [evidencePacks, setEvidencePacks] = useState<any[]>([])
  const [selectedPack, setSelectedPack] = useState<any>(null)
  const [verifyResult, setVerifyResult] = useState<any>(null)
  const [ledgerRows, setLedgerRows] = useState<any[]>([])
  const [momentQuery, setMomentQuery] = useState('')
  const [momentResults, setMomentResults] = useState<any[]>([])
  const [projectAction, setProjectAction] = useState('')
  const [moduleLoading, setModuleLoading] = useState(false)
  const [autoProject, setAutoProject] = useState<any>({ job_status: 'idle', folder: '', stage: '', progress: '', result: null, error: '' })
  const [config, setConfig] = useState<any>({})
  const [llmStatus, setLlmStatus] = useState<any>(null)
  const [modelInfo, setModelInfo] = useState<any>(null)
  const [llmTest, setLlmTest] = useState<{ state: 'idle' | 'testing' | 'ok' | 'error'; message: string }>({ state: 'idle', message: '' })
  const [apiPreset, setApiPreset] = useState('custom')
  const [settingsTab, setSettingsTab] = useState('常规')
  const [viewer, setViewer] = useState<any>(null)
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const conversationRef = useRef<HTMLDivElement>(null)
  const lastEventRef = useRef(0)
  const chatActiveRef = useRef(false)
  const projectEpochRef = useRef(0)
  const activeRunRef = useRef<ChatRunIdentity | null>(null)
  const speakerSetRef = useRef<Set<string>>(new Set())
  const projectRef = useRef('')

  const apiPresets = [
    { id: 'custom', label: '自定义 OpenAI 兼容 API', base: '', model: '' },
    { id: 'deepseek', label: 'DeepSeek', base: 'https://api.deepseek.com/v1', model: 'deepseek-chat' },
    { id: 'agnes', label: 'Agnes', base: 'https://api.agnes.ai/v1', model: 'agnes-3.0-flash' },
  ]
  const apiConfigured = config.llm_provider === 'custom' ? Boolean(config.llm_base_url && config.llm_model) : Boolean(llmStatus?.up)
  const isChatView = activeNav === '会议对话'
  const displayProject = currentProject ? projectName(currentProject) : '未连接项目'
  const signalStatus = recording ? 'LIVE' : 'STANDBY'
  const selectedCaptions = Object.values(liveCaptions).filter(Boolean).slice(-4)
  const sourceCount = Number((autoProject?.map_totals || autoProject?.result?.map_totals)?.current_sources || 0)
  const memoryReady = autoProject.folder === currentProject && autoProject.job_status === 'oriented'
  const projectSessionIds = new Set(libraryGroups.filter((group) => !currentProject || group.project === currentProject).flatMap((group) => (group.sessions || []).map((session: any) => session.sid)))
  const visibleSessions = (currentProject && projectSessionIds.size ? sessions.filter((session) => projectSessionIds.has(session.sid)) : sessions)
  const recentSessions = (sessionFilter === 'minutes' ? visibleSessions.filter((session) => session.minutes) : sessionFilter === 'clips' ? visibleSessions.filter((session) => session.clips) : visibleSessions).slice(0, 12)
  const activeMoment = momentResults[0]
  const activeNavItem = navGroups.reduce<NavItem | undefined>((found, group) => found || group.items.find((item) => item[0] === activeNav), undefined)
  const projectGroups = useMemo(() => {
    const map = new Map<string, any>()
    for (const path of projects) map.set(path, { project: path, name: projectName(path), sessions: [], latest: 0 })
    for (const group of libraryGroups) map.set(group.project || '', { project: group.project || '', name: group.name || projectName(group.project || ''), sessions: group.sessions || [], latest: group.latest || 0 })
    return Array.from(map.values()).filter((group) => group.project || group.sessions.length)
  }, [projects, libraryGroups])

  async function selectProject(path: string, switchView = true, auto = true) {
    if (!path) return
    projectEpochRef.current += 1
    projectRef.current = path
    setCurrentProject(path)
    setMeetingTitle(projectName(path))
    setFiles([]); setSelectedSession(null); setSessionDetail(null); setNotes(''); setLiveCaptions({})
    setActiveNav(switchView ? '工作台' : activeNav)
    const scan = await call('scan_project', path)
    if (scan?.ok && projectRef.current === path) {
      const tree = Array.isArray(scan.tree) ? scan.tree : scan.tree?.children || []
      setFiles(flattenTree(tree)); setLoadedProject(path)
    }
    const loaded = await call('list_library')
    if (Array.isArray(loaded)) setLibraryGroups(loaded)
    if (auto) void startAutoUnderstand(path, null, false)
  }
  async function startAutoUnderstand(folder: string, _unused: any, force: boolean) {
    if (!folder || !isDesktop()) return
    const result = await call('workspace_start', folder, force ? 1 : 0)
    if (!result?.ok) setAutoProject((previous: any) => ({ ...previous, folder, job_status: 'error', error: result?.error || '工作区启动失败' }))
  }
  async function runProjectAction(_action: string) {
    if (!currentProject) { await chooseProject(); return }
    setProjectAction('正在同步项目地图…')
    const result = await call('workspace_start', currentProject, 1)
    setProjectAction(result?.ok ? '同步已启动，可展开认知状态查看进度。' : result?.error || '同步失败')
  }
  async function chooseProject() {
    const result = await call('pick_folder')
    const path = typeof result === 'string' ? result : result?.path || result?.folder
    if (path) await selectProject(path)
  }
  async function refreshFiles() {
    if (!currentProject) return
    const result = await call('scan_project', currentProject)
    if (result?.ok) setFiles(flattenTree(Array.isArray(result.tree) ? result.tree : result.tree?.children || []))
  }
  async function openFile(file: UiFile) {
    if (!currentProject || !file.rel) return
    const result = await call('read_project_file', currentProject, file.rel)
    if (result?.ok) setViewer(result)
  }
  function focusComposer() {
    setActiveNav('会议对话')
    setTimeout(() => composerRef.current?.focus(), 100)
  }
  function openChatWith(text: string) {
    setMessage(text); focusComposer()
  }
  function openSettings(tab = '常规') { setSettingsTab(tab); setShowSettings(true) }
  function openHelp() { setShowHelp(true) }
  async function openSession(session: any) {
    setSelectedSession(session); setMeetingTitle(sessionLabel(session)); setActiveNav('会议纪要')
    if (session.project && session.project !== currentProject) await selectProject(session.project, false, false)
    const result = await call('session_detail', session.sid || session.id)
    setSessionDetail(result || null)
    if (result?.minutes) setNotes(typeof result.minutes === 'string' ? result.minutes : result.minutes.content || JSON.stringify(result.minutes, null, 2))
    if (result?.session?.minutes) setNotes(result.session.minutes)
  }
  async function generateMinutes() {
    if (!selectedSession) return
    setBusy(true)
    const result = await call('digest_session', selectedSession.sid, currentProject)
    const digest = result?.digest || result?.result || {}
    setNotes(result?.minutes || result?.content || digest?.content || digest?.minutes || result?.error || '')
    setBusy(false)
  }
  async function verifyPack(pack: any) {
    setSelectedPack(pack); setVerifyResult(null)
    setVerifyResult(await call('evidence_verify', currentProject, pack.name))
  }
  async function findMoment() {
    if (!currentProject || !momentQuery.trim()) return
    const result = await call('find_moment', currentProject, momentQuery.trim())
    setMomentResults(result?.segments || result?.results || result?.items || [])
  }
  async function sendMessage() {
    const text = message.trim()
    if (!text || chatActiveRef.current) return
    if (!currentProject) { setProjectAction('先选择项目，Agent 才能检索项目证据。'); setMessages((previous) => [...previous, { role: 'assistant', text: '先选择一个项目，我才能检索会议和项目记忆。', answer_status: 'failed' }]); return }
    const epoch = projectEpochRef.current
    let conversation = conversationId
    if (!conversation) {
      const created = await call('chat_new', currentProject)
      conversation = created?.conversation_id || ''
      setConversationId(conversation)
    }
    if (!conversation || epoch !== projectEpochRef.current) { setMessages((previous) => [...previous, { role: 'assistant', text: '对话创建失败，请检查引擎配置。', answer_status: 'failed' }]); return }
    const runId = newRunId()
    const identity = { folder: currentProject, conversation_id: conversation, run_id: runId }
    activeRunRef.current = identity
    const stamp = Date.now()
    const pendingId = `a-${runId}`
    setMessages((previous) => [...previous, { id: `u-${stamp}`, role: 'user', text, ts: stamp / 1000 }, { id: pendingId, role: 'assistant', text: '', thinking: true, thinkingMode: chatMode, startedAt: stamp, liveEvents: [] }])
    setMessage(''); chatActiveRef.current = true; setChatCancelable(true)
    const replacePending = (next: Partial<UiMessage>) => {
      if (epoch !== projectEpochRef.current) return
      setMessages((previous) => previous.map((item) => item.id === pendingId ? { ...item, ...next, thinking: false } : item))
    }
    let live = true
    let cursor = 0
    try {
      const baseline = await call('poll_events', 0, false)
      cursor = Number(baseline?.next || 0)
      const poll = (async () => {
        while (live && epoch === projectEpochRef.current) {
          await new Promise((resolve) => setTimeout(resolve, 350))
          try {
            const events = await call('poll_events', cursor, false)
            if (!live || epoch !== projectEpochRef.current || activeRunRef.current !== identity) return
            cursor = Number(events?.next || cursor)
            const progress = (events?.events || []).filter((event: any) => event?.type === 'agent_progress' && matchesChatRun(event, identity))
            if (progress.length) {
              const answerDelta = progress.filter((event: any) => event?.phase === 'answer_delta' && event?.text).map((event: any) => String(event.text)).join('')
              const thoughtDelta = progress.filter((event: any) => event?.phase === 'thought_delta' && event?.text).map((event: any) => String(event.text)).join('')
              setMessages((previous) => previous.map((item) => item.id === pendingId ? { ...item, liveEvents: [...(item.liveEvents || []), ...progress].slice(-80), liveAnswer: (item.liveAnswer || '') + answerDelta, liveThought: (item.liveThought || '') + thoughtDelta } : item))
            }
          } catch { /* transient bridge error */ }
        }
      })()
      let result: any
      try { result = await call('chat_send', currentProject, conversation, text, chatMode, runId) } finally { live = false; await poll }
      if (epoch !== projectEpochRef.current) return
      if (!matchesChatRun(result, identity)) throw new Error('聊天响应运行身份不匹配')
      if (!result?.ok || !result?.message) {
        replacePending({ ...answerMetadata(result), answer_status: 'failed', text: result?.error || '这次没有完成。' })
        return
      }
      const reply = result.message
      replacePending({ ...answerMetadata(reply), text: bridgeText(reply), actions: reply.actions || [], citations: reply.citations || [], ts: Number(reply.ts) || Date.now() / 1000 })
      const [packs, ledger] = await Promise.all([call('evidence_packs', currentProject), call('commitment_ledger', currentProject)])
      if (packs?.ok) setEvidencePacks(packs.packs || [])
      if (ledger?.ok) setLedgerRows(ledger.rows || [])
    } catch (error: any) {
      replacePending({ answer_status: 'failed', reason: String(error?.message || error), text: `调用失败：${error?.message || error}` })
    } finally {
      live = false
      if (epoch === projectEpochRef.current) { chatActiveRef.current = false; setChatCancelable(false); activeRunRef.current = null }
    }
  }
  async function executeAction(action: any) {
    if (!currentProject || !conversationId || busy || action.status && !['pending', 'awaiting_approval'].includes(action.status)) return
    const epoch = projectEpochRef.current
    const project = currentProject
    const cid = conversationId
    setBusy(true)
    const applyStatus = (status: string) => setMessages((previous) => previous.map((item) => ({ ...item, actions: (item.actions || []).map((row: any) => row.id === action.id ? { ...row, status } : row) })))
    try {
      const result = await call('chat_action', project, cid, action.id)
      if (epoch !== projectEpochRef.current) return
      if (result?.message) setMessages((previous) => [...previous, { ...answerMetadata(result.message), role: 'assistant', text: bridgeText(result.message), actions: result.message.actions || [] }])
      let status = String(result?.status || 'unverified')
      applyStatus(status)
      if (['running', 'approved', 'confirmed', 'cancel_requested', 'pending'].includes(status)) {
        for (let attempt = 0; attempt < 900 && epoch === projectEpochRef.current; attempt += 1) {
          await new Promise((resolve) => setTimeout(resolve, 1000))
          const state = await call('chat_action_status', project, cid, action.id)
          if (!state?.ok) break
          status = String(state.status || 'running')
          applyStatus(status)
          if (!['running', 'approved', 'confirmed', 'cancel_requested', 'pending'].includes(status)) break
        }
      }
      if (['done', 'succeeded'].includes(status)) {
        if (action.type === 'start_meeting') setRecording(true)
        if (action.type === 'stop_meeting') setRecording(false)
        if (['write_file', 'write_document'].includes(action.type)) { setShowInspector(true); setRightTab('files'); await refreshFiles() }
        if (!['start_meeting', 'stop_meeting'].includes(String(action.type || ''))) await startAutoUnderstand(project, null, false)
      }
    } catch (error: any) { setProjectAction(`操作未完成：${error?.message || error}`) } finally { if (epoch === projectEpochRef.current) setBusy(false) }
  }
  async function cancelChat() {
    const identity = activeRunRef.current
    if (identity) await call('chat_cancel', identity.folder, identity.conversation_id, identity.run_id)
    setChatCancelable(false)
  }
  async function toggleRecording() {
    if (busy) return
    if (!currentProject) { await chooseProject(); if (!projectRef.current) { setProjectAction('请先连接项目文件夹，再发起会议。'); return } }
    setBusy(true)
    try {
      const result = recording ? await call('stop_meeting') : await call('start_meeting', currentProject)
      if (!result?.ok) { setProjectAction(result?.error || '会议操作失败'); return }
      if (recording) { setFinishing(true); setRecording(false) } else { setRecording(true); setActiveNav('录屏'); setLiveCaptions({}); speakerSetRef.current.clear(); setSpeakerCount(0) }
    } finally { setBusy(false) }
  }
  function settingsUpdates() {
    return {
      engine_python: config.engine_python || '', repo_dir: config.repo_dir || '',
      device: config.device || 'auto', audio_source: config.audio_source || 'both',
      asr_engine: config.asr_engine || 'local', interest_mode: config.interest_mode || 'manual',
      llm_provider: config.llm_provider || 'local', llm_base_url: config.llm_base_url || '',
      llm_model: config.llm_model || '', llm_api_key: config.llm_api_key || '',
      llm_reasoning_effort: config.llm_reasoning_effort || '',
    }
  }
  async function saveSettings() {
    const result = await call('set_config', settingsUpdates())
    if (result && typeof result === 'object' && !result.error) {
      setConfig(result); setShowSettings(false)
    } else setLlmTest({ state: 'error', message: result?.error || '设置保存失败' })
  }
  async function testLLMConnection() {
    const updates = settingsUpdates()
    if (updates.llm_provider === 'custom' && (!updates.llm_base_url.trim() || !updates.llm_model.trim())) {
      setLlmTest({ state: 'error', message: '请先填写 Base URL 和模型名。' }); return
    }
    setLlmTest({ state: 'testing', message: '已保存配置，正在真实调用模型…' })
    await call('set_config', updates)
    const result = await call('test_llm')
    if (result?.ok) {
      const model = result?.models?.[0] || updates.llm_model || '模型'
      setLlmTest({ state: 'ok', message: `连接成功：${model}${result?.note ? ` · ${result.note}` : ''}` })
      setLlmStatus({ up: true, model })
    } else setLlmTest({ state: 'error', message: result?.error || '连接失败' })
  }
  function openMoment(segment: any) {
    setNotes(segment.text || '')
    setRightTab('notes'); setShowInspector(true)
  }

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const api = await getBridge()
      if (!api || cancelled) return
      await call('page_ready', { page: 'home', version: '0.28.4' })
      await call('client_metrics', { innerWidth: window.innerWidth, innerHeight: window.innerHeight, dpr: window.devicePixelRatio, screenWidth: window.screen.width, screenHeight: window.screen.height, outerWidth: window.outerWidth, outerHeight: window.outerHeight })
      try {
        const [info, cfg, allSessions, groups] = await Promise.all([call('bootstrap'), call('get_config'), call('list_sessions'), call('list_library')])
        if (cancelled) return
        if (info?.meeting_running) setRecording(true)
        setConfig(cfg || {}); setLlmStatus(info?.llm || null)
        const configured = Array.isArray(cfg?.projects) ? cfg.projects : []
        setProjects(configured)
        if (Array.isArray(allSessions)) setSessions(allSessions)
        if (Array.isArray(groups)) setLibraryGroups(groups)
        const project = cfg?.last_project || configured[0] || ''
        if (project) await selectProject(project, false, true)
      } catch { /* browser preview remains a visual shell */ }
    })()
    return () => { cancelled = true }
  }, [])
  useEffect(() => {
    if (!currentProject || !isDesktop()) return
    return subscribeWorkspace(currentProject, (snapshot) => setAutoProject(snapshot), (error) => setAutoProject((previous: any) => ({ ...previous, error: error.message })))
  }, [currentProject])
  useEffect(() => {
    if (!isDesktop() || !recording && !finishing) return
    let cancelled = false
    const timer = setInterval(async () => {
      const result = await call('poll_events', lastEventRef.current, false)
      if (cancelled || !result) return
      lastEventRef.current = result.next || lastEventRef.current
      setScreenState(result.screen || {}); setScreenPhase(result.phase || '')
      if (result.running === false && recording && result.phase !== 'idle') setFinishing(true)
      if (result.running) { setRecording(true); setFinishing(false) }
      if (result.phase === 'idle') { setFinishing(false); setRecording(false) }
      for (const event of result.events || []) {
        if (event?.type === 'partial_line') {
          setLiveCaptions((previous) => {
            let next = { ...previous }
            if (event.clear) return {}
            const key = String(event.id || 'current')
            if (event.text) next[key] = String(event.text)
            else delete next[key]
            return next
          })
        }
        if (event?.type === 'line') {
          setLiveCaptions({})
          if (event.text) setNotes((previous) => [previous, `[${event.clock || ''}] ${event.spk || 'speaker'}: ${event.text}`].filter(Boolean).join(String.fromCharCode(10)))
          if (event.spk) { speakerSetRef.current.add(String(event.spk)); setSpeakerCount(speakerSetRef.current.size) }
        }
        if (event?.type === 'capture_state' && event.phase === 'idle') { setRecording(false); setFinishing(false) }
        if (event?.type === 'meeting_summary') { setRecording(false); setFinishing(false); setActiveNav('会议纪要'); const updated = await call('list_sessions'); if (Array.isArray(updated)) setSessions(updated) }
        if (event?.type === 'agent_progress' && activeRunRef.current && matchesChatRun(event, activeRunRef.current)) {
          setMessages((previous) => previous.map((item) => item.thinking ? { ...item, liveEvents: [...(item.liveEvents || []), event].slice(-20), liveAnswer: event.answer || item.liveAnswer, liveThought: event.thought || item.liveThought } : item))
        }
      }
    }, 800)
    return () => { cancelled = true; clearInterval(timer) }
  }, [recording])
  useEffect(() => {
    if (!isDesktop() || activeNav !== '录屏') return
    let cancelled = false
    const update = async () => {
      const result = await call('screen_state')
      if (!cancelled && result?.ok) { setScreenState(result.screen || {}); setScreenPhase(result.phase || '') }
    }
    void update()
    const timer = setInterval(update, 900)
    return () => { cancelled = true; clearInterval(timer) }
  }, [activeNav])
  useEffect(() => {
    if (!selectedSession || activeNav !== '会议纪要') return
    const sid = selectedSession.sid
    ;(async () => {
      const result = await call('session_detail', sid)
      setSessionDetail(result || null)
      if (result?.minutes) setNotes(result.minutes)
    })()
  }, [currentProject])
  useEffect(() => { conversationRef.current?.scrollTo({ top: conversationRef.current.scrollHeight, behavior: 'smooth' }) }, [messages])
  useEffect(() => {
    if (!currentProject || !isDesktop() || activeNav === '工作台' || activeNav === '会议对话' || activeNav === '项目架构') return
    let cancelled = false
    setModuleLoading(true)
    ;(async () => {
      try {
        if (activeNav === '会议纪要') { const result = await call('list_sessions'); if (!cancelled && Array.isArray(result)) setSessions(result) }
        if (activeNav === '证据包') { const result = await call('evidence_packs', currentProject); if (!cancelled && result?.ok) setEvidencePacks(result.packs || []) }
        if (activeNav === '承诺账本') { const result = await call('commitment_ledger', currentProject); if (!cancelled && result?.ok) setLedgerRows(result.rows || result.items || result.commitments || []) }
      } finally { if (!cancelled) setModuleLoading(false) }
    })()
    return () => { cancelled = true }
  }, [activeNav, currentProject])

  return <main className="var-shell">
    <div className="ambient-grid" /><div className="ambient-glow" />
    <aside className={`var-sidebar ${mobileNav ? 'mobile-open' : ''}`}>
      <div className="var-brand"><div className="brand-emblem"><Hexagon size={29} /><span>V</span></div><div><b>meeting<span>VAR</span></b><small>EVIDENCE INTELLIGENCE</small></div></div>
      <button className="mission-project" onClick={() => void chooseProject()}><span className="project-glyph"><FolderOpen size={17} /></span><div><small>ACTIVE WORKSPACE</small><b>{displayProject}</b></div><ChevronDown size={14} /></button>
      <nav className="command-nav">{navGroups.map((group) => <div className="command-group" key={group.label}><div className="command-label">{group.label}</div>{group.items.map(([value, label, code, Icon]) => <button key={value} className={activeNav === value ? 'active' : ''} onClick={() => { setActiveNav(value); setMobileNav(false) }}><span className="nav-code">{code}</span><Icon size={16} /><span>{label}</span>{activeNav === value ? <i /> : null}</button>)}</div>)}</nav>
      <div className="sidebar-status"><div className="status-line"><span className={`pulse-dot ${recording ? 'recording' : ''}`} /><span>{recording ? '证据信号采集中' : '本地引擎待命中'}</span><b>{signalStatus}</b></div><div className="signal-meter">{Array.from({ length: 16 }).map((_, i) => <i key={i} style={{ height: `${4 + ((i * 7) % 13)}px` }} />)}</div><div className="privacy-line"><LockKeyhole size={11} /> LOCAL-FIRST · SOURCE-BOUND</div></div>
      <div className="sidebar-footer"><button onClick={() => setShowSettings(true)}><Settings size={15} />系统设置</button><button onClick={openHelp}><CircleHelp size={15} />操作指南</button><div className="operator"><span>DX</span><div><b>个人工作区</b><small>Local operator</small></div><button className="hud-icon" aria-label="账户设置" onClick={() => openSettings('我的账户')}><MoreHorizontal size={15} /></button></div></div>
    </aside>

    <section className="var-main">
      <header className="command-topbar"><div className="command-breadcrumb"><button className="mobile-menu" aria-label="打开功能导航" onClick={() => setMobileNav((value) => !value)}><LayoutDashboard size={16} /></button><span>VAR</span><i>/</i><b>{displayProject}</b><i>/</i><span>{activeNavItem?.[1] || '控制台'}</span></div><div className="topbar-center"><span /><span>LOCAL EVIDENCE ENGINE</span><span /></div><div className="topbar-actions"><button className="system-health" onClick={() => openSettings('模型服务')}><span className={apiConfigured ? 'ok' : ''} />{apiConfigured ? '模型在线' : '模型未接入'}</button><IconButton label="搜索" onClick={focusComposer}><Search size={16} /></IconButton><IconButton label="会议偏好" onClick={() => openSettings('会议偏好')}><Bell size={16} /></IconButton><IconButton label="显示或隐藏面板" onClick={() => setShowInspector((value) => !value)}><Maximize2 size={16} /></IconButton></div></header>

      <div className="workspace-stage">
        {activeNav === '工作台' ? <div className="dashboard-view">
          <section className="hero-console hud-panel">
            <div className="hero-copy"><div className="hero-eyebrow"><span className={recording ? 'live' : ''} />{recording ? 'CAPTURE IN PROGRESS' : 'READY FOR THE NEXT MOMENT'}</div><h1>别只记住会议。<br /><em>证明它。</em></h1><p>把重要结论连接回原话、画面和时间戳。<br />你的个人会议证据层，从这里开始。</p><div className="hero-actions"><button className={`primary-action ${recording ? 'recording' : ''}`} onClick={() => void toggleRecording()} disabled={busy || finishing}>{recording ? <Square size={15} /> : <Play size={15} />}<span>{finishing ? '正在收尾…' : recording ? '结束并固化证据' : '发起一场会议'}</span><kbd>↵</kbd></button><button className="secondary-action" onClick={() => void chooseProject()}><FolderOpen size={15} />连接项目</button></div></div>
            <button className={`capture-orb ${recording ? 'recording' : ''}`} onClick={() => void toggleRecording()} disabled={busy || finishing} aria-label={recording ? '结束会议' : '开始会议'}><LiveSignal recording={recording} /><span className="orb-state">{recording ? 'RECORDING' : 'STANDBY'}</span></button>
            <div className="hero-coordinate coordinate-a">AUDIO / SCREEN / MEMORY</div><div className="hero-coordinate coordinate-b">EVIDENCE CHANNEL 01</div>
          </section>
          <div className="metric-deck"><button onClick={() => setActiveNav('会议纪要')}><span className="metric-icon cyan"><Clock3 size={16} /></span><div><small>会议回放</small><b>{sessions.length}<i>场</i></b></div><ArrowRight size={14} /></button><button onClick={() => setActiveNav('证据包')}><span className="metric-icon lime"><PackageCheck size={16} /></span><div><small>证据保险库</small><b>{evidencePacks.length}<i>组</i></b></div><ArrowRight size={14} /></button><button onClick={() => setActiveNav('承诺账本')}><span className="metric-icon orange"><ListChecks size={16} /></span><div><small>行动与承诺</small><b>{ledgerRows.length}<i>项</i></b></div><ArrowRight size={14} /></button><button onClick={() => setActiveNav('项目')}><span className="metric-icon violet"><Database size={16} /></span><div><small>项目认知</small><b>{sourceCount || '—'}<i>{sourceCount ? '源' : ''}</i></b></div><ArrowRight size={14} /></button></div>
          {autoProject.job_status !== 'idle' && autoProject.folder === currentProject ? <AutoProjectCard state={autoProject} modelInfo={modelInfo} onSettings={() => openSettings('模型服务')} onVerifyModel={() => void runProjectAction('sync')} onRefresh={() => void runProjectAction('sync')} /> : null}
          {selectedCaptions.length ? <div className="live-captions"><b><Radio size={12} />实时字幕</b>{selectedCaptions.map((text, index) => <p key={`${index}-${text}`}>{text}</p>)}</div> : null}
          <div className="dashboard-grid"><section className="hud-panel mission-feed"><div className="panel-heading"><div><span>RECENT TRANSMISSIONS</span><h2>最近的会议</h2></div><button onClick={() => setActiveNav('会议纪要')}>全部回放<ArrowRight size={13} /></button></div>{recentSessions.length ? recentSessions.slice(0, 4).map((session, index) => <button className="transmission-row" key={session.sid || index} onClick={() => void openSession(session)}><span className="transmission-index">{String(index + 1).padStart(2, '0')}</span><div><b>{sessionLabel(session)}</b><small>{session.lines || 0} 句 · {session.clips || 0} 个证据片段 · {session.minutes ? '纪要已生成' : '待生成纪要'}</small></div><span className="transmission-time">{relativeTime(session.mtime)}前</span><ChevronRight size={14} /></button>) : <div className="empty-radar"><Orbit size={28} /><b>等待第一段会议信号</b><p>连接项目并发起会议后，回放与证据会出现在这里。</p></div>}</section>
            <section className="hud-panel quick-protocol"><div className="panel-heading"><div><span>QUICK PROTOCOLS</span><h2>快捷任务</h2></div><Zap size={16} /></div><button onClick={() => openChatWith('根据当前项目与最近会议，准备今天的会议议程')}><span>01</span><div><b>准备会议议程</b><small>从项目上下文与最近承诺开始</small></div><ArrowRight size={14} /></button><button onClick={() => setActiveNav('找回 30 秒')}><span>02</span><div><b>找回关键原话</b><small>定位一句话背后的 30–90 秒</small></div><ArrowRight size={14} /></button><button onClick={() => openChatWith('总结当前项目最近会议的行动项与变化')}><span>03</span><div><b>检查行动变化</b><small>谁在什么时候改变了承诺</small></div><ArrowRight size={14} /></button></section></div>
        </div> : null}

        {isChatView ? <div className="agent-workspace"><div className="agent-heading"><div><div className="eyebrow">AGENT UPLINK · {chatMode.toUpperCase()}</div><h1>和证据协作者对话</h1><p>先给结论，再列出来源；不确定和未读范围会明确标出。</p></div><EvidenceStatus value={apiConfigured ? 'MODEL CONNECTED' : 'MODEL OFFLINE'} tone={apiConfigured ? 'ok' : 'warn'} /></div>{!apiConfigured ? <div className="api-banner"><div><b>接入模型 API 后 Agent 才能工作</b><span>录音和本地转写仍可使用。</span></div><button onClick={() => openSettings('模型服务')}>去接入</button></div> : null}<div className="conversation" ref={conversationRef} aria-live="polite">{messages.map((item, index) => <div className={`message-row ${item.role}`} key={item.id || index}><div className={`message-avatar ${item.role}`}>{item.role === 'assistant' ? <Hexagon size={18} /> : 'DX'}</div><div className="message-body"><div className="message-name">{item.role === 'assistant' ? 'VAR / EVIDENCE AGENT' : '你'}<span>{item.ts ? new Date(item.ts * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : ''}</span></div>{item.thinking ? <ThinkingCard startedAt={item.startedAt || Date.now()} events={item.liveEvents} answer={item.liveAnswer} thought={item.liveThought} mode={item.thinkingMode} onCancel={chatCancelable ? () => void cancelChat() : undefined} /> : null}{item.role === 'assistant' && !item.thinking && item.answer_status ? <div className="answer-state"><ShieldCheck size={12} />{{ answered: '已回答', partial: '部分回答', no_evidence: '缺少证据', failed: '回答失败', awaiting_approval: '等待批准' }[item.answer_status]}</div> : null}{item.text ? item.role === 'assistant' ? <MarkdownText text={item.text} /> : <div className="message-text">{item.text}</div> : null}{item.errors?.length ? <div className="auto-warn">{item.errors.map((error: any, i: number) => <div key={i}>{error.message || error.code || String(error)}</div>)}</div> : null}{item.unsupported?.length ? <div className="auto-warn">未支持的结论：{JSON.stringify(item.unsupported)}</div> : null}{item.coverage ? <details className="activity-plan"><summary>回答覆盖范围</summary><pre>{JSON.stringify(item.coverage, null, 2)}</pre></details> : null}{item.actions?.length ? <div className="suggestions">{item.actions.map((action: any) => <button key={action.id} onClick={() => void executeAction(action)}>{action.label || '确认操作'}<ArrowRight size={13} /></button>)}</div> : null}{index === 0 && messages.length === 1 ? <div className="suggestions"><button onClick={() => setMessage('帮我准备今天的会议议程')}>准备会议议程<ArrowRight size={13} /></button><button onClick={() => setMessage('总结上次会议的行动项')}>总结上次行动项<ArrowRight size={13} /></button></div> : null}</div></div>)}</div><div className="composer-wrap"><div className="composer"><div className="composer-top"><span><Sparkles size={12} />{chatMode === 'agent' ? '检索项目与会议证据' : '自由对话，不调用工具'}</span><span>{displayProject}</span></div><textarea ref={composerRef} value={message} onChange={(event) => setMessage(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) { event.preventDefault(); void sendMessage() } }} placeholder="问一个关于会议、承诺或项目的问题…" rows={3} /><div className="composer-actions"><div><IconButton label="添加附件" onClick={() => void call('pick_media')}><Paperclip size={16} /></IconButton><div className="mode-picker"><button className="model-select" onClick={(event) => { event.stopPropagation(); setModeMenu((value) => !value) }}><Bot size={14} />{chatMode === 'chat' ? 'Chat' : 'Agent'}<ChevronDown size={12} /></button>{modeMenu ? <div className="mode-menu"><button onClick={() => { setChatMode('agent'); setModeMenu(false) }}><b>Agent</b><small>检索与工具调用</small></button><button onClick={() => { setChatMode('chat'); setModeMenu(false) }}><b>Chat</b><small>纯对话</small></button></div> : null}</div></div>{chatCancelable ? <button className="send-button stop" aria-label="停止生成" onClick={() => void cancelChat()}><Square size={12} /></button> : <button className="send-button" aria-label="发送消息" onClick={() => void sendMessage()}><Send size={16} /></button>}</div></div><div className="composer-hint">Enter 发送 · Shift + Enter 换行 · 写入和命令需要确认</div></div></div> : null}

        {!['工作台', '会议对话'].includes(activeNav) ? <div className="module-view"><div className="module-heading"><div><div className="eyebrow">{activeNavItem?.[0] === activeNav ? `MODULE ${activeNavItem?.[2]}` : 'MODULE'}</div><h1>{activeNavItem?.[1] || activeNav}</h1><p>{({ 会议纪要: '从已保存的场次中回到原话、片段与决策。', 项目: '建立可回溯的文件地图、检索索引与项目记忆。', 录屏: '实时采集、回溯缓冲与命中画面。未命中内容不落盘。', 证据包: '带 manifest 与 SHA256 的可校验原始证据。', 承诺账本: '把承诺与后来的变化放在同一条时间线上。', '找回 30 秒': '用自然语言定位会议原话和准确时间戳。', 项目架构: 'AI 归纳的模块、负责人、手动增补与依赖。' } as Record<string, string>)[activeNav] || ''}</p></div><div className="module-heading-actions">{activeNav === '项目' ? <button className="secondary-action" onClick={() => void chooseProject()}><FolderOpen size={14} />选择项目</button> : null}{activeNav === '录屏' ? <button className="primary-action" onClick={() => void toggleRecording()}>{recording ? <Square size={14} /> : <Play size={14} />}{recording ? '结束采集' : '开始采集'}</button> : null}</div></div>
          {activeNav === '项目' && autoProject.job_status !== 'idle' ? <AutoProjectCard state={autoProject} modelInfo={modelInfo} onSettings={() => openSettings('模型服务')} onVerifyModel={() => void runProjectAction('sync')} onRefresh={() => void runProjectAction('sync')} /> : null}
          {moduleLoading ? <div className="module-empty"><LoaderCircle className="spin" size={15} />读取中…</div> : null}
          {activeNav === '会议纪要' && sessionDetail?.clips?.length ? <div className="replay-evidence"><div className="panel-heading"><div><span>SOURCE-BOUND CLIPS</span><h2>原话与画面</h2></div><EvidenceStatus value={`${sessionDetail.clips.length} CLIPS`} tone="ok" /></div><div className="clip-grid">{sessionDetail.clips.map((clip: any, index: number) => <article className="clip-card" key={`${clip.clip_id}-${index}`}><div className="clip-visual">{clip.video_url ? <video src={clip.video_url} controls preload="metadata" /> : clip.hd_url ? <video src={clip.hd_url} controls preload="metadata" /> : clip.frames?.[0]?.url ? <img src={clip.frames[0].url} alt="会议证据画面" /> : <div className="clip-placeholder"><Eye size={23} /><span>原始证据片段</span></div>}</div><div className="clip-info"><span>{formatTime(clip.start_ts)} – {formatTime(clip.end_ts)}</span><b>{clip.evidence || '命中的会议证据'}</b><small>{clip.lines || 0} 句转写 · {clip.layer || 'VAR'}</small>{clip.audio_url ? <audio src={clip.audio_url} controls preload="none" /> : null}</div></article>)}</div></div> : null}
          {activeNav === '会议纪要' ? <><div className="session-filters"><button className={sessionFilter === 'all' ? 'active' : ''} onClick={() => setSessionFilter('all')}>全部场次</button><button className={sessionFilter === 'minutes' ? 'active' : ''} onClick={() => setSessionFilter('minutes')}>有纪要</button><button className={sessionFilter === 'clips' ? 'active' : ''} onClick={() => setSessionFilter('clips')}>有证据片段</button>{selectedSession ? <button className="module-button" onClick={() => void generateMinutes()} disabled={busy}>{busy ? '正在生成…' : '生成当前会议纪要'}</button> : null}</div><div className="session-deck">{recentSessions.length ? recentSessions.map((session, index) => <button className={`session-card ${selectedSession?.sid === session.sid ? 'selected' : ''}`} key={session.sid || index} onClick={() => void openSession(session)}><div className="session-card-top"><span>{String(index + 1).padStart(2, '0')} / REPLAY</span><EvidenceStatus value={session.minutes ? 'MINUTES READY' : 'RAW SESSION'} tone={session.minutes ? 'ok' : 'neutral'} /></div><h3>{sessionLabel(session)}</h3><p>{session.sid}</p><div className="session-card-meta"><span><MessageSquare size={12} />{session.lines || 0} 句</span><span><Eye size={12} />{session.clips || 0} 片段</span><span>{relativeTime(session.mtime)}前</span></div></button>) : <div className="module-empty">还没有会议。返回控制台发起第一场会议。</div>}</div></> : null}
          {activeNav === '项目' ? <div className="project-workspace"><button className="project-identity" onClick={() => void chooseProject()}><FolderOpen size={24} /><div><b>{displayProject}</b><small>{currentProject || '选择项目文件夹，Agent 将自动建立地图与索引'}</small></div><ArrowRight size={18} /></button><div className="module-toolbar"><button className="module-button" onClick={() => void runProjectAction('sync')}><RefreshCw size={13} />同步工作区</button><button className="module-button" onClick={() => setActiveNav('项目架构')}><Network size={13} />查看系统蓝图</button></div>{projectAction ? <div className="arch-notice">{projectAction}</div> : null}<div className="project-file-grid">{files.slice(0, 80).map((file, index) => <button className="file-card" key={`${file.rel}-${index}`} onClick={() => file.type === 'file' ? void openFile(file) : undefined}><span>{file.type === 'folder' ? <Folder size={17} /> : <Code2 size={17} />}</span><div><b>{file.name}</b><small>{file.type === 'folder' ? '文件夹' : `${file.suffix || '文件'} · ${formatBytes(file.size || 0)}`}</small></div></button>)}</div>{!files.length ? <div className="module-empty">{!currentProject ? '选择项目后显示真实文件树。' : loadedProject !== currentProject ? '正在扫描项目文件…' : '当前项目没有可显示的文件。'}</div> : null}</div> : null}
          {activeNav === '录屏' ? <div className="capture-workspace"><div className="capture-monitor"><div className="monitor-corners"><i /><i /><i /><i /></div>{screenState.preview ? <img src={screenState.preview} alt="当前屏幕采集预览" /> : <div className="capture-idle"><LiveSignal recording={recording} /><b>{recording ? '正在接收屏幕信号' : '采集器待命'}</b><p>只有命中关注点的回溯片段才会保存。</p></div>}<div className="monitor-hud"><span>CAM 01 · {screenPhase || 'IDLE'}</span><span>{screenState.fps_target || 5} FPS</span></div></div><div className="screen-meta"><span><Activity size={13} />{screenPhase === 'ready' ? '录屏中' : screenPhase || '采集未运行'}</span><span>{screenState.frame_count || 0} 帧</span><span>{((Number(screenState.buffer_bytes) || 0) / 1048576).toFixed(1)} MB 缓冲</span></div>{screenState.error ? <div className="module-empty">{screenState.error}</div> : null}</div> : null}
          {activeNav === '证据包' ? <div className="evidence-vault">{evidencePacks.length ? evidencePacks.map((pack, index) => <div className="evidence-item" key={pack.name || index}><button onClick={() => void verifyPack(pack)}><div className="vault-icon"><PackageCheck size={22} /></div><div><span>EVIDENCE UNIT {String(index + 1).padStart(2, '0')}</span><b>{pack.name}</b><small>{pack.session_id || '会议'} · {pack.files || 0} 个文件 · {pack.created_at || ''}</small></div><ShieldCheck size={18} /></button>{selectedPack?.name === pack.name ? <div className="verify-panel">{!verifyResult ? '正在校验 SHA256…' : verifyResult.ok ? `✓ SHA256 全部通过 · ${verifyResult.files || 0} 个文件` : `校验失败：${verifyResult.error || '存在不一致文件'}`}{verifyResult?.zip ? <button className="module-button" onClick={() => void call('open_path', verifyResult.zip)}>打开 ZIP</button> : null}</div> : null}</div>) : <div className="module-empty">还没有证据包。在 Agent 对话中说“生成证据包”。<button className="empty-cta" onClick={() => openChatWith('生成证据包')}>去生成</button></div>}</div> : null}
          {activeNav === '承诺账本' ? <div className="ledger-table">{ledgerRows.length ? <><div className="ledger-header"><span>承诺 / 行动</span><span>负责人</span><span>期限</span><span>状态</span></div>{ledgerRows.map((row, index) => <div className="ledger-row" key={`${row.title}-${index}`}><div><b>{row.title}</b>{row.changes?.length ? <small>{row.changes.map((change: any) => `${change.field}: ${change.from} → ${change.to}`).join(' / ')}</small> : null}</div><span>{row.owner || '未指定'}</span><span>{row.due || '—'}</span><EvidenceStatus value={row.status || 'OPEN'} tone={row.status === 'done' ? 'ok' : 'warn'} /></div>)}</> : <div className="module-empty">还没有可追踪的承诺。<button className="empty-cta" onClick={() => openChatWith('记住：周五交付第一版')}>去记录</button></div>}</div> : null}
          {activeNav === '找回 30 秒' ? <div className="time-machine"><div className="time-search"><Search size={20} /><input value={momentQuery} onChange={(event) => setMomentQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void findMoment() }} placeholder="例如：预算改成三十万的那一段" /><button onClick={() => void findMoment()}>启动回溯<ArrowRight size={15} /></button></div>{momentResults.length ? <div className="moment-timeline">{momentResults.map((segment: any, index) => <button className="moment-card" key={`${segment.session_id}-${segment.start}-${index}`} onClick={() => openMoment(segment)}><span className="moment-time">{formatTime(segment.start || 0)}</span><div><small>{segment.speaker || '会议片段'} · {segment.session_id || ''}</small><b>{segment.text || ''}</b><p>{segment.why || '已定位到相关原话'}</p></div><Play size={17} /></button>)}</div> : <div className="time-empty"><RotateCcw size={34} /><h3>会议不是黑箱</h3><p>描述你记得的那句话，Agent 会返回对应的原话和时间戳。</p></div>}</div> : null}
          {activeNav === '项目架构' ? <ArchitecturePanel project={currentProject} /> : null}
        </div> : null}
      </div>
    </section>

    <aside className={`var-inspector ${showInspector ? '' : 'hidden'}`}><div className="inspector-top"><span>CONTEXT FEED</span><div><button className={rightTab === 'notes' ? 'active' : ''} onClick={() => setRightTab('notes')}>纪要</button><button className={rightTab === 'files' ? 'active' : ''} onClick={() => setRightTab('files')}>文件</button><IconButton label="关闭面板" onClick={() => setShowInspector(false)}><X size={14} /></IconButton></div></div>{rightTab === 'notes' ? <div className="context-notes"><div className="context-project"><span className="project-glyph"><FolderOpen size={16} /></span><div><small>CURRENT PROJECT</small><b>{displayProject}</b></div></div>{selectedCaptions.length ? <div className="live-captions"><b>实时字幕</b>{selectedCaptions.map((text, index) => <p key={index}>{text}</p>)}</div> : null}<div className="notes-title-row"><div><span>MEETING INTELLIGENCE</span><h3>{meetingTitle}</h3></div>{selectedSession ? <button className="hud-icon" onClick={() => void generateMinutes()} title="生成纪要"><Sparkles size={15} /></button> : null}</div><div className="note-card"><span className="note-icon"><Sparkles size={15} /></span><div><b>{notes ? '最近纪要' : '等待会议内容'}</b><p>{notes || '开始录音后，实时字幕、重点、决策与行动项会在这里汇集。'}</p></div></div><div className="context-section"><span>采集源</span><b><Mic size={12} />{config.audio_source === 'mic' ? '仅麦克风' : config.audio_source === 'loopback' ? '仅系统声音' : '系统声音 + 麦克风'}</b></div><div className="context-section"><span>项目认知</span><b className={memoryReady ? 'success-text' : ''}><Orbit size={12} />{memoryReady ? '地图与关键认知已建立' : '尚未完成定向探索'}</b></div><div className="context-section"><span>本地优先</span><b><ShieldCheck size={12} />证据保存在本机</b></div></div> : <div className="context-files"><div className="tree-header"><span>PROJECT EXPLORER</span><IconButton label="刷新文件" onClick={() => void refreshFiles()}><RefreshCw size={13} /></IconButton></div>{files.slice(0, 100).map((file, index) => <button className="tree-item" key={`${file.rel}-${index}`} style={{ paddingLeft: `${12 + Math.min(3, file.indent || 0) * 12}px` }} onClick={() => file.type === 'file' ? void openFile(file) : undefined}>{file.type === 'folder' ? <Folder size={14} /> : <FileText size={14} />}<span>{file.name}</span></button>)}{!files.length ? <div className="module-empty">选择项目后显示文件。</div> : null}</div>}</aside>

    {viewer ? <div className="viewer-backdrop" onClick={() => setViewer(null)}><div className="viewer-card" onClick={(event) => event.stopPropagation()}><div className="viewer-head"><div><b>{viewer.name}</b><small>{viewer.kind_label || viewer.kind || ''}{viewer.size ? ` · ${formatBytes(viewer.size)}` : ''}</small></div><IconButton label="关闭" onClick={() => setViewer(null)}><X size={17} /></IconButton></div><div className="viewer-body">{viewer.kind === 'pdf' && (viewer.url || viewer.data_url) ? <iframe className="viewer-pdf" src={viewer.url || viewer.data_url} title={viewer.name} /> : null}{viewer.kind === 'image' && viewer.data_url ? <img src={viewer.data_url} alt={viewer.name} /> : null}{viewer.kind === 'video' && viewer.data_url ? <video src={viewer.data_url} controls /> : null}{viewer.kind === 'audio' && viewer.data_url ? <audio src={viewer.data_url} controls /> : null}{viewer.text ? <pre className="viewer-pre">{viewer.text}</pre> : null}{!viewer.text && !viewer.data_url && !viewer.url ? <div className="module-empty">{viewer.meta || '该文件没有内嵌预览，请用系统应用打开。'}</div> : null}</div></div></div> : null}

    {mobileNav ? <button className="mobile-nav-scrim" aria-label="关闭功能导航" onClick={() => setMobileNav(false)} /> : null}
    {showSettings ? <div className="modal-backdrop" onClick={() => setShowSettings(false)}><div className="settings-modal" onClick={(event) => event.stopPropagation()}><div className="modal-title"><div><div className="eyebrow">SYSTEM CONFIGURATION</div><h2>设置</h2></div><IconButton label="关闭设置" onClick={() => setShowSettings(false)}><X size={18} /></IconButton></div><div className="settings-layout"><div className="settings-menu">{['常规', '我的账户', '会议偏好', '模型服务', '团队与权限', '集成', '账单'].map((tab) => <button key={tab} className={settingsTab === tab ? 'settings-selected' : ''} onClick={() => setSettingsTab(tab)}>{tab}</button>)}</div><div className="settings-body"><h3>{settingsTab}</h3><p className="settings-description">管理采集、模型服务和本地工作区。</p>{settingsTab === '常规' ? <><label>引擎 Python<input value={config.engine_python || ''} onChange={(event) => setConfig({ ...config, engine_python: event.target.value })} /></label><label>仓库目录<input value={config.repo_dir || ''} onChange={(event) => setConfig({ ...config, repo_dir: event.target.value })} /></label><label>音频采集源<select value={config.audio_source || 'both'} onChange={(event) => setConfig({ ...config, audio_source: event.target.value })}><option value="both">系统声音 + 麦克风</option><option value="loopback">仅系统声音</option><option value="mic">仅麦克风</option></select></label><label>保存方式<select value={config.interest_mode || 'manual'} onChange={(event) => setConfig({ ...config, interest_mode: event.target.value })}><option value="api">API 判断 + 手动 VAR</option><option value="manual">仅手动 VAR</option></select></label></> : null}{settingsTab === '会议偏好' ? <><label>识别引擎<select value={config.asr_engine || 'local'} onChange={(event) => setConfig({ ...config, asr_engine: event.target.value })}><option value="local">本地 FunASR</option><option value="xfyun_rtasr">讯飞 RTASR</option><option value="xfyun">讯飞 IAT</option></select></label><div className="setting-toggle"><div><b>自动生成会议纪要</b><span>当前版本请会后手动生成</span></div><span>即将支持</span></div></> : null}{settingsTab === '模型服务' ? <><label>快速配置<select value={apiPreset} onChange={(event) => { const preset = apiPresets.find((item) => item.id === event.target.value); setApiPreset(event.target.value); if (preset) setConfig({ ...config, llm_provider: 'custom', llm_base_url: preset.base, llm_model: preset.model }) }}>{apiPresets.map((preset) => <option value={preset.id} key={preset.id}>{preset.label}</option>)}</select></label><label>模型厂商<select value={config.llm_provider || 'local'} onChange={(event) => setConfig({ ...config, llm_provider: event.target.value })}><option value="local">本地 llama.cpp</option><option value="custom">自定义 API</option></select></label><label>Base URL<input value={config.llm_base_url || ''} onChange={(event) => setConfig({ ...config, llm_base_url: event.target.value })} /></label><label>模型名<input value={config.llm_model || ''} onChange={(event) => setConfig({ ...config, llm_model: event.target.value })} /></label><label>API Key<input type="password" value={config.llm_api_key || ''} onChange={(event) => setConfig({ ...config, llm_api_key: event.target.value })} /></label><label>推理强度<select value={config.llm_reasoning_effort || ''} onChange={(event) => setConfig({ ...config, llm_reasoning_effort: event.target.value })}><option value="">默认</option><option value="none">关闭</option><option value="low">低</option><option value="medium">中</option><option value="high">高</option></select></label><button className="module-button" onClick={() => void testLLMConnection()}>{llmTest.state === 'testing' ? '正在测试…' : '测试连接'}</button>{llmTest.message ? <div className={`api-status ${llmTest.state}`}>{llmTest.message}</div> : null}</> : null}{!['常规', '会议偏好', '模型服务'].includes(settingsTab) ? <div className="module-empty">该设置当前版本暂未开放。</div> : null}<button className="save-button" onClick={() => void saveSettings()}>保存更改</button></div></div></div></div> : null}
    {showHelp ? <div className="modal-backdrop" onClick={() => setShowHelp(false)}><div className="settings-modal help-modal" onClick={(event) => event.stopPropagation()}><div className="modal-title"><div><div className="eyebrow">OPERATOR MANUAL</div><h2>操作指南</h2></div><IconButton label="关闭帮助" onClick={() => setShowHelp(false)}><X size={18} /></IconButton></div><div className="help-body"><div className="help-grid"><section><b>快速开始</b><p>连接项目文件夹，Agent 会建立地图和索引；在 AI 协作者里提问。</p></section><section><b>会议与证据</b><p>发起会议采集系统声音、麦克风与屏幕；未命中内容不会长期保存。</p></section><section><b>生成文档</b><p>在 Agent 模式要求写入 docx、pptx 或 pdf；写入与命令必须确认。</p></section><section><b>安全边界</b><p>文件操作限当前项目；API Key 只保存在本机。</p></section></div><button className="save-button" onClick={() => setShowHelp(false)}>知道了</button></div></div></div> : null}
  </main>
}
