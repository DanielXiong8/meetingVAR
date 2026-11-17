'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { call, getBridge, isDesktop, matchesChatRun, newRunId, type ChatRunIdentity, type AnswerMetadata, answerMetadata, subscribeWorkspace, sameProject } from '@/lib/bridge'
import { MarkdownText } from '@/components/MarkdownText'
import { summarizeWorkspaceIssues } from '@/lib/workspace-issues'
import {
  Archive,
  Bell,
  Bot,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Clock3,
  File,
  FileCode2,
  FileImage,
  FileSpreadsheet,
  FileText,
  Folder,
  FolderOpen,
  Music,
  PackageCheck,
  Presentation,
  RefreshCw,
  LayoutDashboard,
  ListChecks,
  MessageSquare,
  Mic,
  MoreHorizontal,
  Network,
  Paperclip,
  Pencil,
  Play,
  Plus,
  Search,
  Send,
  Settings,
  ShieldCheck,
  Sparkles,
  SquarePen,
  Terminal,
  Users,
  Video,
  Wrench,
  X,
  Zap,
} from 'lucide-react'

const apiPresets = [
  { id: 'deepseek', label: 'DeepSeek', base: 'https://api.deepseek.com/v1', model: 'deepseek-chat' },
  { id: 'agnes', label: 'Agnes', base: 'https://apihub.agnes-ai.com/v1', model: 'agnes-3.0-flash' },
  { id: 'openai', label: 'OpenAI', base: 'https://api.openai.com/v1', model: 'gpt-4o-mini' },
  { id: 'qwen', label: '通义千问', base: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus' },
  { id: 'zhipu', label: '智谱', base: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4-flash' },
  { id: 'custom', label: '自定义 / 其他兼容服务', base: '', model: '' },
]

type UiFile = { name: string; type: 'folder' | 'file'; open?: boolean; indent?: number; active?: boolean; rel?: string; kind?: string; suffix?: string; size?: number; kindLabel?: string }
type UiProject = { name: string; tone: string; time: string; path?: string }
type UiAction = { id: string; label?: string; type?: string; status?: string }
const actionStatusLabel: Record<string, string> = {
  done: '（已完成）', succeeded: '（已完成）', failed: '（失败）',
  running: '（执行中）', approved: '（已批准，待执行）', confirmed: '（待执行）',
  cancel_requested: '（正在取消）', cancelled: '（已取消）',
  interrupted: '（已中断，请核对结果）', unverified: '（结果待核验）',
}
type UiMessage = AnswerMetadata & {
  id?: string
  role: 'assistant' | 'user'
  text: string
  actions?: UiAction[]
  citations?: any[]
  mode?: string
  focus?: any[]
  facts?: any[]
  plan?: any[]
  observations?: any[]
  usage?: any
  trace?: string
  thinking?: boolean
  thinkingMode?: 'agent' | 'chat'
  startedAt?: number
  durationMs?: number
  ts?: number
  liveEvents?: any[]
  liveAnswer?: string
  liveThought?: string
}

const initialFiles: UiFile[] = [
  { name: 'meeting-agent', type: 'folder', open: true },
  { name: 'src', type: 'folder', open: true, indent: 1 },
  { name: 'app.tsx', type: 'file', indent: 2, active: true },
  { name: 'meeting.ts', type: 'file', indent: 2 },
  { name: 'prompts.ts', type: 'file', indent: 2 },
  { name: 'components', type: 'folder', open: false, indent: 1 },
  { name: 'README.md', type: 'file', indent: 1 },
  { name: 'package.json', type: 'file', indent: 1 },
]

const initialProjects = [
  { name: '产品周会', tone: 'purple', time: '刚刚' },
  { name: 'Q4 复盘', tone: 'blue', time: '昨天' },
  { name: '招聘面试', tone: 'green', time: '周一' },
]

function flattenTree(nodes: any[], depth = 0): UiFile[] {
  const rows: UiFile[] = []
  for (const node of nodes || []) {
    const isDir = !!node.dir
    rows.push({
      name: String(node.name || ''),
      type: isDir ? 'folder' : 'file',
      open: depth < 1,
      indent: depth,
      rel: node.rel || node.name,
      kind: node.kind,
      suffix: node.actual_suffix || node.suffix || '',
      size: Number(node.size) || 0,
      kindLabel: node.kind_label || '',
    })
    if (isDir) rows.push(...flattenTree(node.children || [], depth + 1))
  }
  return rows
}

function fileIcon(file: UiFile) {
  const name = file.name.toLowerCase()
  const suffix = (file.suffix || name.split('.').pop() || '').toLowerCase()
  const kind = file.kind || ''
  if (kind === 'image' || ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg'].includes(suffix)) return <FileImage size={14} className="file-icon" />
  if (kind === 'video' || ['mp4', 'mov', 'webm', 'mkv', 'avi'].includes(suffix)) return <Video size={14} className="file-icon" />
  if (kind === 'audio' || ['wav', 'mp3', 'm4a', 'flac', 'ogg'].includes(suffix)) return <Music size={14} className="file-icon" />
  if (kind === 'xlsx' || ['xls', 'xlsx', 'csv'].includes(suffix)) return <FileSpreadsheet size={14} className="file-icon" />
  if (suffix === 'pdf' || ['doc', 'docx', 'rtf'].includes(suffix)) return <FileText size={14} className="file-icon" />
  if (['ppt', 'pptx', 'key'].includes(suffix)) return <Presentation size={14} className="file-icon" />
  if (['zip', '7z', 'rar', 'gz', 'tar'].includes(suffix)) return <Archive size={14} className="file-icon" />
  if (['ts', 'tsx', 'js', 'jsx', 'py', 'css', 'html', 'json', 'yaml', 'yml', 'sh', 'rs', 'go', 'java', 'c', 'cpp', 'h'].includes(suffix)) return <FileCode2 size={14} className="file-icon" />
  if (suffix === 'md' || suffix === 'txt') return <FileText size={14} className="file-icon" />
  return <File size={14} className="file-icon" />
}

function visibleTree(files: UiFile[]): UiFile[] {
  const rows: UiFile[] = []
  const closed: number[] = []
  for (const file of files) {
    const depth = file.indent || 0
    while (closed.length && depth <= closed[closed.length - 1]) closed.pop()
    if (closed.length) continue
    rows.push(file)
    if (file.type === 'folder' && !file.open) closed.push(depth)
  }
  return rows
}

function projectName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() || '会议'
}

function formatBytes(value: number): string {
  const size = Number(value) || 0
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

function sessionLabel(session: any): string {
  if (session?.title) return String(session.title)
  const mtime = Number(session?.mtime || 0) * 1000
  if (!mtime) return String(session?.sid || '会议')
  const date = new Date(mtime)
  const hm = `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
  return `会议 ${date.getMonth() + 1}/${date.getDate()} ${hm}`
}

function pad2(value: number): string {
  return String(value).padStart(2, '0')
}

function liveClock(now: number): string {
  const date = new Date(now)
  const weekdays = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六']
  return `${date.getMonth() + 1}月${date.getDate()}日 ${weekdays[date.getDay()]} · ${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`
}

function messageTime(ts: number | undefined, now: number): string {
  if (!ts) return '刚刚'
  const diff = Math.max(0, now / 1000 - Number(ts))
  if (diff < 10) return '刚刚'
  if (diff < 60) return `${Math.floor(diff)}秒前`
  if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)}小时前`
  return relativeTime(ts)
}

function relativeTime(value: any): string {
  const seconds = Number(value || 0)
  if (!seconds) return ''
  const diff = Date.now() / 1000 - seconds
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.max(1, Math.floor(diff / 60))}分`
  if (diff < 86400) return `${Math.floor(diff / 3600)}小时`
  if (diff < 7 * 86400) return `${Math.floor(diff / 86400)}天`
  const date = new Date(seconds * 1000)
  return `${date.getMonth() + 1}/${date.getDate()}`
}

function bridgeText(message: any): string {
  if (!message) return ''
  let text = String(message.content || '')
  if (Array.isArray(message.focus) && message.focus.length) {
    text += '\n\n会前关注\n' + message.focus.map((item: any) => `· ${item.title}${item.why ? ` — ${item.why}` : ''}`).join('\n')
  }
  if (Array.isArray(message.facts) && message.facts.length) {
    text += '\n\n会后事实\n' + message.facts.map((item: any) =>
      `· [${item.fact_type || '事实'}] ${item.title}${item.owner ? ` / ${item.owner}` : ''}${item.due ? ` / ${item.due}` : ''}`).join('\n')
  }
  if (Array.isArray(message.citations) && message.citations.length) {
    const lines = message.citations.slice(0, 12).map((c: any) => {
      const quote = String(c.quote || '').replace(/\s+/g, ' ').trim().slice(0, 140)
      if (c.kind === 'turn') {
        const turns = Array.isArray(c.turn_ids) ? c.turn_ids.join(',') : ''
        return `- 会议 ${c.session_id || ''}${turns ? ` / ${turns}` : ''}${quote ? `：${quote}` : ''}`
      }
      if (c.kind === 'document' || c.path) {
        const title = c.title || c.path || c.source_id || c.chunk_id || '资料'
        const page = c.page ?? c.page_start
        const lineAt = c.line_start
        const loc = page != null ? `第${page}页` : lineAt != null ? `第${lineAt}行` : ''
        return `- 文件 ${title}${loc ? `（${loc}）` : ''}${quote ? `：${quote}` : ''}`
      }
      if (c.kind === 'node') return `- 实体 ${c.name || c.node_id || ''}${quote ? `：${quote}` : ''}`
      if (c.kind === 'edge') return `- 关系 ${c.relation || c.edge_id || c.id || ''}${quote ? `：${quote}` : ''}`
      return `- ${c.name || c.title || c.kind || '依据'}${quote ? `：${quote}` : ''}`
    }).filter(Boolean)
    if (lines.length) {
      text += '\n\n依据：\n' + lines.join('\n')
      if (message.citations.length > lines.length) text += `\n- 其余 ${message.citations.length - lines.length} 条引用略`
    }
  }
  return text
}

function IconButton({ label, children, onClick }: { label: string; children: React.ReactNode; onClick?: () => void }) {
  return <button aria-label={label} className="icon-button" onClick={onClick}>{children}</button>
}

function observationSummary(observation: any): string {
  const text = String(observation?.text || '').replace(/\s+/g, ' ').trim()
  if (text) return text.slice(0, 180)
  const args = observation?.arguments
  if (args && typeof args === 'object') return JSON.stringify(args).slice(0, 180)
  return ''
}

const kgKinds = ['Action', 'Commitment', 'Person', 'Team', 'Date', 'Risk', 'OpenQuestion', 'Requirement', 'Decision', 'Topic', 'Artifact', 'Document', 'Meeting', 'Project']
const kgRelations = ['assigned_to', 'owned_by', 'due_on', 'part_of', 'depends_on', 'blocks', 'mitigates', 'decided_in', 'derived_from', 'about', 'references', 'related_to', 'contradicts', 'supersedes', 'member_of', 'participates_in']
const kgKindColors: Record<string, string> = {
  Action: '#c9ff69', Commitment: '#ffd36e', Person: '#76b8ff', Team: '#7fd8c8',
  Date: '#ff9f7a', Risk: '#ff7d7d', OpenQuestion: '#c39aff', Requirement: '#9ad0ff',
  Decision: '#b6e28a', Topic: '#9a9d9f', Artifact: '#d2a65b', Document: '#7e9bb5',
  Meeting: '#8fbd54', Project: '#e8e8e5',
}

function kgLabel(value: any, max = 16): string {
  const text = String(value || '').trim()
  return text.length > max ? `${text.slice(0, max - 1)}…` : text
}

function kgNodeLabel(node: any): string {
  const name = String(node?.name || '').trim()
  if (node?.kind === 'Meeting' && /^会议\s+go_/.test(name)) {
    const stamp = Number(name.replace(/^会议\s+go_/, ''))
    if (Number.isFinite(stamp)) {
      const milliseconds = stamp > 1e15 ? stamp / 1e6 : stamp > 1e12 ? stamp : stamp * 1000
      const date = new Date(milliseconds)
      if (!Number.isNaN(date.getTime())) {
        return `会议 · ${date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}`
      }
    }
  }
  return kgLabel(name, 12)
}

function kgSeed(value: string): number {
  let hash = 2166136261
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return hash >>> 0
}

function layoutKg(nodes: any[], edges: any[], width = 920, height = 620) {
  const visible = nodes.slice(0, 160)
  const ids = new Set(visible.map((node) => node.id))
  const links = edges.filter((edge) => ids.has(edge.src_id) && ids.has(edge.dst_id))
  const points = visible.map((node, index) => {
    const angle = (kgSeed(String(node.id)) % 360) * Math.PI / 180
    const radius = 70 + ((kgSeed(String(node.id)) >>> 8) % 150)
    return {
      id: node.id, node, x: width / 2 + Math.cos(angle) * radius,
      y: height / 2 + Math.sin(angle) * radius, vx: 0, vy: 0,
    }
  })
  const byId = new Map(points.map((point) => [point.id, point]))
  const degree = new Map<string, number>()
  for (const link of links) {
    degree.set(link.src_id, (degree.get(link.src_id) || 0) + 1)
    degree.set(link.dst_id, (degree.get(link.dst_id) || 0) + 1)
  }
  const k = Math.sqrt((width * height) / Math.max(1, points.length)) * 0.9
  for (let iteration = 0; iteration < 240; iteration += 1) {
    for (const point of points) { point.vx = 0; point.vy = 0 }
    for (let i = 0; i < points.length; i += 1) {
      for (let j = i + 1; j < points.length; j += 1) {
        const a = points[i]; const b = points[j]
        let dx = a.x - b.x; let dy = a.y - b.y
        let distance = Math.sqrt(dx * dx + dy * dy)
        if (distance < 1) { dx = (i % 2 ? 1 : -1); dy = (j % 2 ? 1 : -1); distance = 1.4 }
        const force = (k * k / distance) * 0.014
        const fx = (dx / distance) * force
        const fy = (dy / distance) * force
        a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy
      }
    }
    for (const link of links) {
      const a = byId.get(link.src_id); const b = byId.get(link.dst_id)
      if (!a || !b) continue
      const dx = b.x - a.x; const dy = b.y - a.y
      const distance = Math.max(1, Math.sqrt(dx * dx + dy * dy))
      const force = (distance - k) * 0.04
      const fx = (dx / distance) * force
      const fy = (dy / distance) * force
      a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy
    }
    const cooling = 1 - iteration / 280
    for (const point of points) {
      point.vx += (width / 2 - point.x) * 0.003
      point.vy += (height / 2 - point.y) * 0.003
      point.x = Math.max(55, Math.min(width - 55, point.x + Math.max(-14, Math.min(14, point.vx * 0.35 * cooling))))
      point.y = Math.max(45, Math.min(height - 45, point.y + Math.max(-14, Math.min(14, point.vy * 0.35 * cooling))))
    }
  }
  const xs = points.map((point) => point.x); const ys = points.map((point) => point.y)
  const minX = Math.min(...xs); const maxX = Math.max(...xs)
  const minY = Math.min(...ys); const maxY = Math.max(...ys)
  const scaleX = maxX - minX > 1 ? (width - 150) / (maxX - minX) : 1
  const scaleY = maxY - minY > 1 ? (height - 110) / (maxY - minY) : 1
  const positions: Record<string, any> = {}
  for (const point of points) {
    positions[point.id] = {
      x: 75 + (point.x - minX) * scaleX,
      y: 55 + (point.y - minY) * scaleY,
      node: point.node, degree: degree.get(point.id) || 0,
    }
  }
  return { positions, width, height }
}

type ActivityInfo = { kind: 'search' | 'read' | 'list' | 'write' | 'command' | 'tool'; label: string; detail: string; path?: string; meta?: string }

function activityInfo(observation: any): ActivityInfo {
  const tool = String(observation?.tool || 'tool')
  const args = observation?.arguments && typeof observation.arguments === 'object' ? observation.arguments : {}
  const path = String(args.path || args.file || '')
  if (tool === 'write_file') {
    const content = String(args.content || '')
    const lines = content ? content.split('\n').length : 0
    return { kind: 'write', label: '写入', detail: path, path, meta: lines ? `+${lines}` : '' }
  }
  if (tool === 'run_command') {
    const status = String(observation?.status || '')
    return { kind: 'command', label: '执行', detail: String(args.command || ''), meta: status === 'done' ? '完成' : status === 'failed' ? '失败' : '待确认' }
  }
  if (tool === 'list_files') {
    return { kind: 'list', label: '列出', detail: path || '.', path: path || '.' }
  }
  if (tool === 'search_files') {
    const paths = Array.isArray(args.paths) ? args.paths : []
    return { kind: 'search', label: '搜索文件', detail: String(args.query || ''), path: String(args.path || '') || undefined, meta: paths.length > 1 ? `+${paths.length - 1}` : undefined }
  }
  if (tool === 'search_project_knowledge') {
    const paths = Array.isArray(args.paths) ? args.paths : []
    return { kind: 'search', label: '检索项目', detail: String(args.query || ''), path: String(args.path || '') || undefined, meta: paths.length > 1 ? `+${paths.length - 1}` : undefined }
  }
  if (/search|prefetch|find/i.test(tool) || tool.startsWith('kg_')) {
    const terms = Array.isArray(args.terms) ? args.terms.join('、') : ''
    return { kind: 'search', label: tool === 'prefetch' ? '预检索' : '检索', detail: String(args.query || terms || tool) }
  }
  if (tool === 'read_file' || tool === 'read_project_chunk') {
    return { kind: 'read', label: '读取', detail: path || tool, path }
  }
  if (tool === 'get_transcript_window') {
    return { kind: 'read', label: '读取', detail: [args.session_id, ...(args.turn_ids || [])].filter(Boolean).join(' / ') || tool }
  }
  if (['get_session_overview', 'list_sessions', 'project_status', 'read_fact', 'agent_memory'].includes(tool)) {
    return { kind: 'read', label: '查阅', detail: String(args.session_id || args.name || tool) }
  }
  return { kind: 'tool', label: '调用', detail: tool }
}

function ActivityFeed({ message }: { message: UiMessage }) {
  const observations = message.observations || []
  const infos = observations.map((row: any) => {
    const info = activityInfo(row)
    return row?.error ? { ...info, meta: info.meta || '失败' } : info
  })
  const searches = infos.filter((row) => row.kind === 'search').length
  const reads = infos.filter((row) => row.kind === 'read' && !!row.path).length
  const lists = infos.filter((row) => row.kind === 'list').length
  const writes = infos.filter((row) => row.kind === 'write').length
  const commands = infos.filter((row) => row.kind === 'command').length
  const seconds = message.durationMs ? Math.max(1, Math.round(message.durationMs / 1000)) : 0
  const summary = [searches ? `${searches} 次检索` : '', lists ? `${lists} 个目录` : '', reads ? `${reads} 个文件` : '', writes ? `${writes} 次写入` : '', commands ? `${commands} 条命令` : ''].filter(Boolean).join('，')
  return (
    <div className="activity-feed">
      {seconds ? <div className="activity-row activity-muted"><Clock3 size={14} /><span className="activity-label">思考</span><span className="activity-detail">持续 {seconds} 秒</span></div> : null}
      {summary ? <div className="activity-row activity-muted"><Search size={14} /><span className="activity-label">操作</span><span className="activity-detail">{summary}</span></div> : null}
      {observations.map((observation: any, index: number) => {
        const info = infos[index]
        return (
          <div className={`activity-row activity-${info.kind}`} key={`activity-${index}`}>
            {info.kind === 'search' ? <Search size={14} /> : info.kind === 'write' ? <Pencil size={14} /> : info.kind === 'list' ? <Folder size={14} /> : info.kind === 'read' ? <FileText size={14} /> : info.kind === 'command' ? <Terminal size={14} /> : <Wrench size={14} />}
            <span className="activity-label">{info.label}</span>
            {info.path ? <span className="activity-path">{fileIcon({ name: info.path, type: 'file' } as UiFile)}<span>{info.path}</span></span> : <span className="activity-detail">{info.detail}</span>}
            {info.path && info.detail && info.detail !== info.path ? <span className="activity-detail">{info.detail}</span> : null}
            {info.meta ? <span className="activity-meta">{info.meta}</span> : null}
          </div>
        )
      })}
      {message.plan?.length ? (
        <details className="activity-plan">
          <summary>计划 · {message.plan.length} 步</summary>
          <div className="activity-plan-body">
            {message.plan.map((step: any, index: number) => (
              <div className="activity-plan-step" key={`plan-${index}`}><b>{index + 1}. {step.goal || '步骤'}</b><small>{[step.tool ? `工具：${step.tool}` : '', step.why || ''].filter(Boolean).join(' · ')}</small></div>
            ))}
          </div>
        </details>
      ) : null}
    </div>
  )
}

const AUTO_STAGE_LABELS: Record<string, string> = { scan: '扫描', map: '项目地图', extract: '提取', index: '索引', explore: '关键探索', graph: '图谱' }
const AUTO_STATE_LABELS: Record<string, string> = { completed: '已完成', complete: '已建立', ready: '关键认知已建立', partial: '部分可用', mapped_awaiting_model: '地图可用 · 等待模型', awaiting_model: '等待模型', paused_budget: '预算暂停', paused_provider: '模型受限 · 可重试', paused_model: '模型输出异常 · 可重试', pending: '未开始', not_started: '未开始', not_built: '未建立', failed: '失败', busy: '另一任务处理中', running: '进行中' }
function AutoProjectCard({ state, onRefresh, onSettings, modelInfo, onVerifyModel }: { state: any; onRefresh: () => void; onSettings?: () => void; modelInfo?: any; onVerifyModel?: () => void }) {
  const [open, setOpen] = useState(false)
  // Status polling preserves the user's click; switching projects always collapses.
  useEffect(() => { setOpen(false) }, [state?.folder])
  if (!state || state.status === 'idle') return null
  const running = state.status === 'running'
  const waiting = state.status === 'mapped_awaiting_model'
  const result = state.result || {}
  const kg = result.kg || {}
  const rag = result.rag || {}
  const issues = summarizeWorkspaceIssues(result.errors)
  const stages = result.stages || {}
  const map = result.map_totals || {}
  const exploration = result.coverage?.explore || {}
  const questions = Array.isArray(result.exploration?.open_questions) ? result.exploration.open_questions.slice(0, 5) : []
  const indexed = Number(rag.documents ?? result.totals?.documents ?? 0)
  const discovered = Number(result.discovered ?? result.doc_count ?? 0)
  const headline = running ? '正在建立项目地图与关键认知'
    : waiting ? '项目地图可用 · 等待模型'
      : state.status === 'oriented' ? '项目地图与关键认知已建立'
        : state.status === 'paused_budget' ? '本次探索预算已用尽 · 已暂停'
          : state.status === 'paused_provider' ? '模型服务受限 · 已保留进度，可重试'
          : state.status === 'paused_model' ? '模型未返回有效动作 · 已保留进度，可重试'
          : state.status === 'needs_orientation' ? '旧摘要待迁移 · 尚未建立项目认知' : '工作区有待处理事项'
  const progressText = running
    ? [AUTO_STAGE_LABELS[state.stage] || state.stage, state.progress].filter(Boolean).join(' · ')
    : `地图 ${map.current_sources ?? 0} 个来源 / ${map.units ?? 0} 个单元 · 已探索 ${exploration.processed ?? 0} 个单元${issues.sourceCount ? ` · ${issues.sourceCount} 个来源读取受阻` : ''}${issues.blockerCount ? ` · ${issues.blockerCount} 项待处理` : ''}`
  if (!open) {
    return (
      <div className="auto-project-fab-row">
        <button
          className={`auto-project-fab ${running ? 'running' : ''} ${issues.count ? 'warn' : ''}`}
          onClick={() => setOpen(true)}
          title={`${headline}${progressText ? ` · ${progressText}` : ''}`}
          aria-label="打开工作区同步详情"
          aria-expanded="false"
        >
          {running ? <RefreshCw size={14} className="spin" /> : <Sparkles size={14} />}
          {issues.count ? <i className="auto-fab-dot" /> : null}
        </button>
      </div>
    )
  }
  return (
    <div className={`auto-project expanded ${running ? 'running' : 'done'} ${issues.count ? 'warn' : ''}`}>
      <div className="auto-project-head">
        <span className="auto-project-icon">{running ? <RefreshCw size={14} className="spin" /> : <Sparkles size={14} />}</span>
        <div className="auto-project-title"><b>{headline}</b><small>{progressText}</small></div>
        {running ? <span className="auto-running">后台运行中</span> : <button className="module-button auto-refresh" onClick={onRefresh} title="开始新的有界调查，旧进度与用量保留审计">重新调查</button>}
        <button className="auto-project-toggle" onClick={() => setOpen(false)} aria-expanded="true">收起 <ChevronDown size={13} /></button>
      </div>
      <div className="auto-project-body">
        <div className="auto-line"><span>项目地图</span><div className="auto-stats"><b>{AUTO_STATE_LABELS[result.map_status] || result.map_status || '未建立'}</b><b>{map.current_sources ?? 0} 个当前来源</b><b>{map.complete_sources ?? 0} 个完整提取来源</b><b>{map.units ?? 0} 个定位单元</b></div></div>
        <div className="auto-line"><span>关键探索</span><div className="auto-stats"><b>{AUTO_STATE_LABELS[result.exploration_status] || result.exploration_status || '未开始'}</b><b>选择 {exploration.selected ?? 0}</b><b>已读 {exploration.processed ?? 0}</b><b>失败 {exploration.failed ?? 0}</b><b>待查 {exploration.pending ?? 0}</b></div></div>
        {(() => { const budget = result.model_budget || exploration.budget || {}; return (
          <div className="auto-line"><span>探索预算</span><div className="auto-stats"><b>输入 {budget.input_tokens ?? 0} / {budget.max_input_tokens ?? '—'}</b><b>输出 {budget.output_tokens ?? 0} / {budget.max_output_tokens ?? '—'}</b><b>模型调用 {budget.model_calls ?? 0}</b></div></div>
        ) })()}
        <div className="auto-line"><span>本地索引</span><div className="auto-stats"><b>发现 {discovered} 个来源</b><b>提取 {typeof result.extracted === 'object' ? result.extracted?.processed ?? 0 : result.extracted ?? 0}{result.extracted_unit === 'pages' || typeof result.extracted === 'object' ? ' 页' : ' 个来源'}</b><b>索引 {indexed} 个</b><b>{rag.chunks ?? 0} 个片段</b></div></div>
        {(kg.nodes != null || kg.edges != null) ? <div className="auto-line"><span>图谱</span><div className="auto-stats"><b>{kg.nodes ?? 0} 节点</b><b>{kg.edges ?? 0} 边</b></div></div> : null}
        {Object.entries(stages).filter(([stage]) => stage in AUTO_STAGE_LABELS).map(([stage, detail]: [string, any]) => (
          <div className="auto-line" key={stage}><span>{AUTO_STAGE_LABELS[stage]}</span><small>{AUTO_STATE_LABELS[detail.status] || detail.status}{stage === 'explore' && detail.status === 'ready' ? ' · 关键入口已核对' : ''}</small></div>
        ))}
        {issues.count ? (
          <div className="auto-line"><span>待处理原因</span><div className="auto-issues">
            {issues.groups.map((group) => (
              <div className="auto-issue" key={group.label}><b>{group.label}</b>
                <small>{group.kind === 'source' ? `${group.count} 个来源` : '调查受阻'}</small>
                {group.paths.length ? <details className="auto-issue-paths"><summary>查看文件</summary><ul>{group.paths.slice(0, 20).map((path) => <li key={path}>{path}</li>)}</ul></details> : null}
              </div>
            ))}
          </div></div>
        ) : null}
        {result.overview ? <div className="auto-overview">{result.overview}</div> : null}
        {questions.length ? <div className="auto-line"><span>未决问题</span><div className="auto-focus">{questions.map((question: any, index: number) => <div key={index}>{typeof question === 'string' ? question : question.question || question.text || ''}</div>)}</div></div> : null}
        <div className="auto-line"><small>地图和索引只定位资料；关键认知是带来源的暂定理解，未读内容会在提问时按需继续查。</small></div>
        {waiting ? (
          <div className="auto-warn">地图已建立；可用来源不受其他文件提取失败影响。关键探索需要已配置的模型。
            {modelInfo ? (modelInfo.configured ? (modelInfo.ready ? ' 模型已验证，可继续建立关键认知。' : ` 已配置但尚未验证${modelInfo.error ? `：${modelInfo.error}` : '。'}`) : ` 尚未配置模型${modelInfo.error ? `：${modelInfo.error}` : '。'}`) : null}
            {!modelInfo?.ready && onVerifyModel ? <button className="auto-warn-action" onClick={onVerifyModel}>测试模型连接</button> : null}
            {onSettings ? <button className="auto-warn-action" onClick={onSettings}>打开模型设置</button> : null}
          </div>
        ) : state.status === 'paused_budget' ? <div className="auto-warn">本次调查进度与用量已保留，不会自动重试调用模型。点击“重新调查”会开启新预算，旧用量仍可审计。</div>
          : state.status === 'paused_provider' ? <div className="auto-warn">模型服务限流或网络中断，已读进度与未决问题已保留；稍后点“立即同步”继续。</div>
          : state.status === 'paused_model' ? <div className="auto-warn">模型返回了非 JSON/未知动作，已读进度已保留；点“立即同步”会重试。</div>
          : state.error && !issues.count ? <div className="auto-warn">{state.error}</div> : null}
        <details className="activity-plan"><summary>原始状态（调试）</summary><pre>{JSON.stringify({ map_status: result.map_status, exploration_status: result.exploration_status, coverage: result.coverage || {}, budget: result.model_budget || {}, errors: result.errors || [] }, null, 2)}</pre></details>
      </div>
    </div>
  )
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
  const [draft, setDraft] = useState({ name: '', purpose: '', layer: 'core', owner: '', files: '' })
  const autoGenerateTried = useRef(false)

  function apply(result: any) {
    setData(result)
    setOwners(result?.owners || {})
    setManual(Array.isArray(result?.manual_modules) ? result.manual_modules : [])
  }

  async function load() {
    if (!project || !isDesktop()) return
    setBusy(true)
    setError('')
    try {
      const result = await call('architecture', project)
      apply(result)
      if (!result?.ok) setError(result?.error || '项目架构读取失败')
      const needsAi = result?.ok && result?.architecture_status === 'complete'
        && result?.generation?.mode !== 'ai' && (result?.coverage?.total || 0) > 0
      if (needsAi && !autoGenerateTried.current) {
        autoGenerateTried.current = true
        void generate()
      }
    } catch (issue: any) {
      setError(String(issue?.message || issue))
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => { autoGenerateTried.current = false; void load() }, [project])

  async function generate() {
    if (!project || !isDesktop() || generating) return
    setGenerating(true)
    setError('')
    setNotice('正在分析仓库地图并归纳模块，可能需要一会儿…')
    try {
      const result = await call('architecture_generate', project)
      if (!result?.ok) {
        setError(result?.error || 'AI 架构生成失败')
        setNotice('')
        return
      }
      apply(result)
      setNotice(`AI 已归纳 ${result.groups?.length || 0} 个模块；负责人和模块都可以继续手动修改。`)
    } catch (issue: any) {
      setError(String(issue?.message || issue))
      setNotice('')
    } finally {
      setGenerating(false)
    }
  }

  async function save(nextManual = manual, nextOwners = owners) {
    if (!project || !isDesktop() || saving) return
    setSaving(true)
    setError('')
    try {
      const result = await call('architecture_save', project, JSON.stringify({
        owners: nextOwners, modules: nextManual,
      }))
      if (!result?.ok) {
        setError(result?.error || '架构保存失败')
        return
      }
      apply(result)
      setNotice('已保存模块与负责人。')
    } catch (issue: any) {
      setError(String(issue?.message || issue))
    } finally {
      setSaving(false)
    }
  }

  function addManual() {
    const name = draft.name.trim()
    if (!name) {
      setError('请先填写模块名称')
      return
    }
    const module = {
      id: `manual-${Date.now().toString(36)}`,
      name,
      purpose: draft.purpose.trim(),
      layer: draft.layer,
      owner: draft.owner.trim(),
      files: draft.files.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean),
    }
    const next = [...manual, module]
    setManual(next)
    setShowAdd(false)
    setDraft({ name: '', purpose: '', layer: 'core', owner: '', files: '' })
    void save(next, { ...owners, [module.id]: module.owner })
  }

  function removeManual(id: string) {
    const next = manual.filter((item) => item.id !== id)
    setManual(next)
    void save(next, owners)
  }

  const groups = Array.isArray(data?.groups) ? data.groups : []
  const edges = Array.isArray(data?.edges) ? data.edges : []
  const generation = data?.generation || {}
  const layers = Array.isArray(data?.layers) && data.layers.length
    ? data.layers
    : [{ id: 'other', label: '模块', groups: groups.map((group: any) => group.id) }]
  const byId: Record<string, any> = Object.fromEntries(groups.map((group: any) => [group.id, group]))
  const layerNames = [
    ['entry', '入口'], ['ui', '界面'], ['core', '核心模块'], ['data', '数据与知识'],
    ['docs', '文档与资料'], ['test', '测试'], ['other', '其他'],
  ]
  const ownerCount = groups.filter((group: any) => group.owner && group.owner !== '未指定').length
  return (
    <div className="arch-view">
      <div className="arch-head">
        <div className="arch-stats">
          <b>{groups.length} 个模块</b>
          <b>{edges.length} 条依赖</b>
          <b>{data?.coverage?.total ?? 0} 个来源</b>
          <b>负责人 {ownerCount}/{groups.length || 0}</b>
        </div>
        <div className="arch-actions">
          <button className="module-button arch-ai-button" onClick={() => void generate()} disabled={busy || generating || saving}>
            {generating ? '模型分析中…' : 'AI 生成架构'}
          </button>
          <button className="module-button" onClick={() => setShowAdd((value) => !value)} disabled={busy || generating || saving}>
            {showAdd ? '收起添加' : '添加模块'}
          </button>
          <button className="module-button" onClick={() => void save()} disabled={busy || generating || saving}>
            {saving ? '保存中…' : '保存修改'}
          </button>
          <button className="module-button" onClick={() => void load()} disabled={busy || generating || saving}>
            {busy ? '读取中…' : '刷新'}
          </button>
        </div>
      </div>
      {notice ? <div className="arch-notice">{notice}</div> : null}
      {error ? <div className="module-empty">{error}</div> : null}
      <div className={`arch-generation ${generation.stale ? 'stale' : ''}`}>
        <span>
          {generation.mode === 'ai'
            ? `AI 归纳 · ${generation.model || '当前模型'} · ${generation.generated_at || ''}`
            : '静态地图投影 · 尚未调用模型'}
        </span>
        {generation.stale ? <b>地图已有更新，建议重新生成</b> : null}
      </div>
      {generation.summary ? <div className="arch-summary">{generation.summary}</div> : null}
      {data?.architecture_status === 'not_built' ? <div className="module-empty">项目地图尚未建立；先在工作区点“立即同步”。</div> : null}
      {showAdd ? <div className="arch-form">
        <div className="arch-form-row">
          <input className="module-input" placeholder="模块名称，例如：会议转写" value={draft.name}
            onChange={(event) => setDraft((value) => ({ ...value, name: event.target.value }))} />
          <select className="module-input arch-select" value={draft.layer}
            onChange={(event) => setDraft((value) => ({ ...value, layer: event.target.value }))}>
            {layerNames.map(([id, label]) => <option value={id} key={id}>{label}</option>)}
          </select>
        </div>
        <input className="module-input" placeholder="职责（一句话）" value={draft.purpose}
          onChange={(event) => setDraft((value) => ({ ...value, purpose: event.target.value }))} />
        <div className="arch-form-row">
          <input className="module-input" placeholder="负责人（可留空）" value={draft.owner}
            onChange={(event) => setDraft((value) => ({ ...value, owner: event.target.value }))} />
          <input className="module-input" placeholder="文件路径，逗号分隔（可留空）" value={draft.files}
            onChange={(event) => setDraft((value) => ({ ...value, files: event.target.value }))} />
        </div>
        <div className="arch-form-actions">
          <button className="module-button" onClick={addManual}>添加到架构</button>
          <button className="module-button" onClick={() => setShowAdd(false)}>取消</button>
        </div>
      </div> : null}
      <div className="arch-columns">
        {layers.map((layer: any) => (
          <section className="arch-layer" key={layer.id}>
            <div className="arch-layer-title">{layer.label}</div>
            {(layer.groups || []).map((id: string) => {
              const group = byId[id]
              if (!group) return null
              return (
                <div className={`arch-card arch-${group.layer}`} key={id}>
                  <div className="arch-card-title">
                    <b>{group.name || group.label}</b>
                    {group.source === 'user' ? <span className="arch-badge">手动</span> : null}
                    {group.source === 'ai' ? <span className="arch-badge arch-badge-ai">AI</span> : null}
                  </div>
                  {group.purpose ? <div className="arch-purpose">{group.purpose}</div> : null}
                  <small>{group.file_count} 个文件{group.code_files ? ` · ${group.code_files} 个代码文件` : ''}</small>
                  <label className="arch-owner">
                    <span>负责人</span>
                    <input className="arch-owner-input" placeholder="未指定" value={owners[id] ?? (group.owner && group.owner !== '未指定' ? group.owner : '')}
                      onChange={(event) => setOwners((value) => ({ ...value, [id]: event.target.value }))} />
                  </label>
                  {group.entrypoints?.length ? <div className="arch-entry">入口：{group.entrypoints.slice(0, 4).join('、')}</div> : null}
                  <details><summary>文件</summary><ul>{group.files.slice(0, 20).map((file: string) => <li key={file}>{file}</li>)}</ul></details>
                  {group.source === 'user' ? <button className="arch-remove" onClick={() => removeManual(group.id)}>移除手动模块</button> : null}
                </div>
              )
            })}
          </section>
        ))}
      </div>
      {edges.length ? <div className="arch-deps">
        <div className="arch-layer-title">模块依赖</div>
        {edges.slice(0, 40).map((edge: any) => (
          <div className="arch-edge" key={`${edge.source}-${edge.target}`}>
            <span>{byId[edge.source]?.name || byId[edge.source]?.label || edge.source}</span>
            <i>→</i>
            <span>{byId[edge.target]?.name || byId[edge.target]?.label || edge.target}</span>
            <small>{edge.label || (edge.count > 1 ? `${edge.count} 次引用` : '')}</small>
          </div>
        ))}
      </div> : null}
    </div>
  )
}

function ThinkingCard({ startedAt, events = [], answer = '', thought = '', mode = 'agent', onCancel }: { startedAt: number; events?: any[]; answer?: string; thought?: string; mode?: 'agent' | 'chat'; onCancel?: () => void }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])

  const seconds = Math.max(0, Math.floor((now - startedAt) / 1000))
  const planEvent = [...events].reverse().find((event) => event?.phase === 'plan')
  const toolRows = new Map<string, any>()
  for (const event of events) {
    if (event?.phase === 'tool') {
      // Repeated calls with identical arguments are distinct invocations.
      if (event.call_id) toolRows.set(String(event.call_id), { ...toolRows.get(String(event.call_id)), ...event })
    }
  }
  const rows = [...(planEvent ? [planEvent] : []), ...Array.from(toolRows.values()).slice(-8)]
  const chatMode = mode === 'chat'
  return (
    <div className="thinking-card">
      <div className="thinking-head"><span className="thinking-spinner" /> {chatMode ? '正在回复…' : '正在思考与执行工作流…'} <span className="thinking-time">{seconds}s</span>{onCancel ? <button className="thinking-stop" onClick={onCancel}>停止</button> : null}</div>
      <div className="thinking-sub">{chatMode ? '自由对话 · 不检索项目、不调用工具' : '规划步骤 → 检索会议/项目文件 → 调用工具 → 核对证据'}</div>
      {!chatMode && rows.length ? (
        <div className="activity-feed thinking-feed">
          {rows.map((event: any, index: number) => {
            if (event.phase === 'plan') {
              return <div className="activity-row activity-muted" key={`tp-${index}`}><ListChecks size={14} /><span className="activity-label">计划</span><span className="activity-detail">{event.steps || 0} 步</span></div>
            }
            const info = activityInfo(event)
            return (
              <div className={`activity-row activity-${info.kind}`} key={`tt-${index}`}>
                {info.kind === 'search' ? <Search size={14} /> : info.kind === 'write' ? <Pencil size={14} /> : info.kind === 'list' ? <Folder size={14} /> : info.kind === 'read' ? <FileText size={14} /> : info.kind === 'command' ? <Terminal size={14} /> : <Wrench size={14} />}
                <span className="activity-label">{info.label}</span>
                {info.path ? <span className="activity-path">{fileIcon({ name: info.path, type: 'file' } as UiFile)}<span>{info.path}</span></span> : <span className="activity-detail">{info.detail}</span>}
                <span className="activity-meta">{event.status === 'done' ? '✓' : event.status === 'error' ? '失败' : '运行中'}</span>
              </div>
            )
          })}
        </div>
      ) : null}
      {thought ? <div className="thinking-thought"><span>思考</span>{thought}</div> : null}
      {answer ? <div className="thinking-answer">{answer}<span className="thinking-cursor" /></div> : null}
    </div>
  )
}

export default function Home() {
  const [activeNav, setActiveNav] = useState('工作台')
  const [message, setMessage] = useState('')
  const [messages, setMessages] = useState<UiMessage[]>([{ role: 'assistant', text: '你好，我是你的会议 agent。\n我可以帮你准备议程、记录重点、提炼决策，并在会后自动生成行动项。', ts: Date.now() / 1000 }])
  const [rightTab, setRightTab] = useState<'files' | 'notes'>('files')
  const [showSettings, setShowSettings] = useState(false)
  const [showHelp, setShowHelp] = useState(false)
  const [files, setFiles] = useState<UiFile[]>(initialFiles)
  const [projects, setProjects] = useState<UiProject[]>(initialProjects)
  const [autoProject, setAutoProject] = useState<any>({ status: 'idle', folder: '', stage: '', progress: '', result: null, error: '' })
  const [now, setNow] = useState(Date.now())
  const [currentProject, setCurrentProject] = useState('')
  const [meetingTitle, setMeetingTitle] = useState('产品周会')
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
  const [expandedProjects, setExpandedProjects] = useState<Record<string, boolean>>({})
  const [showAllSessions, setShowAllSessions] = useState<Record<string, boolean>>({})
  const [selectedSession, setSelectedSession] = useState<any>(null)
  const [evidencePacks, setEvidencePacks] = useState<any[]>([])
  const [selectedPack, setSelectedPack] = useState<any>(null)
  const [verifyResult, setVerifyResult] = useState<any>(null)
  const [ledgerRows, setLedgerRows] = useState<any[]>([])
  const [kgNodes, setKgNodes] = useState<any[]>([])
  const [kgEdges, setKgEdges] = useState<any[]>([])
  const [kgStats, setKgStats] = useState<any>({})
  const [kgBusy, setKgBusy] = useState(false)
  const [kgMessage, setKgMessage] = useState('')
  const [kgSelected, setKgSelected] = useState<any>(null)
  const [kgNewNode, setKgNewNode] = useState({ kind: 'Action', name: '', description: '' })
  const [kgNewEdge, setKgNewEdge] = useState({ src: '', dst: '', relation: 'assigned_to', valid_from: '' })
  const [kgQuery, setKgQuery] = useState('')
  const [kgKindFilter, setKgKindFilter] = useState('all')
  const [kgHover, setKgHover] = useState('')
  const [kgView, setKgView] = useState({ x: 0, y: 0, w: 920, h: 620 })
  const [momentQuery, setMomentQuery] = useState('')
  const [momentResults, setMomentResults] = useState<any[]>([])
  const [projectAction, setProjectAction] = useState('')
  const [moduleLoading, setModuleLoading] = useState(false)
  const [config, setConfig] = useState<any>({})
  const [llmStatus, setLlmStatus] = useState<any>(null)
  const [modelInfo, setModelInfo] = useState<any>(null)
  const [llmTest, setLlmTest] = useState<{ state: 'idle' | 'testing' | 'ok' | 'error'; message: string }>({ state: 'idle', message: '' })
  const [apiPreset, setApiPreset] = useState('custom')
  const [settingsTab, setSettingsTab] = useState('常规')
  const [showInspector, setShowInspector] = useState(true)
  const [autoNotes, setAutoNotes] = useState(true)
  const [desktopNotifications, setDesktopNotifications] = useState(true)
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const tfTimer = useRef<any>(null)
  const lastEventRef = useRef(0)
  const conversationRef = useRef<HTMLDivElement>(null)
  const speakerSetRef = useRef<Set<string>>(new Set())
  const chatActiveRef = useRef(false)
  const chatTouchedRef = useRef(false)
  const projectEpochRef = useRef(0)
  const projectRef = useRef('')
  const activeRunRef = useRef<ChatRunIdentity | null>(null)
  const requestEpochRef = useRef<Record<string, number>>({})
  const guardRequest = (key: string) => {
    const epoch = projectEpochRef.current
    const request = (requestEpochRef.current[key] || 0) + 1
    requestEpochRef.current[key] = request
    return () => epoch === projectEpochRef.current && requestEpochRef.current[key] === request
  }
  const notesRef = useRef<HTMLDivElement>(null)
  const [viewer, setViewer] = useState<any>(null)
  const [filePreview, setFilePreview] = useState<{ name: string; lines: string[] }>({
    name: 'app.tsx',
    lines: [
      'import { meetingAgent } from "@meeting/core"',
      '',
      'const meeting = await meetingAgent.start({',
      '  title: "产品周会",',
      '  autoNotes: true,',
      '})',
    ],
  })
  const bridgeReady = useRef(false)

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    if (!modeMenu) return
    const close = () => setModeMenu(false)
    document.addEventListener('click', close)
    return () => document.removeEventListener('click', close)
  }, [modeMenu])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      let api = await getBridge()
      if (!api && !cancelled) {
        // The inline bootstrap dispatches `mvbridge` when WebView2 attaches the
        // API (possibly after React's first pass); wait for it without relying
        // on timers, which are throttled while the window is occluded.
        api = await new Promise<any>((resolve) => {
          let done = false
          const finish = () => {
            if (done) return
            done = true
            window.removeEventListener('mvbridge', finish)
            void getBridge().then(resolve)
          }
          window.addEventListener('mvbridge', finish)
          setTimeout(() => {
            if (done) return
            done = true
            window.removeEventListener('mvbridge', finish)
            resolve(null)
          }, 15000)
        })
      }
      if (!api || cancelled) return
      bridgeReady.current = true
      await call('page_ready', { page: 'home', version: '0.28.4' })
      await call('client_metrics', {
        innerWidth: window.innerWidth, innerHeight: window.innerHeight,
        dpr: window.devicePixelRatio,
        screenWidth: window.screen.width, screenHeight: window.screen.height,
        outerWidth: window.outerWidth, outerHeight: window.outerHeight,
      })
      try {
        const [info, cfg] = await Promise.all([call('bootstrap'), call('get_config')])
        if (cancelled) return
        if (info?.meeting_running) setRecording(true)
        setConfig(cfg || {})
        setLlmStatus(info?.llm || null)
        const configured = Array.isArray(cfg?.projects) ? cfg.projects : []
        if (configured.length) {
          setProjects(configured.map((path: string, index: number) => ({
            name: projectName(path), tone: ['purple', 'blue', 'green'][index % 3],
            time: index === 0 ? '当前' : '', path,
          })))
        }
        const project = String(cfg?.last_project || configured[0] || '')
        if (project) await selectProject(project, false, false)
        const sessions = await call('list_sessions')
        if (Array.isArray(sessions) && sessions.length && !project) {
          setMeetingTitle(projectName(String(sessions[0].sid || '会议')))
        }
        const groups = await call('list_library')
        if (!cancelled && Array.isArray(groups)) setLibraryGroups(groups)
      } catch {
        // Browser/Vercel preview keeps the original mock surface.
      }
    })()
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (projects.some((project) => project.path)) return
    const hydrate = async () => {
      try {
        const [info, cfg] = await Promise.all([call('bootstrap'), call('get_config')])
        setConfig(cfg || {})
        setLlmStatus(info?.llm || null)
        const configured = Array.isArray(cfg?.projects) ? cfg.projects : []
        if (configured.length) {
          setProjects(configured.map((path: string, index: number) => ({
            name: projectName(path), tone: ['purple', 'blue', 'green'][index % 3],
            time: index === 0 ? '当前' : '', path,
          })))
        }
        const project = String(cfg?.last_project || configured[0] || '')
        if (project && !currentProject) await selectProject(project, false, false)
        const groups = await call('list_library')
        if (Array.isArray(groups)) setLibraryGroups(groups)
      } catch { /* bridge still starting */ }
    }
    const retry = () => { void hydrate(); void ensureProject() }
    window.addEventListener('focus', retry)
    window.addEventListener('pointerdown', retry)
    window.addEventListener('mvbridge', retry)
    let attempts = 0
    const timer = setInterval(() => {
      attempts += 1
      if (projects.some((project) => project.path) || attempts > 10) {
        clearInterval(timer)
        return
      }
      retry()
    }, 1000)
    return () => {
      clearInterval(timer)
      window.removeEventListener('focus', retry)
      window.removeEventListener('pointerdown', retry)
      window.removeEventListener('mvbridge', retry)
    }
  }, [projects, currentProject])

  useEffect(() => {
    if (currentProject) return
    // The first click wakes a background/occluded WebView2; recover the project
    // then instead of leaving the surface on mock data.
    const retry = () => { void ensureProject() }
    window.addEventListener('pointerdown', retry)
    window.addEventListener('focus', retry)
    return () => {
      window.removeEventListener('pointerdown', retry)
      window.removeEventListener('focus', retry)
    }
  }, [currentProject])

  useEffect(() => {
    let cancelled = false
    const project = currentProject
    const epoch = projectEpochRef.current
    const current = () => !cancelled && epoch === projectEpochRef.current
    if (!project) return
    if (!['会议纪要', '会议档案', '证据包', '承诺账本', '项目架构'].includes(activeNav)) return
    setModuleLoading(true)
    ;(async () => {
      try {
        if (activeNav === '会议纪要' || activeNav === '会议档案') {
          const result = await call('list_library')
          const rows = Array.isArray(result) ? result : (result?.groups || [])
          const flattened = rows
            .filter((group: any) => String(group?.project || '') === String(project))
            .flatMap((group: any) => (group.sessions || []).map((session: any) => ({
              ...session, project: group.project || group.name || '',
            })))
          if (current()) setSessions(flattened)
        } else if (activeNav === '证据包') {
          const result = await call('evidence_packs', project)
          if (current()) setEvidencePacks(result?.packs || [])
        } else if (activeNav === '承诺账本') {
          const result = await call('commitment_ledger', project, '')
          if (current()) setLedgerRows(result?.rows || [])
        } else if (activeNav === '__legacy_kg__') {
          const data = await call('kg_list', project)
          if (current()) {
            const nodes = Array.isArray(data?.nodes) ? data.nodes : []
            const edges = Array.isArray(data?.edges) ? data.edges : []
            setKgNodes(nodes)
            setKgEdges(edges)
            setKgStats({ nodes: nodes.length, edges: edges.length })
          }
        }
      } catch {
        // leave the module empty; the surface is still usable
      } finally {
        if (current()) setModuleLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [activeNav, currentProject])

  useEffect(() => {
    if (activeNav !== '录屏') return
    let cancelled = false
    const tick = async () => {
      try {
        const result = await call('screen_state')
        if (!cancelled) {
          setScreenState(result?.screen || {})
          setScreenPhase(String(result?.phase || ''))
        }
      } catch { /* recorder may not be running */ }
    }
    void tick()
    const timer = setInterval(tick, 1000)
    return () => { cancelled = true; clearInterval(timer) }
  }, [activeNav])

  useEffect(() => {
    if (!recording && !finishing) return
    let failures = 0
    const timer = setInterval(async () => {
      try {
        const result = await call('poll_events', lastEventRef.current, false)
        failures = 0
        lastEventRef.current = Number(result?.next || lastEventRef.current)
        const events = result?.events || []
        const captions = events
          .filter((event: any) => event?.type === 'line' && event?.text)
          .map((event: any) => `[${event.clock || ''}] ${event.spk || 'speaker'}: ${event.text}`)
        const partialEvents = events.filter((event: any) => event?.type === 'partial_line')
        if (partialEvents.length) {
          setLiveCaptions((previous) => {
            let next = { ...previous }
            for (const event of partialEvents) {
              const key = String(event.id || 'current')
              if (event.clear) next = {}
              else if (event.text) next[key] = String(event.text)
              else delete next[key]
            }
            return next
          })
        }
        const speakers = events
          .filter((event: any) => event?.type === 'line' && event?.spk)
          .map((event: any) => String(event.spk))
        let speakerChanged = false
        for (const speaker of speakers) {
          if (!speakerSetRef.current.has(speaker)) {
            speakerSetRef.current.add(speaker)
            speakerChanged = true
          }
        }
        if (speakerChanged) setSpeakerCount(speakerSetRef.current.size)
        const phases = events
          .filter((event: any) => event?.type === 'capture_state' && event?.text)
          .map((event: any) => `● ${event.text}`)
        const diagnostics = events
          .filter((event: any) => ['log', 'status', 'recording_warning'].includes(event?.type) && event?.text)
          .map((event: any) => String(event.text))
          .filter((text: string) => /\[done\]|零命中|本场|Traceback|Error|失败|错误|模型/.test(text))
        const failuresText = events
          .filter((event: any) => event?.type === 'asr_state' && event?.error)
          .map((event: any) => `● ${event.error}`)
        // 同一条 [done] 会同时以 log 和 status 出现；按文本去重，避免纪要里重复两遍。
        const lines = Array.from(new Set([...phases, ...captions, ...diagnostics, ...failuresText]))
        if (lines.length) {
          setNotes((previous) => [previous, ...lines].filter(Boolean).slice(-300).join('\n'))
        }
        const exits = events.filter((event: any) => event?.type === 'exit')
        if (exits.length) {
          setRecording(false)
          setFinishing(false)
          const last = exits[exits.length - 1]
          const code = Number(last.code ?? 0)
          setNotes((previous) => [previous, code === 0
            ? '● 会议已结束，纪要正在归档。'
            : `● 会议异常结束（code=${last.code ?? ''}）${last.last_log ? '：' + last.last_log : ''}`
          ].filter(Boolean).join('\n'))
          void call('list_library').then((groups) => {
            if (Array.isArray(groups)) setLibraryGroups(groups)
          }).catch(() => {})
          if (code === 0 && currentProject) void refreshKnowledgeAfterMeeting(currentProject)
        }
      } catch {
        failures += 1
        if (failures === 3) {
          setNotes((previous) => [previous, '● 暂时读不到会议状态；如果一直没有字幕，请重启应用。'].filter(Boolean).join('\n'))
        }
      }
    }, 1000)
    return () => clearInterval(timer)
  }, [recording, finishing])

  useEffect(() => {
    if (!recording && !finishing) return
    const view = notesRef.current
    if (view) view.scrollTop = view.scrollHeight
  }, [notes, recording, finishing])

  useEffect(() => {
    const view = conversationRef.current
    if (!view) return
    const nearBottom = view.scrollHeight - view.scrollTop - view.clientHeight < 160
    if (busy || nearBottom) view.scrollTop = view.scrollHeight
  }, [messages, busy])

  useEffect(() => {
    if (!finishing) return
    const timer = setTimeout(() => {
      setFinishing(false)
      setNotes((previous) => [previous, '● 已停止等待（引擎仍在收尾时，字幕可能延迟出现）。'].filter(Boolean).join('\n'))
    }, 60000)
    return () => clearTimeout(timer)
  }, [finishing])

  async function selectProject(path: string, switchView = true, auto = true) {
    if (!path) return
    const name = projectName(path)
    if (path !== projectRef.current) {
      // Project-scoped UI state must not leak across projects: the graph edit
      // form and chat thread are the two places where stale data is most
      // visible (an old node name can be saved into the new project).
      const previousRun = activeRunRef.current
      if (previousRun) void call('chat_cancel', previousRun.folder, previousRun.conversation_id, previousRun.run_id).catch(() => {})
      activeRunRef.current = null
      chatActiveRef.current = false
      chatTouchedRef.current = false
      projectEpochRef.current += 1
      setBusy(false)
      setChatCancelable(false)
      setConversationId('')
      setMessages([{
        role: 'assistant',
        text: `已切换到项目「${name}」。可以让我读取文件、查会议，或直接说“列出项目目录”。`,
        ts: Date.now() / 1000,
      }])
      setNotes('')
      setLiveCaptions({})
      setViewer(null)
      setAutoProject({ status: 'idle', folder: path, stage: '', progress: '', result: null, error: '' })
      setMomentResults([])
      setVerifyResult(null)
      setKgBusy(false)
      if (tfTimer.current) { clearInterval(tfTimer.current); tfTimer.current = null }
      speakerSetRef.current.clear()
      setSpeakerCount(0)
      setFiles([])
      setModuleLoading(true)
      setSessions([])
      setSelectedSession(null)
      setSelectedPack(null)
      setEvidencePacks([])
      setLedgerRows([])
      setFilePreview({ name: '', lines: [] })
      setKgNodes([])
      setKgEdges([])
      setKgStats({})
      setKgSelected(null)
      setKgNewNode({ kind: 'Action', name: '', description: '' })
      setKgNewEdge({ src: '', dst: '', relation: 'assigned_to', valid_from: '' })
      setKgQuery('')
      setKgKindFilter('all')
      setKgHover('')
      setKgView({ x: 0, y: 0, w: 920, h: 620 })
      setKgMessage('')
    }
    const epoch = projectEpochRef.current
    projectRef.current = path
    setCurrentProject(path)
    setMeetingTitle(name)
    if (switchView) setActiveNav('工作台')
    setProjects((previous) => {
      if (previous.some((item) => item.path === path || item.name === name)) {
        return previous.map((item) => item.path === path || item.name === name
          ? { ...item, path, time: '当前' } : item)
      }
      return [{ name, tone: 'purple', time: '当前', path }, ...previous].slice(0, 12)
    })
    let scan: any = null
    try {
      scan = await call('scan_project', path)
      if (epoch !== projectEpochRef.current) return
      if (scan?.ok && Array.isArray(scan.tree)) {
        setFiles(flattenTree(scan.tree))
      } else {
        setFiles([])
        setFilePreview({ name: '', lines: [] })
      }
    } catch {
      if (epoch !== projectEpochRef.current) return
      setFiles([])
      setFilePreview({ name: '', lines: [] })
    } finally {
      if (epoch === projectEpochRef.current) setModuleLoading(false)
    }
    if (auto) void startAutoUnderstand(path, scan)
    try {
      const conversations = await call('chat_list', path)
      if (epoch !== projectEpochRef.current) return
      const rows = conversations?.conversations || []
      if (rows.length) {
        const loaded = await call('chat_load', path, rows[0].id)
        if (epoch !== projectEpochRef.current) return
        const conversation = loaded?.conversation
        if (conversation && !chatActiveRef.current && !chatTouchedRef.current) {
          setConversationId(String(conversation.id || rows[0].id || ''))
          const loadedMessages = (conversation.messages || [])
            .filter((row: any) => row.role === 'user' || row.role === 'assistant')
            .map((row: any) => ({
              ...answerMetadata(row), role: row.role, text: bridgeText(row), actions: row.actions || [],
              citations: row.citations || [], mode: row.mode,
              plan: Array.isArray(row.plan) ? row.plan : [],
              observations: Array.isArray(row.observations) ? row.observations : [],
              usage: row.usage || null, trace: row.trace || '',
              durationMs: Number(row.elapsed_ms) || undefined, ts: Number(row.ts) || undefined,
            }))
          if (loadedMessages.length) setMessages(loadedMessages)
        }
      } else if (!chatActiveRef.current) {
        const created = await call('chat_new', path)
        if (epoch !== projectEpochRef.current || chatActiveRef.current || chatTouchedRef.current) return
        if (created?.conversation_id) setConversationId(String(created.conversation_id))
      }
    } catch { /* chat routes are optional in preview */ }
  }

  function applyWorkspace(path: string, state: any) {
    if (!sameProject(projectRef.current, path) || !sameProject(String(state.project || state.folder || ''), path)) return
    const statuses: Record<string, string> = { completed: 'needs_orientation', running: 'running', pending: 'running', idle: 'idle' }
    const errors = (state.errors || []).map((error: any) => typeof error === 'string' ? error : error.message || error.error || error.code || JSON.stringify(error))
    setAutoProject((previous: any) => {
      if (sameProject(previous.folder || '', path) && Number(previous.result?.updated_at || 0) > Number(state.updated_at || 0)) return previous
      return { folder: path, job_id: state.job_id, status: statuses[state.job_status] || state.job_status,
        stage: state.stage || '', progress: String(state.stages?.[state.stage]?.progress?.current || state.stages?.[state.stage]?.progress || ''),
        error: errors.join('；'), result: { ...state, doc_count: state.discovered ?? 0, rag: state.totals || {},
          kg: state.graph || {}, overview: state.exploration?.overview || '', workspace: state.workspace || {} } }
    })
  }

  useEffect(() => {
    if (!currentProject || !isDesktop()) return
    // Default starts only observe changes / attach; paused ledgers are never renewed.
    void startAutoUnderstand(currentProject, null)
    const unsubscribe = subscribeWorkspace(currentProject, (state) => applyWorkspace(currentProject, state), (error) => {
      if (sameProject(currentProject, projectRef.current)) setAutoProject((previous: any) => ({ ...previous, folder: currentProject,
        status: 'partial', error: `状态未确认：${error.message}` }))
    })
    const refresh = setInterval(() => { void startAutoUnderstand(currentProject, null, false) }, 30000)
    return () => { unsubscribe(); clearInterval(refresh) }
  }, [currentProject, llmStatus?.ready])

  useEffect(() => {
    if (!currentProject || !isDesktop()) return
    let cancelled = false
    void call('model_status').then((info) => { if (!cancelled) setModelInfo(info) }).catch(() => {})
    return () => { cancelled = true }
  }, [currentProject, llmStatus?.ready, autoProject.status])

  async function startAutoUnderstand(path: string, scan: any, force = false) {
    if (!path || !isDesktop() || path !== projectRef.current) return
    const current = guardRequest('understanding')
    try {
      const state = await call('workspace_start', path, force)
      if (!current()) return
      if (state?.ok !== true) throw new Error(state?.error || '工作区任务未启动')
      applyWorkspace(path, state)
    } catch (error: any) {
      if (current()) setAutoProject((previous: any) => ({ ...previous, folder: path, status: 'failed', error: String(error?.message || error) }))
    }
  }

  async function refreshKnowledgeAfterMeeting(path: string) {
    await startAutoUnderstand(path, null)
  }

  async function openSession(session: any) {
    if (session.project && session.project !== currentProject) {
      await selectProject(String(session.project), false, false)
    }
    if (session.project && session.project !== projectRef.current) return
    const current = guardRequest('notes')
    setSelectedSession(session)
    setActiveNav('工作台')
    setShowInspector(true)
    setRightTab('notes')
    setBusy(true)
    try {
      const detail = await call('session_detail', String(session.sid || ''))
      if (!current()) return
      setNotes(String(detail?.minutes || detail?.reports || '暂无纪要'))
    } finally {
      if (current()) setBusy(false)
    }
  }

  async function generateMinutes() {
    if (!selectedSession || busy) return
    const current = guardRequest('notes')
    setBusy(true)
    setNotes('正在用模型 API 生成纪要…')
    try {
      const result = await call('digest_session', String(selectedSession.sid || ''), currentProject)
      if (!current()) return
      if (result?.ok) {
        setNotes(String(result.minutes || '暂无纪要'))
      } else {
        setNotes(`纪要怎么没生成：${result?.error || '请先在设置 → 模型服务接入 API。'}`)
      }
    } catch (error: any) {
      if (!current()) return
      setNotes(`纪要怎么没生成：${error?.message || error}`)
    } finally {
      if (current()) setBusy(false)
    }
  }

  async function loadKg() {
    if (!currentProject) return
    const current = guardRequest('kg')
    setKgBusy(true)
    try {
      const data = await call('kg_list', currentProject)
      if (!current()) return
      if (data?.ok !== true) throw new Error(data?.error || '图谱读取失败')
      const nodes = Array.isArray(data?.nodes) ? data.nodes : []
      const edges = Array.isArray(data?.edges) ? data.edges : []
      setKgNodes(nodes)
      setKgEdges(edges)
      setKgStats({ nodes: nodes.length, edges: edges.length })
      setKgMessage('')
    } catch (error: any) {
      if (!current()) return
      setKgMessage(String(error?.message || error))
    } finally {
      if (current()) setKgBusy(false)
    }
  }

  async function saveKgNode() {
    if (!currentProject || !kgNewNode.name.trim()) return
    const current = guardRequest('kg-write')
    setKgBusy(true)
    try {
      const result = await call('kg_add_node', currentProject, kgNewNode.kind,
                                kgNewNode.name.trim(), kgNewNode.description.trim())
      if (!current()) return
      setKgMessage(result?.ok ? '实体已保存' : `保存失败：${result?.error || '未知错误'}`)
      if (result?.ok) {
        setKgNewNode({ kind: kgNewNode.kind, name: '', description: '' })
        setKgSelected(null)
        await loadKg()
      }
    } finally {
      if (current()) setKgBusy(false)
    }
  }

  async function deleteKgNode(node: any) {
    if (!currentProject || !node?.id) return
    if (!window.confirm(`删除实体「${node.name || node.id}」及其关系？`)) return
    const current = guardRequest('kg-write')
    setKgBusy(true)
    try {
      const result = await call('kg_delete_node', currentProject, node.id)
      if (!current()) return
      setKgMessage(result?.ok ? '实体已删除' : `删除失败：${result?.error || '未知错误'}`)
      if (kgSelected?.id === node.id) setKgSelected(null)
      await loadKg()
    } finally {
      if (current()) setKgBusy(false)
    }
  }

  async function saveKgEdge() {
    if (!currentProject || !kgNewEdge.src || !kgNewEdge.dst || !kgNewEdge.relation) return
    const current = guardRequest('kg-write')
    setKgBusy(true)
    try {
      const result = await call('kg_add_edge', currentProject, kgNewEdge.src,
                                kgNewEdge.dst, kgNewEdge.relation, kgNewEdge.valid_from)
      if (!current()) return
      setKgMessage(result?.ok ? '关系已保存' : `保存失败：${result?.error || '未知错误'}`)
      if (result?.ok) {
        setKgNewEdge({ src: '', dst: '', relation: 'assigned_to', valid_from: '' })
        await loadKg()
      }
    } finally {
      if (current()) setKgBusy(false)
    }
  }

  async function deleteKgEdge(edge: any) {
    if (!currentProject || !edge?.id) return
    const current = guardRequest('kg-write')
    setKgBusy(true)
    try {
      const result = await call('kg_delete_edge', currentProject, edge.id)
      if (!current()) return
      setKgMessage(result?.ok ? '关系已删除' : `删除失败：${result?.error || '未知错误'}`)
      await loadKg()
    } finally {
      if (current()) setKgBusy(false)
    }
  }

  function selectKgNode(node: any) {
    setKgSelected(node)
    setKgNewNode({ kind: node.kind, name: node.name, description: node.description || '' })
  }

  async function searchKg() {
    if (!currentProject || !kgQuery.trim()) return
    const current = guardRequest('kg')
    setBusy(true)
    try {
      const result = await call('kg_neighbors', currentProject, kgQuery.trim(), 2, '')
      if (!current()) return
      setKgNodes(result?.nodes || [])
      setKgEdges(result?.edges || [])
    } finally {
      if (current()) setBusy(false)
    }
  }

  async function runProjectAction(action: 'sync') {
    if (!currentProject) return
    setProjectAction('已请求新的有界调查；地图与关键认知分别记录，旧调查用量保留审计。')
    await startAutoUnderstand(currentProject, null, true)
  }

  async function findMoment() {
    if (!currentProject || !momentQuery.trim()) return
    const current = guardRequest('moment')
    setBusy(true)
    try {
      const result = await call('find_moment', currentProject, momentQuery.trim(), '', 5)
      if (!current()) return
      setMomentResults(result?.segments || [])
    } finally {
      if (current()) setBusy(false)
    }
  }

  async function verifyPack(pack: any) {
    if (!currentProject) return
    const current = guardRequest('verify')
    setSelectedPack(pack)
    setVerifyResult(null)
    setBusy(true)
    try {
      const result = await call('evidence_verify', currentProject, pack.name)
      if (!current()) return
      setVerifyResult(result || { ok: false, error: '校验失败' })
    } finally {
      if (current()) setBusy(false)
    }
  }

  async function sendMessage() {
    if (!message.trim() || busy || chatActiveRef.current) return
    const text = message.trim()
    const epoch = projectEpochRef.current
    const runId = newRunId()
    const pendingId = `pending-${runId}`
    const startedAt = Date.now()
    chatActiveRef.current = true
    chatTouchedRef.current = true
    setMessage('')
    setBusy(true)
    setMessages((previous) => [...previous, { role: 'user', text, ts: Date.now() / 1000 }])
    setMessages((previous) => [...previous, { id: pendingId, role: 'assistant', text: '', thinking: true, thinkingMode: chatMode, startedAt, ts: Date.now() / 1000 }])
    const replacePending = (next: UiMessage) => {
      if (epoch !== projectEpochRef.current) return
      setMessages((previous) => previous.map((item) => item.id === pendingId ? { ...next, id: pendingId } : item))
    }
    try {
      if (!currentProject) {
        replacePending({ role: 'assistant', text: '先选择一个项目，我才能检索会议和项目记忆。' })
        return
      }
      let cid = conversationId
      if (!cid) {
        const created = await call('chat_new', currentProject)
        if (epoch !== projectEpochRef.current) return
        cid = String(created?.conversation_id || '')
        if (cid) setConversationId(cid)
      }
      if (!cid) throw new Error('conversation_unavailable')
      if (epoch !== projectEpochRef.current) return
      const identity: ChatRunIdentity = { folder: currentProject, conversation_id: cid, run_id: runId }
      activeRunRef.current = identity
      setChatCancelable(true)
      const baseline = await call('poll_events', 0, false)
      if (epoch !== projectEpochRef.current) return
      let cursor = Number(baseline?.next || 0)
      let live = true
      const poll = (async () => {
        while (live && epoch === projectEpochRef.current) {
          await new Promise((resolve) => setTimeout(resolve, 350))
          try {
            const events = await call('poll_events', cursor, false)
            if (!live || epoch !== projectEpochRef.current || activeRunRef.current !== identity) return
            cursor = Number(events?.next || cursor)
            const progress = (events?.events || []).filter((event: any) =>
              event?.type === 'agent_progress' && matchesChatRun(event, identity))
            if (progress.length) {
              const answerDelta = progress.filter((event: any) => event?.phase === 'answer_delta' && event?.text)
                .map((event: any) => String(event.text)).join('')
              const thoughtDelta = progress.filter((event: any) => event?.phase === 'thought_delta' && event?.text)
                .map((event: any) => String(event.text)).join('')
              setMessages((previous) => previous.map((item) => item.id === pendingId
                ? {
                  ...item,
                  liveEvents: [...(item.liveEvents || []), ...progress].slice(-80),
                  liveAnswer: (item.liveAnswer || '') + answerDelta,
                  liveThought: (item.liveThought || '') + thoughtDelta,
                }
                : item))
            }
          } catch { /* transient bridge error */ }
        }
      })()
      let result: any
      try {
        result = await call('chat_send', currentProject, cid, text, chatMode, runId)
      } finally {
        live = false
        await poll
      }
      if (epoch !== projectEpochRef.current) return
      if (!matchesChatRun(result, identity)) throw new Error('聊天响应运行身份不匹配')
      if (!result?.ok || !result?.message) {
        replacePending({ ...answerMetadata(result), answer_status: result?.answer_status || 'failed', role: 'assistant', text: result?.error || '这次没有完成。' })
        return
      }
      const reply = result.message
      replacePending({
        ...answerMetadata(reply), role: 'assistant', text: bridgeText(reply), actions: reply.actions || [],
        citations: reply.citations || [], mode: reply.mode,
        focus: reply.focus || [], facts: reply.facts || [],
        plan: Array.isArray(reply.plan) ? reply.plan : [],
        observations: Array.isArray(reply.observations) ? reply.observations : [],
        usage: reply.usage || null, trace: reply.trace || '',
        durationMs: Number(reply.elapsed_ms) || (Date.now() - startedAt),
        ts: Number(reply.ts) || Date.now() / 1000,
      })
    } catch (error: any) {
      replacePending({ role: 'assistant', answer_status: 'failed', reason: String(error?.message || error), text: `调用失败：${error?.message || error}` })
    } finally {
      if (epoch === projectEpochRef.current) {
        activeRunRef.current = null
        chatActiveRef.current = false
        setChatCancelable(false)
        setBusy(false)
      }
    }
  }

  async function cancelChat() {
    const identity = activeRunRef.current
    if (!identity) return
    try {
      const result = await call('chat_cancel', identity.folder, identity.conversation_id, identity.run_id)
      if (activeRunRef.current !== identity) return
      setNotes((previous) => [previous, result?.ok ? '● 正在停止生成…'
        : `● 停止尚未确认：${result?.error || '请核对运行状态'}`].filter(Boolean).join('\n'))
    } catch (error: any) {
      if (activeRunRef.current === identity) setNotes((previous) =>
        [previous, `● 停止尚未确认：${error?.message || error}`].filter(Boolean).join('\n'))
    }
  }

  async function executeAction(action: UiAction) {
    if (!currentProject || !conversationId || busy) return
    const epoch = projectEpochRef.current
    const project = currentProject
    const cid = conversationId
    setBusy(true)
    const applyStatus = (status: string) => {
      if (epoch !== projectEpochRef.current) return
      setMessages((previous) => previous.map((item) => ({ ...item,
        actions: (item.actions || []).map((row) => row.id === action.id ? { ...row, status } : row),
      })))
    }
    try {
      const result = await call('chat_action', project, cid, action.id)
      if (epoch !== projectEpochRef.current) return
      if (result?.message) setMessages((previous) => [...previous, {
        ...answerMetadata(result.message), role: 'assistant', text: bridgeText(result.message), actions: result.message.actions || [],
      }])
      let status = String(result?.status || 'unverified')
      applyStatus(status)
      if (status === 'unverified') setNotes(`操作状态待确认：${result?.error || '尚未获得持久执行回执'}`)
      if (['running', 'approved', 'confirmed', 'cancel_requested', 'pending'].includes(status)) {
        // Approval is not completion. Backend dispatches and writes the receipt.
        for (let attempt = 0; attempt < 900 && epoch === projectEpochRef.current; attempt += 1) {
          await new Promise((resolve) => setTimeout(resolve, 1000))
          if (epoch !== projectEpochRef.current) return
          const state = await call('chat_action_status', project, cid, action.id)
          if (epoch !== projectEpochRef.current) return
          if (!state?.ok) break
          status = String(state.status || 'running')
          applyStatus(status)
          if (!['running', 'approved', 'confirmed', 'cancel_requested', 'pending'].includes(status)) break
        }
      }
      if (['done', 'succeeded'].includes(status)) {
        if (action.type === 'start_meeting') { setRecording(true); setFinishing(false) }
        if (action.type === 'stop_meeting') { setRecording(false); setFinishing(false) }
        if (action.type === 'write_file' || action.type === 'write_document') {
          setShowInspector(true)
          setRightTab('files')
          await refreshFiles()
        }
        if (!['start_meeting', 'stop_meeting'].includes(String(action.type || ''))) {
          // Agent-maintained workspace: refresh inventory/facts after any
          // completed write, memory, index or command action.
          await startAutoUnderstand(project, null)
        }
      }
    } catch (error: any) {
      // A transport failure is not proof the operation failed; leave it recoverable.
      if (epoch === projectEpochRef.current) setNotes(`操作状态待确认：${error?.message || error}`)
    } finally {
      if (epoch === projectEpochRef.current) setBusy(false)
    }
  }

  async function ensureProject(): Promise<string> {
    if (currentProject) return currentProject
    try {
      // Fallback for a cold/occluded WebView: the bootstrap effect can miss the
      // bridge window, but by the time the user clicks the API is definitely up.
      const cfg = await call('get_config')
      const project = String(cfg?.last_project || (Array.isArray(cfg?.projects) ? cfg.projects[0] : '') || '')
      if (project) {
        await selectProject(project, false, false)
        try {
          const groups = await call('list_library')
          if (Array.isArray(groups)) setLibraryGroups(groups)
        } catch { /* project loads without history */ }
        return project
      }
    } catch { /* leave it to the explicit error below */ }
    return ''
  }

  async function toggleRecording() {
    if (busy) return
    const project = await ensureProject()
    if (project && project !== projectRef.current) return
    const current = guardRequest('meeting-control')
    if (!project) {
      setRightTab('notes')
      setShowInspector(true)
      setNotes((previous) => [previous, '● 请先在左侧选择一个项目文件夹，再开始录音。'].filter(Boolean).join('\n'))
      return
    }
    setBusy(true)
    try {
      if (recording) {
        const result = await call('stop_meeting')
        if (!current()) return
        if (result?.ok) {
          setRecording(false)
          setFinishing(true)
          setNotes((previous) => [previous, '● 正在结束采集并转写尾段…'].filter(Boolean).join('\n'))
        } else {
          setMessages((previous) => [...previous, {
            role: 'assistant', text: `结束会议失败：${result?.error || '请稍后重试。'}`,
          }])
        }
      } else {
        // 先把事件游标推到当前末尾，避免把上一场的字幕重新贴进来。
        const baseline = await call('poll_events', 0, false)
        if (!current()) return
        lastEventRef.current = Number(baseline?.next || 0)
        const result = await call('start_meeting', project)
        if (!current()) return
        if (result?.ok) {
          speakerSetRef.current.clear()
          setSpeakerCount(0)
          setRecording(true)
          setFinishing(false)
          setRightTab('notes')
          setShowInspector(true)
          setNotes((previous) => [previous, '● 已开始采集；正在加载本地识别模型，说话后字幕会出现在这里。'].filter(Boolean).join('\n'))
        } else {
          setMessages((previous) => [...previous, {
            role: 'assistant', text: `会议操作失败：${result?.error || '请检查设置和项目。'}`,
          }])
          setRightTab('notes')
          setShowInspector(true)
          setNotes((previous) => [previous, `● 启动失败：${result?.error || '请检查设置和项目。'}`].filter(Boolean).join('\n'))
        }
      }
    } catch (error: any) {
      if (current()) setNotes((previous) => [previous, `● 会议操作失败：${error?.message || error}`].filter(Boolean).join('\n'))
    } finally { if (current()) setBusy(false) }
  }

  async function openFile(file: UiFile) {
    const rel = file.rel || file.name
    if (!rel) return
    const epoch = projectEpochRef.current
    const project = await ensureProject()
    if (epoch !== projectEpochRef.current || (project && project !== projectRef.current)) return
    const current = guardRequest('file')
    if (!project) {
      if (!isDesktop()) {
        const preview = await call('read_project_file', 'preview', rel)
        if (!current()) return
        setViewer({ ...preview, name: file.name, rel, project })
      } else {
        setViewer({ name: file.name, kind: 'error', meta: '请先选择一个项目文件夹，再查看文件。', text: '' })
      }
      return
    }
    try {
      const result = await call('read_project_file', project, rel)
      if (!current()) return
      if (!result?.ok) {
        setViewer({ name: file.name, meta: result?.error || '无法读取', kind: 'error', text: '' })
        return
      }
      const body = String(result.text || result.markdown || result.content || '')
      setFilePreview({ name: file.name, lines: body.split('\n').slice(0, 80) })
      setFiles((previous) => previous.map((item) => ({ ...item, active: item.name === file.name })))
      setViewer({ ...result, name: result.name || file.name, rel, project })
    } catch (error: any) {
      if (!current()) return
      setViewer({ name: file.name, meta: String(error?.message || error), kind: 'error', text: '' })
    }
  }

  async function chooseProject() {
    try {
      const picked = await call('pick_folder')
      if (picked) await selectProject(String(picked))
    } catch { /* native picker unavailable */ }
  }

  function openSettings(tab = '常规') {
    setSettingsTab(tab)
    setShowSettings(true)
  }

  function focusComposer() {
    if (!isChatView) setActiveNav('工作台')
    setTimeout(() => composerRef.current?.focus(), 40)
  }

  function openChatWith(prompt = '') {
    setActiveNav('工作台')
    setMessage(prompt)
    setTimeout(() => composerRef.current?.focus(), 50)
  }

  async function refreshFiles() {
    if (currentProject) await selectProject(currentProject, false, false)
  }

  async function attachMedia() {
    if (!currentProject) { focusComposer(); return }
    const current = guardRequest('media')
    try {
      const path = await call('pick_media')
      if (!current() || !path) return
      const result = await call('transcribe_media_start', String(path))
      if (!current()) return
      setMessages((previous) => [...previous, {
        role: 'assistant',
        text: result?.ok ? '已开始文件转写；完成后会生成 transcript.txt。' : `文件转写失败：${result?.error || '未知错误'}`,
      }])
      setActiveNav('工作台')
      if (result?.ok) {
        if (tfTimer.current) clearInterval(tfTimer.current)
        const timer = setInterval(async () => {
          if (!current()) { clearInterval(timer); return }
          try {
            const status = await call('transcribe_media_status')
            if (!current()) { clearInterval(timer); return }
            if (String(status?.file || '') !== String(path)) return
            if (!status?.running && status?.done) {
              clearInterval(timer)
              if (tfTimer.current === timer) tfTimer.current = null
              setMessages((previous) => [...previous, {
                role: 'assistant',
                text: status.ok ? `文件转写完成：${status.out || 'transcript.txt'}` : `文件转写失败：${status.error || '未知错误'}`,
              }])
            }
          } catch { /* keep existing transcript */ }
        }, 2000)
        tfTimer.current = timer
      }
    } catch (error: any) {
      if (!current()) return
      setMessages((previous) => [...previous, { role: 'assistant', text: `文件转写失败：${error?.message || error}` }])
    }
  }

  async function openHelp() {
    setShowHelp(true)
  }

  function settingsUpdates() {
    return {
      engine_python: config.engine_python || '',
      repo_dir: config.repo_dir || '',
      device: config.device || 'auto',
      audio_source: config.audio_source || 'both',
      asr_engine: config.asr_engine || 'local',
      interest_mode: config.interest_mode || 'manual',
      llm_provider: config.llm_provider || 'local',
      llm_base_url: config.llm_base_url || '',
      llm_model: config.llm_model || '',
      llm_api_key: config.llm_api_key || '',
      llm_reasoning_effort: config.llm_reasoning_effort || '',
    }
  }

  async function saveSettings() {
    await call('set_config', settingsUpdates())
    const fresh = await call('get_config')
    setConfig(fresh || {})
    setShowSettings(false)
  }

  async function testLLMConnection() {
    const updates = settingsUpdates()
    if (updates.llm_provider === 'custom' && (!updates.llm_base_url.trim() || !updates.llm_model.trim())) {
      setLlmTest({ state: 'error', message: '请先填写 Base URL 和模型名。' })
      return
    }
    if (updates.llm_provider === 'custom' && /^https?:\/\//i.test(updates.llm_api_key.trim())) {
      setLlmTest({ state: 'error', message: 'API Key 栏填成了网址。Base URL 填上一栏，API Key 填服务商控制台生成的令牌。' })
      return
    }
    setLlmTest({ state: 'testing', message: '已保存配置，正在用一次真实 JSON 生成验证…' })
    try {
      await call('set_config', updates)
      const result = await call('test_llm')
      if (result?.ok) {
        const model = result?.models?.[0] || updates.llm_model || '模型'
        setLlmTest({ state: 'ok', message: `连接成功：${model}${result?.note ? ` · ${result.note}` : ''}` })
        setLlmStatus({ up: true, model })
      } else {
        setLlmTest({ state: 'error', message: result?.error || '连接失败，请检查 Base URL、模型名和 Key。' })
      }
    } catch (error: any) {
      setLlmTest({ state: 'error', message: String(error?.message || error) })
    }
  }

  async function verifyModelAndContinue() {
    try {
      const result = await call('test_llm')
      const info = await call('model_status').catch(() => null)
      setModelInfo(info || (result?.ok ? { configured: true, ready: true, error: '' } : null))
      if (result?.ok) await startAutoUnderstand(currentProject, null)
      else setModelInfo((previous: any) => ({ ...(previous || {}), configured: true, ready: false, error: result?.error || '模型连接失败' }))
    } catch (error: any) {
      setModelInfo({ configured: true, ready: false, error: String(error?.message || error) })
    }
  }

  function openProject(project: UiProject) {
    const path = project.path || (currentProject && projectName(currentProject) === project.name ? currentProject : '')
    if (path) void selectProject(path)
    setMeetingTitle(project.name)
    setActiveNav('工作台')
  }

  const isChatView = activeNav === '工作台' || activeNav === '会议对话'
  const apiConfigured = config.llm_provider === 'custom'
    ? Boolean(String(config.llm_base_url || '').trim() && String(config.llm_model || '').trim())
    : Boolean(llmStatus?.up)
  const projectGroups = useMemo(() => {
    const groups = new Map<string, any>()
    for (const project of projects) {
      if (!project.path) continue
      groups.set(project.path, {
        project: project.path, name: project.name, sessions: [], latest: 0,
      })
    }
    for (const group of libraryGroups) {
      const key = String(group?.project || '')
      groups.set(key, {
        project: key,
        name: group?.name || (key ? projectName(key) : '未归属会议'),
        sessions: Array.isArray(group?.sessions) ? group.sessions : [],
        latest: Number(group?.latest || 0),
      })
    }
    return Array.from(groups.values())
      .filter((group) => group.project || group.sessions.length)
      .sort((a, b) => {
        if (!a.project && b.project) return 1
        if (a.project && !b.project) return -1
        return Number(b.latest || 0) - Number(a.latest || 0) || String(a.name).localeCompare(String(b.name))
      })
  }, [projects, libraryGroups])
  const defaultOpenKey = (() => {
    const current = projectGroups.find((group) => group.project === currentProject)
    if (current?.sessions?.length) return currentProject
    return projectGroups.find((group) => group.sessions?.length)?.project || currentProject
  })()
  const kgNodeMap = useMemo(() => {
    const map: Record<string, any> = {}
    for (const node of kgNodes) map[node.id] = node
    return map
  }, [kgNodes])
  const kgPresentKinds = useMemo(() => {
    const counts = new Map<string, number>()
    for (const node of kgNodes) counts.set(node.kind, (counts.get(node.kind) || 0) + 1)
    return Array.from(counts.entries()).sort((a, b) => b[1] - a[1])
  }, [kgNodes])
  const kgVisibleNodes = useMemo(
    () => kgKindFilter === 'all' ? kgNodes : kgNodes.filter((node) => node.kind === kgKindFilter),
    [kgNodes, kgKindFilter],
  )
  const kgVisibleIds = useMemo(() => new Set(kgVisibleNodes.map((node) => node.id)), [kgVisibleNodes])
  const kgVisibleEdges = useMemo(
    () => kgEdges.filter((edge) => kgVisibleIds.has(edge.src_id) && kgVisibleIds.has(edge.dst_id)),
    [kgEdges, kgVisibleIds],
  )
  const kgLayout = useMemo(
    () => layoutKg(kgVisibleNodes, kgVisibleEdges),
    [kgVisibleNodes, kgVisibleEdges],
  )
  const kgPositions = kgLayout.positions
  const kgLabels = useMemo(() => {
    const placed: Array<{ x1: number; y1: number; x2: number; y2: number }> = []
    const result: Record<string, { x: number; y: number; anchor: 'start' | 'end' | 'middle' }> = {}
    const entries = Object.values(kgPositions) as any[]
    for (const point of entries) {
      const radius = 8 + Math.min(7, Number(point.degree || 0) * 1.4)
      placed.push({ x1: point.x - radius, y1: point.y - radius, x2: point.x + radius, y2: point.y + radius })
    }
    const ordered = [...entries].sort((a, b) => Number(b.degree || 0) - Number(a.degree || 0))
    for (const point of ordered) {
      const text = kgNodeLabel(point.node)
      const width = Math.min(150, Math.max(30, text.length * 11 + 8))
      const radius = 8 + Math.min(7, Number(point.degree || 0) * 1.4)
      const candidates = [
        { x: point.x + radius + 6, y: point.y + 4, anchor: 'start' as const },
        { x: point.x - radius - 6 - width, y: point.y + 4, anchor: 'start' as const },
        { x: point.x - width / 2, y: point.y - radius - 9, anchor: 'start' as const },
        { x: point.x - width / 2, y: point.y + radius + 17, anchor: 'start' as const },
      ]
      let chosen = candidates[0]
      for (const candidate of candidates) {
        const box = { x1: candidate.x, y1: candidate.y - 11, x2: candidate.x + width, y2: candidate.y + 5 }
        if (box.x1 < 4 || box.x2 > kgLayout.width - 4) continue
        if (placed.some((other) => box.x1 < other.x2 + 3 && box.x2 + 3 > other.x1 && box.y1 < other.y2 + 2 && box.y2 + 2 > other.y1)) continue
        chosen = candidate
        break
      }
      result[point.node.id] = chosen
      placed.push({ x1: chosen.x, y1: chosen.y - 11, x2: chosen.x + width, y2: chosen.y + 5 })
    }
    return result
  }, [kgPositions, kgLayout.width])
  const kgActiveId = kgHover || kgSelected?.id || ''
  const kgNeighborIds = useMemo(() => {
    const ids = new Set<string>()
    if (!kgActiveId) return ids
    ids.add(kgActiveId)
    for (const edge of kgVisibleEdges) {
      if (edge.src_id === kgActiveId) ids.add(edge.dst_id)
      if (edge.dst_id === kgActiveId) ids.add(edge.src_id)
    }
    return ids
  }, [kgActiveId, kgVisibleEdges])
  const kgZoom = (factor: number) => {
    setKgView((view) => {
      const width = Math.max(320, Math.min(2200, view.w * factor))
      const height = width * (620 / 920)
      return { x: view.x + (view.w - width) / 2, y: view.y + (view.h - height) / 2, w: width, h: height }
    })
  }
  const kgSelectedNeighbors = useMemo(() => {
    if (!kgSelected?.id) return []
    const rows = []
    for (const edge of kgEdges) {
      const other = edge.src_id === kgSelected.id ? edge.dst_id : edge.dst_id === kgSelected.id ? edge.src_id : ''
      if (!other) continue
      const node = kgNodeMap[other]
      if (node) rows.push({ node, relation: String(edge.relation || '') })
    }
    return rows.slice(0, 5)
  }, [kgSelected, kgEdges, kgNodeMap])

  const workspaceTabs = [
    ['工作台', '对话'],
    ['项目', '项目'],
    ['录屏', '录屏'],
    ['会议纪要', '纪要'],
    ['证据包', '证据包'],
    ['承诺账本', '账本'],
    ['找回 30 秒', '找回'],
    ['项目架构', '架构'],
  ]
  const moduleDescription: Record<string, string> = {
    '会议纪要': '查看每场会议的纪要、证据片段和原话。',
    '会议档案': '按项目归档的会议与证据。',
    '项目': '选择项目文件夹，读取材料并让 agent 理解上下文。',
    '录屏': '当前屏幕采集预览、帧数与缓冲状态；只保存命中的回溯片段。',
    '证据包': '可校验、可回看的会议证据单元。',
    '承诺账本': '谁承诺了什么，后来谁改了口。',
    '找回 30 秒': '用自然语言定位会议里的具体片段和原话。',
    '项目架构': 'AI 归纳的模块、负责人、手动增补与依赖。',
  }

  return (
    <main className={`app-shell ${currentProject ? 'with-tabs' : ''}`}>
      <aside className="sidebar">
        <div className="brand-row"><div className="brand-mark"><Sparkles size={15} /></div><span>会议 agent</span><button className="workspace-switcher" onClick={() => openSettings('常规')}>个人空间 <ChevronDown size={13} /></button></div>
        <button className="new-meeting" onClick={() => void chooseProject()}><Plus size={16} /> 新建会议 <span>⌘ N</span></button>
        <div className="sidebar-actions">
          <button className="sidebar-action" onClick={() => openSettings('会议偏好')}><Sparkles size={15} /> 自动纪要</button>
        </div>
        <div className="section-label"><span>项目</span><button aria-label="添加项目" onClick={() => void chooseProject()}><Plus size={14} /></button></div>
        <div className="project-list">
          {projectGroups.length ? projectGroups.map((group) => {
            const key = group.project || '__unassigned__'
            const expanded = expandedProjects[key] ?? Boolean(group.project && (group.project === currentProject || group.project === defaultOpenKey))
            const items = group.sessions || []
            const visibleItems = showAllSessions[key] ? items : items.slice(0, 4)
            return (
              <div className="project-group" key={key}>
                <button
                  className={`project-row ${group.project && group.project === currentProject ? 'active-project' : ''}`}
                  aria-expanded={expanded}
                  onClick={() => {
                    if (group.project) void selectProject(group.project)
                    setExpandedProjects((previous) => ({ ...previous, [key]: !expanded }))
                  }}
                >
                  {expanded ? <FolderOpen size={15} className="folder-icon" /> : <Folder size={15} className="folder-icon" />}
                  <span className="project-name">{group.name}</span>
                  {items.length ? <span className="project-count">{items.length}</span> : null}
                  <ChevronRight size={13} className={`project-chevron ${expanded ? 'open' : ''}`} />
                </button>
                {expanded ? (
                  <div className="session-list">
                    {visibleItems.length ? visibleItems.map((session: any) => (
                      <button
                        className={`session-row ${selectedSession?.sid === session.sid ? 'selected' : ''}`}
                        key={session.sid}
                        onClick={() => void openSession({ ...session, project: group.project })}
                      >
                        <span className={`session-dot ${session.minutes ? 'done' : ''}`} />
                        <span className="session-title">{sessionLabel(session)}</span>
                        <span className="session-time">{relativeTime(session.mtime)}</span>
                      </button>
                    )) : <div className="session-empty">暂无会议</div>}
                    {items.length > 4 ? (
                      <button className="session-more" onClick={() => setShowAllSessions((previous) => ({ ...previous, [key]: !showAllSessions[key] }))}>
                        {showAllSessions[key] ? '收起' : `显示更多（${items.length - 4}）`}
                      </button>
                    ) : null}
                  </div>
                ) : null}
              </div>
            )
          }) : <div className="project-empty">还没有项目。点击 + 添加项目文件夹。</div>}
        </div>
        <div className="sidebar-bottom">
          <button className="nav-item" onClick={() => setShowSettings(true)}><Settings size={16} /> 设置 <span className="shortcut">⌘ ,</span></button>
          <button className="nav-item" onClick={() => void openHelp()}><CircleHelp size={16} /> 帮助中心</button>
          <div className="sidebar-user">
            <div className="avatar">D</div>
            <div className="sidebar-user-name"><div className="profile-name">Daniel Xiong</div><div className="profile-sub">Free workspace</div></div>
            <button className="icon-button" aria-label="账户设置" onClick={() => openSettings('我的账户')}><MoreHorizontal size={15} /></button>
          </div>
        </div>
      </aside>

      <section className="chat-panel">
        <header className="topbar"><div className="breadcrumb"><span className="live-dot" />{meetingTitle} <ChevronRight size={14} /><span className="muted-text">{activeNav}</span></div><div className="top-actions"><IconButton label="搜索" onClick={focusComposer}><Search size={17} /></IconButton><IconButton label="通知" onClick={() => openSettings('会议偏好')}><Bell size={17} /></IconButton><button className="top-avatar" aria-label="账户设置" onClick={() => openSettings('我的账户')}>D</button></div></header>
        {currentProject ? (
          <div className="workspace-tabs" role="tablist" aria-label="项目工具">
            {workspaceTabs.map(([value, label]) => (
              <button key={value} role="tab" aria-selected={activeNav === value} className={activeNav === value ? 'selected' : ''} onClick={() => setActiveNav(value)}>{label}</button>
            ))}
          </div>
        ) : null}
        <div className="chat-content" style={{ display: isChatView ? 'flex' : 'none' }}>
          <div className="meeting-header"><div><div className="eyebrow">{liveClock(now)}</div><h1>{meetingTitle}</h1><p>和 agent 一起，让每次会议都有结果。</p></div><button className="record-button" onClick={() => void toggleRecording()} disabled={busy || finishing}><span className="record-dot" /> {finishing ? '正在结束…' : recording ? '结束录音' : '开始录音'}</button></div>
          <div className="context-chips">
            <span title="来自设置 → 音频采集源">
              <Mic size={14} /> {config.audio_source === 'mic' ? '仅麦克风' : config.audio_source === 'loopback' ? '仅系统声音' : '系统声音 + 麦克风'}
            </span>
            <span title={recording || finishing ? '按本场已识别到的说话人统计' : '开始录音后显示识别到的发言人'}>
              <Users size={14} /> {recording || finishing ? (speakerCount ? `${speakerCount} 位发言人` : '正在识别发言人') : (selectedSession ? `${selectedSession.lines || 0} 句记录` : '未开始录音')}
            </span>
            <span title={autoProject.folder === currentProject ? '当前项目的关键认知进度（地图≠全部精读）' : '本地优先'}>
              <Sparkles size={14} /> {autoProject.folder === currentProject && autoProject.status === 'running' ? '同步工作区…' : autoProject.folder === currentProject && autoProject.status === 'oriented' ? '项目认知已建立' : autoProject.folder === currentProject && autoProject.status === 'paused_budget' ? '探索已暂停' : '本地优先'}
            </span>
          </div>
          {!apiConfigured ? (
            <div className="api-banner">
              <div><b>接入模型 API 后 agent 才能干活</b><span>录音和本地转写可用；会议对话、纪要、项目理解需要模型 API。</span></div>
              <button onClick={() => openSettings('模型服务')}>去接入</button>
            </div>
          ) : null}
          {autoProject.status !== 'idle' && autoProject.folder === currentProject ? (
            <AutoProjectCard key={currentProject} state={autoProject} modelInfo={modelInfo} onVerifyModel={() => { void verifyModelAndContinue() }} onSettings={() => openSettings('模型服务')} onRefresh={() => { void runProjectAction('sync') }} />
          ) : null}
          <div className="conversation" aria-live="polite" ref={conversationRef}>
            {messages.map((item, i) => {
              return (
                <div key={item.id || i} className={`message-row ${item.role}`}>
                  <div className={`message-avatar ${item.role}`}>{item.role === 'assistant' ? <Sparkles size={15} /> : 'D'}</div>
                  <div className="message-body">
                    <div className="message-name">{item.role === 'assistant' ? '会议 agent' : '你'} <span>{messageTime(item.ts, now)}</span></div>
                    {item.thinking ? <ThinkingCard startedAt={item.startedAt || Date.now()} events={item.liveEvents || []} answer={item.liveAnswer || ''} thought={item.liveThought || ''} mode={item.thinkingMode || 'agent'} onCancel={chatCancelable ? () => void cancelChat() : undefined} /> : null}
                    {item.role === 'assistant' && !item.thinking ? <div className="activity-row activity-muted"><span className="activity-meta">{{ answered: '已回答', partial: '部分回答', no_evidence: '缺少证据', failed: '回答失败', awaiting_approval: '等待批准' }[item.answer_status || 'failed']}{!item.answer_status ? ' / 状态未核验' : ''}</span>{item.reason ? <span>{item.reason}</span> : null}</div> : null}
                    {item.errors?.length ? <div className="auto-warn">{item.errors.map((error: any, index: number) => <div key={index}>{typeof error === 'string' ? error : error.message || error.code || JSON.stringify(error)}</div>)}</div> : null}
                    {item.unsupported?.length ? <div className="auto-warn">未支持的结论：{JSON.stringify(item.unsupported)}</div> : null}
                    {item.coverage ? <details className="activity-plan"><summary>回答覆盖范围</summary><pre>{JSON.stringify(item.coverage, null, 2)}</pre></details> : null}
                    {item.text ? (item.role === 'assistant' ? <MarkdownText text={item.text} /> : <div className="message-text">{item.text}</div>) : null}
                    {(item.plan?.length || item.observations?.length) ? <ActivityFeed message={item} /> : null}
                    {i === 0 && <div className="suggestions"><button onClick={() => setMessage('帮我准备今天的会议议程')}>准备会议议程 <ChevronRight size={14} /></button><button onClick={() => setMessage('总结上次会议的行动项')}>总结上次行动项 <ChevronRight size={14} /></button><button onClick={() => setMessage('分析当前项目进度')}>分析项目进度 <ChevronRight size={14} /></button></div>}
                    {item.actions && item.actions.length > 0 && <div className="suggestions">{item.actions.map((action) => <button key={action.id} disabled={Boolean(action.status && !['pending', 'awaiting_approval'].includes(action.status))} onClick={() => void executeAction(action)}>{action.label || '确认操作'}{actionStatusLabel[action.status || ''] || ''} <ChevronRight size={14} /></button>)}</div>}
                  </div>
                </div>
              )
            })}
          </div>
          <div className="composer-wrap"><div className="composer"><textarea ref={composerRef} value={message} onChange={(e) => setMessage(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing && e.keyCode !== 229) { e.preventDefault(); void sendMessage() } }} placeholder={chatMode === 'chat' ? '自由对话…' : '问会议 agent 任何问题...'} rows={2} /><div className="composer-actions"><div><IconButton label="添加附件" onClick={() => void attachMedia()}><Paperclip size={16} /></IconButton><div className="mode-picker">
                    <button className="model-select" aria-haspopup="menu" aria-expanded={modeMenu} onClick={(event) => { event.stopPropagation(); setModeMenu((value) => !value) }}>
                      {chatMode === 'chat' ? <MessageSquare size={14} /> : <Bot size={14} />} {chatMode === 'chat' ? 'Chat' : 'Agent'} <ChevronDown size={13} />
                    </button>
                    {modeMenu ? (
                      <div className="mode-menu" role="menu" onClick={(event) => event.stopPropagation()}>
                        <button role="menuitem" className={chatMode === 'agent' ? 'selected' : ''} onClick={() => { setChatMode('agent'); setModeMenu(false) }}>
                          <Bot size={15} /><span><b>Agent</b><small>检索项目/会议，调用读写文件和命令</small></span>
                        </button>
                        <button role="menuitem" className={chatMode === 'chat' ? 'selected' : ''} onClick={() => { setChatMode('chat'); setModeMenu(false) }}>
                          <MessageSquare size={15} /><span><b>Chat</b><small>自由对话，不检索项目、不调用工具</small></span>
                        </button>
                      </div>
                    ) : null}
                  </div></div>{chatCancelable ? <button className="send-button stop" onClick={() => void cancelChat()} aria-label="停止生成"><span className="stop-square" /></button> : <button className="send-button" onClick={() => void sendMessage()} aria-label="发送"><Send size={16} /></button>}</div></div><div className="composer-hint">按 Enter 发送 · Shift + Enter 换行</div></div>
        </div>
        {!isChatView && (
          <div className="chat-content" style={{ display: 'flex' }}>
            <div className="meeting-header">
              <div><div className="eyebrow">WORKSPACE</div><h1>{activeNav}</h1><p>{moduleDescription[activeNav] || ''}</p></div>
              {activeNav === '项目' ? <button className="record-button" onClick={() => void chooseProject()}><Folder size={14} /> 选择项目文件夹</button> : null}
              {activeNav === '录屏' ? <button className="record-button" onClick={() => void toggleRecording()} disabled={busy || finishing}><span className="record-dot" /> {finishing ? '正在结束…' : recording ? '结束录屏' : '开始录屏'}</button> : null}
            </div>
            <div className="context-chips">
              <span><Folder size={14} /> {currentProject ? projectName(currentProject) : '未选择项目'}</span>
              <span><Users size={14} /> {projects.length} 个项目</span>
              <span><Zap size={14} /> 本地优先</span>
            </div>
            {activeNav === '项目' && autoProject.status !== 'idle' && autoProject.folder === currentProject ? (
              <AutoProjectCard key={currentProject} state={autoProject} modelInfo={modelInfo} onVerifyModel={() => { void verifyModelAndContinue() }} onSettings={() => openSettings('模型服务')} onRefresh={() => { void runProjectAction('sync') }} />
            ) : null}
            <div className="conversation">
              {moduleLoading ? <div className="module-empty">读取中…</div> : null}
              {(activeNav === '会议纪要' || activeNav === '会议档案') && (sessions.length ? sessions.map((session) => (
                <button className="module-row" key={session.sid} onClick={() => void openSession(session)}>
                  <div><b>{sessionLabel(session)}</b><small>{session.sid} · {session.lines || 0} 句 · {session.clips || 0} 片段 · {session.minutes ? '有纪要' : '无纪要'}</small></div>
                  <ChevronRight size={15} />
                </button>
              )) : <div className="module-empty">还没有会议。打开项目并开始会议后，这里会出现场次。</div>)}
              {activeNav === '项目' && (
                <>
                  <button className="module-row" onClick={() => void chooseProject()}><div><b>{currentProject ? projectName(currentProject) : '选择项目文件夹'}</b><small>{currentProject || '点击选择项目，读取材料、文件树和项目上下文'}</small></div><ChevronRight size={15} /></button>
                  {visibleTree(files).map((file, index) => <button className="module-row module-file" key={`${file.name}-${index}`} onClick={() => file.type === 'file' ? void openFile(file) : undefined}><div><b>{file.name}</b><small>{file.type === 'folder' ? '文件夹' : [file.kindLabel || file.suffix?.toUpperCase() || '文件', file.size ? formatBytes(file.size) : ''].filter(Boolean).join(' · ')}</small></div>{file.type === 'file' ? <ChevronRight size={15} /> : <Folder size={15} />}</button>)}
                  <div className="module-toolbar"><button className="module-button" onClick={() => void runProjectAction('sync')}>重新调查工作区</button></div>
                  <div className="module-empty">工作区由 Agent 自动维护：打开项目、会议结束和文件变更后会增量同步文件清单、术语、事实与会议记忆。</div>
                  {projectAction ? <div className="module-empty">{projectAction}</div> : null}
                </>
              )}
              {activeNav === '录屏' && (
                <>
                  <div className="screen-live">
                    {screenState.preview
                      ? <img src={screenState.preview} alt="当前录制屏幕预览" />
                      : <div className="screen-placeholder">开始会议后显示当前屏幕预览</div>}
                  </div>
                  <div className="screen-meta">
                    <span>{screenPhase === 'ready' ? '录屏中' : screenPhase === 'loading' ? '正在初始化…' : screenPhase === 'stopping' ? '正在收尾…' : '采集未运行'}</span>
                    {screenState.backend ? <span>{screenState.backend} · {screenState.fps_target || 5} fps</span> : null}
                    {screenState.frame_count != null ? <span>{screenState.frame_count} 帧 · {((Number(screenState.buffer_bytes) || 0) / 1048576).toFixed(1)} MB</span> : null}
                    {screenState.last_success_ts ? <span>更新于 {relativeTime(screenState.last_success_ts)}前</span> : null}
                  </div>
                  {screenState.error ? <div className="module-empty">录屏异常：{screenState.error}</div> : null}
                  <div className="module-empty">只保存命中片段的回溯画面；未命中时随会议结束丢弃。</div>
                </>
              )}
              {activeNav === '找回 30 秒' && (
                <>
                  <div className="module-toolbar">
                    <input className="module-input" value={momentQuery} onChange={(event) => setMomentQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); void findMoment() } }} placeholder="例如：预算三十万的那一段" />
                    <button className="module-button" onClick={() => void findMoment()}>找回</button>
                  </div>
                  {momentResults.length ? momentResults.map((segment: any, index: number) => (
                    <button className="module-row" key={`${segment.session_id}-${segment.start}-${index}`} onClick={() => { setNotes(segment.text || ''); setRightTab('notes') }}>
                      <div><b>{segment.speaker || '会议片段'} · {Math.floor(Number(segment.start || 0))}s</b><small>{segment.text || ''}</small></div>
                      <MessageSquare size={15} />
                    </button>
                  )) : <div className="module-empty">输入一个问题，agent 会回到会议原话和具体时间戳。</div>}
                </>
              )}
              {activeNav === '证据包' && (evidencePacks.length ? evidencePacks.map((pack) => (
                <div key={pack.name}>
                  <button className="module-row" onClick={() => void verifyPack(pack)}>
                    <div><b>{pack.name}</b><small>{pack.session_id || '会议'} · {pack.files || 0} 个文件 · {pack.created_at || ''}</small></div>
                    <PackageCheck size={15} />
                  </button>
                  {selectedPack?.name === pack.name && (
                    <div className="module-empty">
                      {!verifyResult ? '正在校验 SHA256…' : verifyResult.ok
                        ? `SHA256 全部通过 · ${verifyResult.files || 0} 个文件`
                        : `校验失败：${(verifyResult.failed || []).map((item: any) => item.path).join('、') || verifyResult.error || '未知错误'}`}
                      {verifyResult?.zip ? <div style={{ marginTop: 8 }}><button className="module-button" onClick={() => void call('open_path', verifyResult.zip)}>打开 ZIP</button></div> : null}
                    </div>
                  )}
                </div>
              )) : <div className="module-empty">还没有证据包。在对话里说“生成证据包”后，这里会出现可校验 ZIP。<button className="empty-cta" onClick={() => openChatWith('生成证据包')}>去对话生成</button></div>)}
              {activeNav === '承诺账本' && (ledgerRows.length ? ledgerRows.map((row, index) => (
                <div className="module-row" key={`${row.name || row.title}-${index}`}>
                  <div><b>{row.title}</b><small>{[row.owner, row.due, row.status].filter(Boolean).join(' · ')}{row.changes && row.changes.length ? ` · ${row.changes.map((c: any) => `${c.field}: ${c.from} → ${c.to}`).join(' / ')}` : ''}</small></div>
                  <ListChecks size={15} />
                </div>
              )) : <div className="module-empty">还没有可追踪的承诺。开一场会，或在聊天里说“记住：周五交付第一版”。<button className="empty-cta" onClick={() => openChatWith('记住：周五交付第一版')}>去对话记录</button></div>)}
              {activeNav === '项目架构' && <ArchitecturePanel project={currentProject} />}
              {activeNav === '__legacy_kg__' && (
                <>
                  <div className="kg-stats">
                    <b>{Number(kgStats.nodes ?? kgNodes.length) || 0} 节点</b>
                    <b>{Number(kgStats.edges ?? kgEdges.length) || 0} 边</b>
                    <span>{kgBusy ? '同步中…' : '可视化 · 可编辑'}</span>
                    <button className="module-button" onClick={() => void loadKg()}>刷新</button>
                  </div>
                  <div className="kg-toolbar">
                    <div className="kg-filters">
                      <button className={kgKindFilter === 'all' ? 'selected' : ''} onClick={() => setKgKindFilter('all')}>全部</button>
                      {kgPresentKinds.map(([kind, count]) => (
                        <button key={kind} className={kgKindFilter === kind ? 'selected' : ''} onClick={() => setKgKindFilter(kgKindFilter === kind ? 'all' : kind)}>
                          <span className="kg-kind-dot" style={{ background: kgKindColors[kind] || '#9a9d9f' }} />{kind}<small>{count}</small>
                        </button>
                      ))}
                    </div>
                    <div className="kg-zoom">
                      <button onClick={() => kgZoom(1.22)} aria-label="缩小">−</button>
                      <button onClick={() => setKgView({ x: 0, y: 0, w: 920, h: 620 })}>适应</button>
                      <button onClick={() => kgZoom(0.82)} aria-label="放大">＋</button>
                    </div>
                  </div>
                  {kgVisibleNodes.length ? (
                    <div className="kg-graph-wrap">
                      <svg
                        className="kg-canvas"
                        viewBox={`${kgView.x} ${kgView.y} ${kgView.w} ${kgView.h}`}
                        role="img"
                        aria-label="项目知识图谱"
                        onWheel={(event) => { event.preventDefault(); kgZoom(event.deltaY > 0 ? 1.1 : 0.9) }}
                      >
                        <defs>
                          <marker id="kg-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
                            <path d="M 0 0 L 10 5 L 0 10 z" fill="#65705f" />
                          </marker>
                        </defs>
                        {kgVisibleEdges.map((edge: any) => {
                          const a = kgPositions[edge.src_id]
                          const b = kgPositions[edge.dst_id]
                          if (!a || !b) return null
                          const hot = kgActiveId && (edge.src_id === kgActiveId || edge.dst_id === kgActiveId)
                          return <line key={edge.id} x1={a.x} y1={a.y} x2={b.x} y2={b.y} markerEnd="url(#kg-arrow)" className={`kg-edge ${hot ? 'hot' : ''} ${kgActiveId && !hot ? 'dim' : ''}`}><title>{String(kgNodeMap[edge.src_id]?.name || '')} → {String(edge.relation || '').replaceAll('_', ' ')} → {String(kgNodeMap[edge.dst_id]?.name || '')}</title></line>
                        })}
                        {Object.values(kgPositions).map(({ x, y, node, degree }: any) => {
                          const active = node.id === kgSelected?.id
                          const dim = kgActiveId && !kgNeighborIds.has(node.id)
                          const radius = 8 + Math.min(7, Number(degree || 0) * 1.4)
                          const label = kgLabels[node.id] || { x: x + radius + 6, y: y + 4, anchor: 'start' as const }
                          return (
                            <g
                              key={node.id}
                              className={`kg-node ${active ? 'selected' : ''} ${dim ? 'dim' : ''}`}
                              onClick={(event) => { event.stopPropagation(); selectKgNode(node) }}
                              onMouseEnter={() => setKgHover(node.id)}
                              onMouseLeave={() => setKgHover('')}
                            >
                              <circle cx={x} cy={y} r={radius} fill={kgKindColors[node.kind] || '#9a9d9f'} />
                              <text x={label.x} y={label.y} textAnchor={label.anchor} className="kg-node-label">{kgNodeLabel(node)}</text>
                              <title>{String(node.name || '')} · {String(node.kind || '')}</title>
                            </g>
                          )
                        })}
                      </svg>
                      {kgSelected ? (
                        <div className="kg-selection">
                          <div className="kg-selection-head">
                            <span className="kg-kind-dot" style={{ background: kgKindColors[kgSelected.kind] || '#9a9d9f' }} />
                            <b>{String(kgSelected.name || '')}</b>
                            <small>{String(kgSelected.kind || '')}</small>
                          </div>
                          {kgSelected.description ? <p>{String(kgSelected.description).slice(0, 180)}</p> : null}
                          {kgSelectedNeighbors.length ? <div className="kg-selection-links">{kgSelectedNeighbors.map(({ node, relation }: any) => <span key={`${node.id}-${relation}`}>{String(relation).replaceAll('_', ' ')} · {String(node.name || '')}</span>)}</div> : null}
                          <div className="kg-selection-actions">
                            <button onClick={() => document.querySelector('.kg-editor-grid')?.scrollIntoView({ behavior: 'smooth', block: 'center' })}>编辑</button>
                            <button onClick={() => void deleteKgNode(kgSelected)}>删除</button>
                          </div>
                        </div>
                      ) : null}
                      <div className="kg-graph-hint">点击节点编辑 · 箭头表示关系方向 · 上方可按类型筛选 · 滚轮缩放</div>
                    </div>
                  ) : <div className="module-empty">当前筛选下没有实体。选择“全部”或换一个类型。</div>}
                  <div className="kg-editor-grid">
                  <div className="kg-form">
                    <div className="kg-form-title">{kgSelected ? `编辑实体：${kgSelected.name}` : '新增实体'}</div>
                    <div className="module-toolbar">
                      <select className="module-input" value={kgNewNode.kind} onChange={(event) => setKgNewNode({ ...kgNewNode, kind: event.target.value })}>{kgKinds.map((kind) => <option key={kind} value={kind}>{kind}</option>)}</select>
                      <input className="module-input" value={kgNewNode.name} onChange={(event) => setKgNewNode({ ...kgNewNode, name: event.target.value })} placeholder="名称，例如：周五交付第一版 / 张三" />
                    </div>
                    <div className="module-toolbar">
                      <input className="module-input" value={kgNewNode.description} onChange={(event) => setKgNewNode({ ...kgNewNode, description: event.target.value })} placeholder="描述 / 依据（可选）" />
                      <button className="module-button" onClick={() => void saveKgNode()} disabled={kgBusy || !kgNewNode.name.trim()}>{kgSelected ? '保存修改' : '新增实体'}</button>
                      {kgSelected ? <button className="module-button" onClick={() => { setKgSelected(null); setKgNewNode({ kind: 'Action', name: '', description: '' }) }}>取消</button> : null}
                    </div>
                  </div>
                  <div className="kg-form">
                    <div className="kg-form-title">新增关系</div>
                    <div className="module-toolbar">
                      <select className="module-input" value={kgNewEdge.src} onChange={(event) => setKgNewEdge({ ...kgNewEdge, src: event.target.value })}><option value="">起点实体</option>{kgNodes.map((node: any) => <option key={node.id} value={node.id}>{String(node.kind || '')}: {String(node.name || '')}</option>)}</select>
                      <select className="module-input" value={kgNewEdge.relation} onChange={(event) => setKgNewEdge({ ...kgNewEdge, relation: event.target.value })}>{kgRelations.map((relation) => <option key={relation} value={relation}>{relation}</option>)}</select>
                      <select className="module-input" value={kgNewEdge.dst} onChange={(event) => setKgNewEdge({ ...kgNewEdge, dst: event.target.value })}><option value="">终点实体</option>{kgNodes.map((node: any) => <option key={node.id} value={node.id}>{String(node.kind || '')}: {String(node.name || '')}</option>)}</select>
                    </div>
                    <div className="module-toolbar">
                      <input className="module-input" value={kgNewEdge.valid_from} onChange={(event) => setKgNewEdge({ ...kgNewEdge, valid_from: event.target.value })} placeholder="有效期/日期（可选，如 2026-11-08）" />
                      <button className="module-button" onClick={() => void saveKgEdge()} disabled={kgBusy || !kgNewEdge.src || !kgNewEdge.dst}>新增关系</button>
                    </div>
                  </div>
                  </div>
                  {kgMessage ? <div className="module-empty">{kgMessage}</div> : null}
                  <div className="kg-grid">
                  {kgNodes.length ? <div className="kg-list">
                    <div className="kg-form-title">实体（{kgNodes.length}）</div>
                    {kgNodes.slice(0, 80).map((node: any) => (
                      <div className="kg-row" key={node.id}>
                        <span className="kg-kind-dot" style={{ background: kgKindColors[node.kind] || '#9a9d9f' }} />
                        <div><b>{String(node.name || '')}</b><small>{String(node.kind || '')}{node.description ? ` · ${String(node.description)}` : ''}</small></div>
                        <button className="module-button" onClick={() => selectKgNode(node)}>编辑</button>
                        <button className="module-button" onClick={() => void deleteKgNode(node)}>删除</button>
                      </div>
                    ))}
                  </div> : null}
                  {kgEdges.length ? <div className="kg-list">
                    <div className="kg-form-title">关系（{kgEdges.length}）</div>
                    {kgEdges.slice(0, 100).map((edge: any) => (
                      <div className="kg-row" key={edge.id}>
                        <div><b>{String(kgNodeMap[edge.src_id]?.name || edge.src_id)} → {String(edge.relation || '')} → {String(kgNodeMap[edge.dst_id]?.name || edge.dst_id)}</b><small>{edge.valid_from || ''}{edge.quote ? ` · ${String(edge.quote).slice(0, 120)}` : ''}</small></div>
                        <button className="module-button" onClick={() => void deleteKgEdge(edge)}>删除</button>
                      </div>
                    ))}
                  </div> : null}
                  </div>
                  <div className="module-toolbar">
                    <input className="module-input" value={kgQuery} onChange={(event) => setKgQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); void searchKg() } }} placeholder="按名称查邻居，例如：张三 / 周五交付" />
                    <button className="module-button" onClick={() => void searchKg()}>查邻居</button>
                  </div>
                </>
              )}            </div>
          </div>
        )}
      </section>

      <aside className="inspector" style={{ display: showInspector ? undefined : 'none' }}>
        <div className="inspector-header">
          <div className="inspector-tabs">
            <button className={rightTab === 'files' ? 'selected' : ''} onClick={() => setRightTab('files')}><Folder size={15} /> 文件</button>
            <button className={rightTab === 'notes' ? 'selected' : ''} onClick={() => setRightTab('notes')}><FileText size={15} /> 纪要</button>
          </div>
          <IconButton label="关闭面板" onClick={() => setShowInspector(false)}><X size={16} /></IconButton>
        </div>
        {rightTab === 'files' ? (
          <>
            <div className="tree-header"><span>EXPLORER</span><div><IconButton label="刷新文件" onClick={() => void refreshFiles()}><RefreshCw size={14} /></IconButton><IconButton label="打开项目" onClick={() => setActiveNav('项目')}><MoreHorizontal size={15} /></IconButton></div></div>
            <div className="file-tree">
              {visibleTree(files).map((file, index) => (
                <button
                  key={`${file.name}-${index}`}
                  className={`tree-item ${file.active ? 'tree-active' : ''}`}
                  style={{ paddingLeft: `${14 + (file.indent || 0) * 16}px` }}
                  onClick={() => {
                    if (file.type === 'folder') {
                      setFiles((previous) => previous.map((item, i) => i === index ? { ...item, open: !item.open } : item))
                    } else {
                      void openFile(file)
                    }
                  }}
                >
                  {file.type === 'folder' ? (file.open ? <FolderOpen size={15} className="folder-icon" /> : <Folder size={15} className="folder-icon" />) : fileIcon(file)}
                  <span>{file.name}</span>
                  {file.active && <span className="unsaved-dot" />}
                </button>
              ))}
            </div>
            {filePreview.name ? (
              <div className="file-preview">
                <div className="preview-label">当前文件</div>
                <div className="code-window">
                  {filePreview.lines.map((line, index) => (
                    <div className="code-line" key={index}><span className="line-num">{index + 1}</span>{line}</div>
                  ))}
                </div>
              </div>
            ) : <div className="module-empty">从上方文件树选择文件查看。</div>}
          </>
        ) : (
          <div className="notes-view" ref={notesRef}>
            <div className="notes-title-row">
              <div className="notes-title">{meetingTitle} · 纪要</div>
              {selectedSession ? <button className="module-button" disabled={busy} onClick={() => void generateMinutes()}>{busy ? '生成中…' : '用 API 生成'}</button> : null}
            </div>
            {Object.values(liveCaptions).length ? <div className="live-captions"><b>实时字幕</b>{Object.values(liveCaptions).slice(-4).map((text, index) => <p key={`${index}-${text}`}>{text}</p>)}</div> : null}
            <div className="note-card"><span className="note-icon"><Sparkles size={14} /></span><div><b>{notes ? '最近纪要' : '待生成会议纪要'}</b><p>{notes || '开始录音后，agent 会自动整理重点、决策和行动项。'}</p></div></div>
            <div className="note-section"><span>会议模板</span><button onClick={() => openSettings('会议偏好')}>产品评审 <ChevronDown size={13} /></button></div>
            <div className="note-section"><span>输出格式</span><button onClick={() => openSettings('会议偏好')}>详细版 <ChevronDown size={13} /></button></div>
          </div>
        )}
      </aside>

      {viewer && (
        <div className="viewer-backdrop" onClick={() => setViewer(null)}>
          <div className="viewer-card" onClick={(event) => event.stopPropagation()}>
            <div className="viewer-head">
              <div className="viewer-title"><b>{viewer.name}</b><small>{viewer.kind_label || viewer.kind || ''}{viewer.mismatch ? ' · 后缀与内容不符' : ''}{viewer.size ? ` · ${Math.round(Number(viewer.size) / 1024)} KB` : ''}</small></div>
              <div className="viewer-actions">
                <button className="module-button" onClick={() => viewer.rel && viewer.project && void call('open_project_file', viewer.project, viewer.rel)}>用系统应用打开</button>
                <button className="icon-button" aria-label="关闭查看器" onClick={() => setViewer(null)}><X size={18} /></button>
              </div>
            </div>
            <div className="viewer-body">
              {viewer.kind === 'pdf' && (viewer.url || viewer.data_url) ? <iframe className="viewer-pdf" src={viewer.url || viewer.data_url} title={viewer.name} /> : null}
              {viewer.kind === 'image' && viewer.data_url ? <img src={viewer.data_url} alt={viewer.name} /> : null}
              {viewer.kind === 'video' && viewer.data_url ? <video src={viewer.data_url} controls /> : null}
              {viewer.kind === 'audio' && viewer.data_url ? <audio src={viewer.data_url} controls /> : null}
              {(viewer.kind === 'zip' || viewer.kind === 'xlsx') && Array.isArray(viewer.entries) ? (
                <div className="viewer-entries">{viewer.entries.slice(0, 200).map((entry: any, index: number) => <div className="module-row" key={`${entry.name}-${index}`}><div><b>{entry.name}</b><small>{entry.size || 0} bytes</small></div></div>)}</div>
              ) : null}
              {viewer.text ? <pre className="viewer-pre">{viewer.text}</pre> : null}
              {!viewer.text && !['pdf', 'image', 'video', 'audio', 'zip', 'xlsx'].includes(viewer.kind) ? <div className="module-empty">{viewer.meta || '该文件没有可内嵌预览的文本；请用系统应用打开。'}</div> : null}
              {viewer.too_large ? <div className="module-empty">文件较大，未内嵌预览；请用系统应用打开。</div> : null}
            </div>
          </div>
        </div>
      )}

      {showSettings && (
        <div className="modal-backdrop" onClick={() => setShowSettings(false)}>
          <div className="settings-modal" onClick={(event) => event.stopPropagation()}>
            <div className="modal-title">
              <div><div className="eyebrow">WORKSPACE</div><h2>设置</h2></div>
              <IconButton label="关闭设置" onClick={() => setShowSettings(false)}><X size={18} /></IconButton>
            </div>
            <div className="settings-layout">
              <div className="settings-menu">
                {['常规', '我的账户', '会议偏好', '模型服务', '团队与权限', '集成', '账单'].map((tab) => (
                  <button key={tab} className={settingsTab === tab ? 'settings-selected' : ''} onClick={() => setSettingsTab(tab)}>{tab}</button>
                ))}
              </div>
              <div className="settings-body">
                <h3>{settingsTab}设置</h3>
                <p className="settings-description">管理你的工作区和会议 agent 偏好。</p>
                {settingsTab === '常规' && (
                  <>
                    <label>引擎 Python<input value={config.engine_python || ''} onChange={(event) => setConfig({ ...config, engine_python: event.target.value })} placeholder="D:\miniconda3\envs\meetingvar\python.exe" /></label>
                    <label>仓库目录<input value={config.repo_dir || ''} onChange={(event) => setConfig({ ...config, repo_dir: event.target.value })} placeholder="C:\Users\Lenovo\projects\meetingVAR" /></label>
                    <label>设备<select value={config.device || 'auto'} onChange={(event) => setConfig({ ...config, device: event.target.value })}><option value="auto">auto</option><option value="cuda">cuda</option><option value="cpu">cpu</option></select></label>
                    <label>音频采集源<select value={config.audio_source || 'both'} onChange={(event) => setConfig({ ...config, audio_source: event.target.value })}><option value="both">系统声音 + 麦克风</option><option value="loopback">仅系统声音</option><option value="mic">仅麦克风</option></select></label>
                    <label>保存方式<select value={config.interest_mode || 'manual'} onChange={(event) => setConfig({ ...config, interest_mode: event.target.value })}><option value="api">API 判断 + 手动 VAR</option><option value="manual">仅手动 VAR</option></select></label>
                  </>
                )}
                {settingsTab === '会议偏好' && (
                  <>
                    <label>识别引擎<select value={config.asr_engine || 'local'} onChange={(event) => setConfig({ ...config, asr_engine: event.target.value })}><option value="local">本地 FunASR</option><option value="xfyun_rtasr">讯飞 RTASR</option><option value="xfyun">讯飞 IAT</option></select></label>
                    <div className="setting-toggle"><div><b>自动生成会议纪要</b><span>当前版本请在会后用“用 API 生成”或对话生成；自动流程即将支持</span></div><span className="setting-soon">即将支持</span></div>
                    <div className="setting-toggle"><div><b>桌面通知</b><span>会议开始前提醒我</span></div><span className="setting-soon">即将支持</span></div>
                  </>
                )}
                {settingsTab === '模型服务' && (
                  <>
                    <p className="settings-description">接入后，会议对话、纪要、项目理解和知识图谱会调用这里配置的模型。录音和本地转写不需要 API。</p>
                    <label>快速配置<select value={apiPreset} onChange={(event) => {
                      const preset = apiPresets.find((item) => item.id === event.target.value)
                      setApiPreset(event.target.value)
                      if (preset) {
                        setConfig({ ...config, llm_provider: 'custom', llm_base_url: preset.base, llm_model: preset.model })
                        setLlmTest({ state: 'idle', message: '' })
                      }
                    }}>{apiPresets.map((preset) => <option key={preset.id} value={preset.id}>{preset.label}</option>)}</select></label>
                    <label>模型厂商<select value={config.llm_provider || 'local'} onChange={(event) => setConfig({ ...config, llm_provider: event.target.value })}><option value="local">本地 llama.cpp</option><option value="custom">自定义 OpenAI 兼容 API</option></select></label>
                    <label>Base URL<input value={config.llm_base_url || ''} onChange={(event) => setConfig({ ...config, llm_base_url: event.target.value })} placeholder="https://api.deepseek.com/v1" /></label>
                    <label>模型名<input value={config.llm_model || ''} onChange={(event) => setConfig({ ...config, llm_model: event.target.value })} placeholder="deepseek-chat" /></label>
                    <label>API Key<input type="password" value={config.llm_api_key || ''} onChange={(event) => setConfig({ ...config, llm_api_key: event.target.value })} placeholder="只存本机配置，不上传" /></label>
                    <label>推理强度<select value={config.llm_reasoning_effort || ''} onChange={(event) => setConfig({ ...config, llm_reasoning_effort: event.target.value })}><option value="">默认</option><option value="none">关闭</option><option value="low">低</option><option value="medium">中</option><option value="high">高</option></select></label>
                    <div className="module-toolbar">
                      <button className="module-button" onClick={() => void testLLMConnection()} disabled={llmTest.state === 'testing'}>{llmTest.state === 'testing' ? '正在测试…' : '测试连接'}</button>
                      <span className="api-hint">测试会真实调用一次模型生成；Key 只写本机配置。</span>
                    </div>
                    {llmTest.message ? <div className={`api-status ${llmTest.state}`}>{llmTest.message}</div> : null}
                  </>
                )}
                {!['常规', '会议偏好', '模型服务'].includes(settingsTab) && <div className="module-empty">该设置当前版本暂未开放。</div>}
                <button className="save-button" onClick={() => void saveSettings()}>保存更改</button>
              </div>
            </div>
          </div>
        </div>
      )}
    {showHelp && (
        <div className="modal-backdrop" onClick={() => setShowHelp(false)}>
          <div className="settings-modal help-modal" onClick={(event) => event.stopPropagation()}>
            <div className="modal-title">
              <div><div className="eyebrow">MEETINGVAR</div><h2>帮助中心</h2></div>
              <IconButton label="关闭帮助" onClick={() => setShowHelp(false)}><X size={18} /></IconButton>
            </div>
            <div className="help-body">
              <div className="help-grid">
                <section><b>快速开始</b><p>在左侧选择一个项目文件夹，agent 会自动读取材料、建立检索索引和知识图谱；完成后即可在“对话”里提问。</p></section>
                <section><b>对话里可以直接说</b><p><code>列出项目目录</code> · <code>搜索 关键词</code> · <code>读取 README.md</code> · <code>写入 notes.md: 内容</code> · <code>执行命令 git status</code></p></section>
                <section><b>生成文档</b><p>在 Agent 模式直接说：<code>写入 report.docx: # 标题…</code>、<code>生成 slides.pptx: ## 第一页…</code> 或 <code>写入 plan.pdf: …</code>；确认后生成到当前项目文件夹。</p></section>
                <section><b>录音与证据</b><p>“开始录音”同时采集系统声音和麦克风；会议结束后用“纪要 / 证据包 / 账本 / 找回”查看原话和承诺变化。</p></section>
                <section><b>快捷键</b><p><code>Cmd/Ctrl + N</code> 新建会议 · <code>Cmd/Ctrl + ,</code> 设置 · <code>Enter</code> 发送 · <code>Shift + Enter</code> 换行</p></section>
                <section><b>安全边界</b><p>文件操作仅限当前项目文件夹；写入和命令必须点确认；危险命令会被拒绝。API Key 只保存在本机配置。</p></section>
                <section><b>图谱</b><p>图谱页可按类型筛选、缩放、点击节点编辑；自动整理只合并抽取结果，不会改动你手工新增的节点。</p></section>
              </div>
              <button className="save-button" onClick={() => setShowHelp(false)}>知道了</button>
            </div>
          </div>
        </div>
      )}
    </main>
  )
}
