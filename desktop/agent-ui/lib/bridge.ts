'use client'

type AnyApi = Record<string, (...args: any[]) => Promise<any>>

export type ChatRunIdentity = { folder: string; conversation_id: string; run_id: string }
export type AnswerStatus = 'answered' | 'partial' | 'no_evidence' | 'failed' | 'awaiting_approval'
export type AnswerMetadata = { answer_status?: AnswerStatus; errors?: any[]; unsupported?: any[]; coverage?: any; reason?: string }
export function answerMetadata(message: any): AnswerMetadata {
  const meta = { ...(message?.meta || {}), ...message }
  return { answer_status: meta.answer_status, errors: meta.errors || [], unsupported: meta.unsupported || [], coverage: meta.coverage, reason: meta.reason || '' }
}
export function sameProject(left: string, right: string): boolean {
  return left.replaceAll('\\', '/').replace(/\/$/, '').toLowerCase() === right.replaceAll('\\', '/').replace(/\/$/, '').toLowerCase()
}
export function subscribeWorkspace(folder: string, receive: (snapshot: any) => void, failure: (error: Error) => void): () => void {
  let cancelled = false
  let timer: ReturnType<typeof setTimeout>
  const poll = async () => {
    try {
      const status = await call('workspace_subscribe', folder, '')
      if (cancelled) return
      if (status?.ok !== true) throw new Error(status?.error || '工作区状态读取失败')
      if (!sameProject(String(status.project || status.folder || ''), folder)) throw new Error('工作区项目身份不匹配')
      receive(status)
    } catch (error: any) {
      if (!cancelled) failure(error instanceof Error ? error : new Error(String(error)))
    } finally {
      if (!cancelled) timer = setTimeout(poll, 1500)
    }
  }
  void poll()
  return () => { cancelled = true; clearTimeout(timer) }
}

export function newRunId(): string {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

export function matchesChatRun(event: any, identity: ChatRunIdentity): boolean {
  return event?.folder === identity.folder
    && event?.conversation_id === identity.conversation_id
    && event?.run_id === identity.run_id
}

declare global {
  interface Window {
    pywebview?: { api: AnyApi }
  }
}

let bridgePromise: Promise<AnyApi | null> | null = null

/**
 * Wait for pywebview's injected bridge; return null in browser preview.
 *
 * pywebview may fire `pywebviewready` a tick before `window.pywebview.api` is
 * attached (and React may hydrate after the event). A one-shot listener with a
 * short timeout loses that race and leaves the whole app on mock data, so poll
 * for the API itself for a few seconds. A null result is not cached forever.
 */
export function getBridge(): Promise<AnyApi | null> {
  if (typeof window === 'undefined') return Promise.resolve(null)
  if (bridgePromise) return bridgePromise
  bridgePromise = new Promise((resolve) => {
    let settled = false
    let poll: ReturnType<typeof setInterval> | undefined
    const finish = () => {
      if (settled) return
      settled = true
      if (poll) clearInterval(poll)
      const api = window.pywebview?.api ?? null
      if (!api) bridgePromise = null
      resolve(api)
    }
    if (window.pywebview?.api) return finish()
    // The event can fire before the API object is attached; only settle on the
    // event when the API is actually there. Otherwise the poll below catches it.
    window.addEventListener('pywebviewready', () => {
      if (window.pywebview?.api) finish()
    }, { once: true })
    // Inline bootstrap in layout.tsx dispatches this once the API is attached;
    // it does not depend on React hydration or (throttled) timers.
    window.addEventListener('mvbridge', () => {
      if (window.pywebview?.api) finish()
    }, { once: true })
    const started = Date.now()
    poll = setInterval(() => {
      if (window.pywebview?.api) {
        finish()
      } else if (Date.now() - started > 6000) {
        finish()
      }
    }, 50)
  })
  return bridgePromise
}

export async function call<T = any>(method: string, ...args: any[]): Promise<T> {
  const api = await getBridge()
  if (api) {
    if (typeof api[method] === 'function') return api[method](...args) as Promise<T>
    return { ok: false, error: `桌面接口不可用：${method}`, error_code: 'bridge_method_unavailable', method } as T
  }
  return mockCall(method, args) as T
}

function mockCall(method: string, args: any[]): any {
  if (method === 'bootstrap') {
    return { ok: true, version: '0.18.1', media_base: '', meeting_running: false }
  }
  if (method === 'get_config') {
    return {
      engine_python: '', repo_dir: '', projects: [], last_project: '',
      llm_provider: 'local', llm_model: '', audio_source: 'both',
      asr_engine: 'local', interest_mode: 'manual',
    }
  }
  if (method === 'list_sessions' || method === 'list_library') return []
  if (method === 'bootstrap') return { ok: true, meeting_running: false, llm: { up: false } }
  if (method === 'evidence_packs') return { ok: true, packs: [] }
  if (method === 'commitment_ledger') return { ok: true, rows: [] }
  if (method === 'find_moment') return { ok: true, segments: [], answer: '浏览器预览未连接会议证据' }
  if (method === 'workspace_start') return { ok: false, error: '浏览器预览未连接桌面引擎，未启动项目认知' }
  if (method === 'set_config') return { ok: true, preview_only: true }
  if (method === 'session_detail') return { sid: String(args[0] || ''), minutes: '', clips: [] }
  if (method === 'screen_state') return { ok: true, screen: {}, phase: 'idle', running: false }
  if (method === 'architecture') return { ok: true, architecture_status: 'not_built', groups: [], edges: [], generation: { mode: 'none' } }
  if (method === 'chat_new') {
    return { ok: true, conversation_id: 'mock-conversation', title: '新对话' }
  }
  if (method === 'chat_send') {
    return {
      ok: true, folder: args[0], project: args[0], conversation_id: args[1], run_id: args[4], title: '新对话',
      message: {
        role: 'assistant', mode: 'chat',
        content: '这是浏览器预览回复，未连接桌面引擎，也未读取、保存或同步任何会议数据。',
        ts: Date.now() / 1000,
      },
    }
  }
  if (method === 'scan_project') {
    return {
      ok: true, doc_count: 0, term_count: 0, skipped: [],
      tree: [{ name: 'meeting-agent', dir: true, rel: 'meeting-agent', children: [] }],
    }
  }
  if (method === 'pick_folder') return null
  if (method === 'read_project_file') {
    return {
      ok: true, name: String(args[1] || 'app.tsx').split('/').pop(), suffix: '.tsx',
      kind: 'text', kind_label: '代码', kind_short: 'TSX', size: 320, mismatch: false,
      language: 'tsx',
      text: [
        'import { meetingAgent } from "@meeting/core"',
        '',
        'const meeting = await meetingAgent.start({',
        '  title: "产品周会",',
        '  autoNotes: true,',
        '})',
      ].join('\n'),
    }
  }
  if (method === 'start_meeting' || method === 'stop_meeting') {
    return { ok: false, error: '浏览器预览未连接桌面引擎，未执行会议操作', error_code: 'bridge_unavailable' }
  }
  if (method === 'poll_events') return { events: [], next: 0, running: false, screen: {}, phase: 'idle' }
  if (method === 'agent_memory') return { ok: true, facts: [], stats: { total: 0 } }
  if (method === 'agent_status') return { ok: true, facts: 0, focus: [] }
  if (method === 'rag_status') return { ok: true, status: { documents: 0, chunks: 0 } }
  if (method === 'kg_status') return { ok: true, nodes: 0, edges: 0 }
  return { ok: false, error: 'bridge_unavailable', method, args }
}

export function isDesktop(): boolean {
  return typeof window !== 'undefined' && !!window.pywebview?.api
}