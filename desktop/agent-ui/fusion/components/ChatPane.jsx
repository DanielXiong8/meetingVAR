"use client"

import { useState, forwardRef, useImperativeHandle, useRef } from "react"
import { Pencil, RefreshCw, Check, X, Square } from "lucide-react"
import Message from "./Message"
import Composer from "./Composer"
import { cls, timeAgo } from "./utils"

function ThinkingMessage({ onPause }) {
  return (
    <Message role="assistant">
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-1">
          <div className="h-2 w-2 animate-bounce rounded-full bg-zinc-400 [animation-delay:-0.3s]"></div>
          <div className="h-2 w-2 animate-bounce rounded-full bg-zinc-400 [animation-delay:-0.15s]"></div>
          <div className="h-2 w-2 animate-bounce rounded-full bg-zinc-400"></div>
        </div>
        <span className="text-sm text-zinc-500">正在检索会议证据……</span>
        <button
          onClick={onPause}
          className="ml-auto inline-flex items-center gap-1 rounded-full border border-zinc-300 px-2 py-1 text-xs text-zinc-600 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-800"
        >
          <Square className="h-3 w-3" /> 停止
        </button>
      </div>
    </Message>
  )
}

const ChatPane = forwardRef(function ChatPane(
  { conversation, onSend, onEditMessage, on重发Message, isThinking, onPauseThinking, liveEvents = [], onAction, models, model, onModelChange, provider, onOpenSettings, reasoning, onReasoningChange, reasoningAvailable, contextUsed, contextTotal, contextEstimated },
  ref,
) {
  const [editingId, setEditingId] = useState(null)
  const [draft, setDraft] = useState("")
  const [busy, setBusy] = useState(false)
  const composerRef = useRef(null)

  useImperativeHandle(
    ref,
    () => ({
      insertTemplate: (templateContent) => {
        composerRef.current?.insertTemplate(templateContent)
      },
    }),
    [],
  )

  if (!conversation) return null

  const messages = Array.isArray(conversation.messages) ? conversation.messages : []
  const count = messages.length || conversation.messageCount || 0

  function startEdit(m) {
    setEditingId(m.id)
    setDraft(m.content)
  }
  function cancelEdit() {
    setEditingId(null)
    setDraft("")
  }
  function saveEdit() {
    if (!editingId) return
    onEditMessage?.(editingId, draft)
    cancelEdit()
  }
  function saveAnd重发() {
    if (!editingId) return
    onEditMessage?.(editingId, draft)
    on重发Message?.(editingId)
    cancelEdit()
  }

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col">
      <div className="flex-1 space-y-5 overflow-y-auto px-4 py-6 sm:px-8">
        <div className="mb-2 text-3xl font-serif tracking-tight sm:text-4xl md:text-5xl">
          <span className="block leading-[1.05] font-sans text-2xl">{conversation.title}</span>
        </div>
        {messages.length === 0 ? (
          <div className="rounded-xl border border-dashed border-zinc-300 p-6 text-sm text-zinc-500 dark:border-zinc-700 dark:text-zinc-400">
            询问一场已保存的会议。每个回答都应带回它的出处。
          </div>
        ) : (
          <>
            {messages.map((m) => (
              <div key={m.id} className="space-y-2">
                {editingId === m.id ? (
                  <div className={cls("rounded-2xl border p-2", "border-zinc-200 dark:border-zinc-800")}>
                    <textarea
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      className="w-full resize-y rounded-xl bg-transparent p-2 text-sm outline-none"
                      rows={3}
                    />
                    <div className="mt-2 flex items-center gap-2">
                      <button
                        onClick={saveEdit}
                        className="inline-flex items-center gap-1 rounded-full bg-zinc-900 px-3 py-1.5 text-xs text-white dark:bg-white dark:text-zinc-900"
                      >
                        <Check className="h-3.5 w-3.5" /> 保存
                      </button>
                      <button
                        onClick={saveAnd重发}
                        className="inline-flex items-center gap-1 rounded-full border px-3 py-1.5 text-xs"
                      >
                        <RefreshCw className="h-3.5 w-3.5" /> 保存并重发
                      </button>
                      <button
                        onClick={cancelEdit}
                        className="inline-flex items-center gap-1 rounded-full px-3 py-1.5 text-xs"
                      >
                        <X className="h-3.5 w-3.5" /> 取消
                      </button>
                    </div>
                  </div>
                ) : (
                  <Message role={m.role}>
                    <div className="whitespace-pre-wrap">{m.content}</div>
                    {m.citations?.length ? <div className="mt-3 border-t border-zinc-200/50 pt-3 dark:border-zinc-700"><div className="mb-2 text-xs text-zinc-500">来源</div>{m.citations.map((c, i) => <div className="mt-2 text-xs" key={i}><b>[{i + 1}] {c.session_id || c.title || c.path || c.name || "来源"}</b><p className="mt-1">{c.quote || c.text || ""}</p></div>)}</div> : null}
                    {m.ledger?.length ? <div className="mt-3 rounded-lg border border-zinc-200 p-3 text-xs dark:border-zinc-700">{m.ledger.map((row, i) => <div className="mt-2" key={i}><b>{row.title}</b><p>{row.owner || "未分配"} · {row.due || "无截止日期"}</p>{(row.changes || []).map((change, j) => <p key={j}>{change.from} → {change.to}</p>)}</div>)}</div> : null}
                    {m.segments?.length ? <div className="mt-3 text-xs text-zinc-500">找回那一刻：已返回 {m.segments.length} 个片段。点击工具栏中的“时刻”查看。</div> : null}
                    {m.actions?.length ? <div className="mt-3 flex flex-wrap gap-2">{m.actions.map((action) => <button className="rounded-full bg-zinc-900 px-3 py-1.5 text-xs text-white dark:bg-white dark:text-zinc-900" key={action.id} onClick={() => onAction?.(action)}>{action.label || "确认操作"}</button>)}</div> : null}
                    {m.role === "user" && (
                      <div className="mt-1 flex gap-2 text-[11px] text-zinc-500">
                        <button className="inline-flex items-center gap-1 hover:underline" onClick={() => startEdit(m)}>
                          <Pencil className="h-3.5 w-3.5" /> 编辑
                        </button>
                        <button
                          className="inline-flex items-center gap-1 hover:underline"
                          onClick={() => on重发Message?.(m.id)}
                        >
                          <RefreshCw className="h-3.5 w-3.5" /> 重发
                        </button>
                      </div>
                    )}
                  </Message>
                )}
              </div>
            ))}
            {isThinking && <ThinkingMessage onPause={onPauseThinking} />}
            {isThinking && liveEvents.length ? <div className="ml-9 text-xs text-zinc-500">{liveEvents.filter((e) => e.phase === "tool").slice(-3).map((event, i) => <div key={i}>{event.tool || event.name || "工具"} · {event.status || "运行中"}</div>)}</div> : null}
          </>
        )}
      </div>

      <Composer
        ref={composerRef}
        models={models}
        model={model}
        onModelChange={onModelChange}
        provider={provider}
        onOpenSettings={onOpenSettings}
        reasoning={reasoning}
        onReasoningChange={onReasoningChange}
        reasoningAvailable={reasoningAvailable}
        contextUsed={contextUsed}
        contextTotal={contextTotal}
        contextEstimated={contextEstimated}
        onSend={async (text) => {
          if (!text.trim()) return
          setBusy(true)
          await onSend?.(text)
          setBusy(false)
        }}
        busy={busy}
      />
    </div>
  )
})

export default ChatPane
