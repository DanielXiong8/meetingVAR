"use client"

import { useRef, useState, forwardRef, useImperativeHandle, useEffect } from "react"
import { Send, Loader2, ChevronDown, SlidersHorizontal } from "lucide-react"
import { cls } from "./utils"
import { REASONING_LEVELS, reasoningIndex } from "./model-options"
import ModelPicker from "./ModelPicker"

function compactTokens(value) {
  const number = Math.max(0, Number(value) || 0)
  return number >= 1000000 ? `${(number / 1000000).toFixed(1)}M` : number >= 1000 ? `${(number / 1000).toFixed(1)}K` : `${number}`
}

const Composer = forwardRef(function Composer({
  onSend,
  busy,
  models = [],
  model = "",
  onModelChange,
  provider = "local",
  reasoning = "",
  onReasoningChange,
  reasoningAvailable = false,
  contextUsed = 0,
  contextTotal = 16384,
  contextEstimated = false,
  onOpenSettings,
}, ref) {
  const [value, setValue] = useState("")
  const [sending, setSending] = useState(false)
  const [pickerOpen, setPickerOpen] = useState(false)
  const inputRef = useRef(null)
  const level = REASONING_LEVELS[reasoningIndex(reasoning)]

  useEffect(() => {
    if (!inputRef.current) return
    const textarea = inputRef.current
    textarea.style.height = "auto"
    const scrollHeight = textarea.scrollHeight
    const lines = Math.max(1, Math.ceil(scrollHeight / 24))
    if (lines <= 12) { textarea.style.height = `${Math.max(24, scrollHeight)}px`; textarea.style.overflowY = "hidden" }
    else { textarea.style.height = "288px"; textarea.style.overflowY = "auto" }
  }, [value])

  useEffect(() => {
    const onKey = (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "t") {
        event.preventDefault()
        setPickerOpen((open) => !open)
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  useImperativeHandle(ref, () => ({
    insertTemplate: (templateContent) => setValue((prev) => {
      const next = prev ? `${prev}\n\n${templateContent}` : templateContent
      setTimeout(() => { inputRef.current?.focus(); const length = next.length; inputRef.current?.setSelectionRange(length, length) }, 0)
      return next
    }),
    focus: () => inputRef.current?.focus(),
    openModelPicker: () => setPickerOpen(true),
  }), [])

  async function handleSend() {
    if (!value.trim() || sending) return
    setSending(true)
    try { await onSend?.(value); setValue(""); inputRef.current?.focus() } finally { setSending(false) }
  }

  const hasContent = value.trim().length > 0
  const percent = Math.max(0, Math.min(100, Math.round((Number(contextUsed) / Math.max(1, Number(contextTotal))) * 100)))
  return (
    <div className="border-t border-zinc-200/60 p-4 dark:border-zinc-800">
      <div className="relative mx-auto max-w-3xl">
        <ModelPicker open={pickerOpen} onClose={() => setPickerOpen(false)} config={{ llm_model: model, llm_provider: provider === "local" ? "local" : "custom", llm_context_tokens: contextTotal, llm_api_context_tokens: contextTotal }} models={models} provider={provider} onModelChange={onModelChange} reasoning={reasoning} onReasoningChange={onReasoningChange} reasoningAvailable={reasoningAvailable} onManage={onOpenSettings} />
        <div className={cls("flex flex-col rounded-3xl border bg-white shadow-sm dark:bg-zinc-950 transition-all duration-200", "border-zinc-200 dark:border-zinc-800")}>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-zinc-100 px-4 py-2.5 dark:border-zinc-800/80">
            <button type="button" data-model-trigger onClick={() => setPickerOpen((open) => !open)} aria-expanded={pickerOpen} className={cls("inline-flex min-w-0 items-center gap-2 rounded-full border px-2.5 py-1 text-xs transition-colors", pickerOpen ? "border-zinc-300 bg-zinc-100 text-zinc-900 dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-100" : "border-zinc-200 text-zinc-600 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-900")}>
              <SlidersHorizontal className="h-3.5 w-3.5 shrink-0 text-zinc-400" />
              <span className="max-w-[180px] truncate font-medium">{model || "选择模型"}</span>
              <span className="shrink-0 rounded-full bg-zinc-100 px-1.5 py-0.5 text-[10px] text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">{reasoningAvailable ? level.label : "默认"}</span>
              <ChevronDown className={cls("h-3 w-3 shrink-0 text-zinc-400 transition-transform", pickerOpen ? "rotate-180" : "")} />
            </button>
            <div className="ml-auto inline-flex shrink-0 items-center gap-2 text-[11px] text-zinc-400 dark:text-zinc-500" title={contextEstimated ? "根据当前对话估算；精确 token 用量会在 Agent 下次回复后显示。" : "最近一次模型调用使用的 prompt token 数。"}>
              <span>上下文</span>
              <span className="h-1.5 w-20 overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-800"><span className="block h-full rounded-full bg-zinc-500 transition-[width] duration-300 dark:bg-zinc-300" style={{ width: `${percent}%` }} /></span>
              <span className="w-[116px] shrink-0 text-right font-mono">{compactTokens(contextUsed)} / {compactTokens(contextTotal)}{contextEstimated ? " 估算" : ""}</span>
            </div>
          </div>
          <div className="flex-1 px-4 pt-3 pb-2">
            <textarea ref={inputRef} value={value} onChange={(e) => setValue(e.target.value)} placeholder="询问某个决定、承诺，或它们背后的原话" rows={1} className={cls("w-full resize-none bg-transparent text-sm outline-none placeholder:text-zinc-400 transition-all duration-200", "min-h-[24px] text-left leading-6")} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend() } }} />
          </div>
          <div className="flex items-center justify-between px-3 pb-3">
            <span className="pl-1 text-[10px] text-zinc-400 dark:text-zinc-500">有据可查 · 本地证据引擎</span>
            <button onClick={handleSend} disabled={sending || busy || !hasContent} aria-label="发送问题" className={cls("inline-flex shrink-0 items-center justify-center rounded-full p-2.5 transition-colors", hasContent ? "bg-zinc-900 text-white hover:bg-zinc-800 dark:bg-white dark:text-zinc-900 dark:hover:bg-zinc-200" : "bg-zinc-200 text-zinc-400 dark:bg-zinc-800 dark:text-zinc-600 cursor-not-allowed")}>
              {sending || busy ? <Loader2 className="h-5 w-5 animate-spin" /> : <Send className="h-5 w-5" />}
            </button>
          </div>
        </div>
      </div>
      <div className="mx-auto mt-2 max-w-3xl px-1 text-center text-[11px] text-zinc-400 dark:text-zinc-500">meetingVAR · 每个回答都回到已保存的会议证据。</div>
    </div>
  )
})

export default Composer