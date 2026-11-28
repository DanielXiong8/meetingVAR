"use client"

import { useEffect, useRef, useState } from "react"
import { Check, ChevronRight, Cpu, Search, Settings2, X } from "lucide-react"
import { REASONING_LEVELS, PROVIDER_PRESETS, contextLimit, loadProviderId, reasoningIndex } from "./model-options"

function compact(value) {
  const number = Math.max(0, Number(value) || 0)
  return number >= 1000000 ? `${(number / 1000000).toFixed(1)}M` : number >= 1000 ? `${(number / 1000).toFixed(1)}K` : String(number)
}

function providerFor(id) {
  return PROVIDER_PRESETS.find((item) => item.id === id) || PROVIDER_PRESETS[0]
}

export default function ModelPicker({
  open,
  onClose,
  config = {},
  models = [],
  provider,
  onModelChange,
  reasoning = "",
  onReasoningChange,
  reasoningAvailable = false,
  onManage,
}) {
  const rootRef = useRef(null)
  const [activeProvider, setActiveProvider] = useState(() => provider || loadProviderId(config))
  const [selected, setSelected] = useState(String(config.llm_model || ""))
  const [query, setQuery] = useState("")
  const [draftReasoning, setDraftReasoning] = useState(reasoning)

  useEffect(() => {
    if (!open) return
    setActiveProvider(provider || loadProviderId(config))
    setSelected(String(config.llm_model || ""))
    setDraftReasoning(reasoning)
    setQuery("")
  }, [open])

  useEffect(() => { if (open) setDraftReasoning(reasoning) }, [reasoning, open])

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event) => { if (rootRef.current?.contains(event.target) || event.target?.closest?.("[data-model-trigger]")) return; onClose?.() }
    const onKey = (event) => { if (event.key === "Escape") onClose?.() }
    document.addEventListener("pointerdown", onPointerDown)
    document.addEventListener("keydown", onKey)
    return () => { document.removeEventListener("pointerdown", onPointerDown); document.removeEventListener("keydown", onKey) }
  }, [open, onClose])

  if (!open) return null

  const preset = providerFor(activeProvider)
  const options = [...new Set([selected, String(config.llm_model || ""), preset.model, ...models].filter(Boolean))]
  const visible = options.filter((name) => name.toLowerCase().includes(query.trim().toLowerCase()))
  const limits = compact(contextLimit(config))
  const groups = ["内置", "预设"]
  const index = reasoningIndex(draftReasoning)
  const level = REASONING_LEVELS[index]
  const canUse = Boolean(selected) && (activeProvider === "local" || Boolean(preset.base || config.llm_base_url))
  const reasoningEnabled = reasoningAvailable || activeProvider !== "local"

  function chooseProvider(id) {
    setActiveProvider(id)
  }

  function commit() {
    if (!selected) return
    onModelChange?.(selected, activeProvider)
    onClose?.()
  }

  return <div ref={rootRef} className="absolute bottom-[calc(100%+8px)] left-0 z-50 w-[min(660px,calc(100vw-2rem))] overflow-hidden rounded-2xl border border-zinc-200 bg-white shadow-2xl dark:border-zinc-700 dark:bg-zinc-950">
    <div className="grid min-h-[310px] grid-cols-[210px_minmax(0,1fr)]">
      <aside className="border-r border-zinc-200 bg-zinc-50/70 p-2 dark:border-zinc-800 dark:bg-zinc-900/40">
        <div className="px-2 pb-2 pt-1 text-[10px] font-semibold uppercase tracking-[0.14em] text-zinc-400">服务商</div>
        {groups.map((group) => <div key={group} className="mb-1">
          <div className="px-2 py-1 text-[10px] text-zinc-400">{group}</div>
          {PROVIDER_PRESETS.filter((item) => item.group === group).map((item) => {
            const active = item.id === activeProvider
            return <button key={item.id} type="button" onClick={() => chooseProvider(item.id)} className={"flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left transition-colors " + (active ? "bg-white text-zinc-900 shadow-sm dark:bg-zinc-800 dark:text-white" : "text-zinc-600 hover:bg-white/70 dark:text-zinc-400 dark:hover:bg-zinc-800/60")}>
              <span className={"flex h-6 w-6 shrink-0 items-center justify-center rounded-md " + (active ? "bg-zinc-900 text-white dark:bg-white dark:text-zinc-900" : "bg-zinc-200/70 text-zinc-500 dark:bg-zinc-800")}><Cpu className="h-3.5 w-3.5" /></span>
              <span className="min-w-0 flex-1"><span className="block truncate text-xs font-medium">{item.name}</span><span className="mt-0.5 block truncate text-[10px] text-zinc-400">{item.note}</span></span>
              {active ? <Check className="h-3.5 w-3.5 shrink-0" /> : null}
            </button>
          })}
        </div>)}
      </aside>
      <div className="flex min-w-0 flex-col">
        <div className="flex items-start justify-between gap-3 px-4 pb-2 pt-3">
          <div><div className="text-sm font-semibold">模型</div><div className="mt-0.5 text-[11px] text-zinc-500">{preset.name} · {activeProvider === "local" ? "本地运行时" : "API"}</div></div>
          <button type="button" onClick={onClose} aria-label="关闭模型选择器" className="rounded-md p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"><X className="h-4 w-4" /></button>
        </div>
        <label className="mx-3 flex items-center gap-2 rounded-lg border border-zinc-200 px-2.5 py-1.5 text-xs dark:border-zinc-700"><Search className="h-3.5 w-3.5 text-zinc-400" /><input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索模型" className="min-w-0 flex-1 bg-transparent outline-none placeholder:text-zinc-400" /></label>
        <div className="mt-2 max-h-52 min-h-0 flex-1 overflow-y-auto px-2 pb-2">
          {visible.map((name) => <button key={name} type="button" onClick={() => setSelected(name)} className={"flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left transition-colors " + (selected === name ? "bg-zinc-100 dark:bg-zinc-800" : "hover:bg-zinc-50 dark:hover:bg-zinc-900")}>
            <span className="min-w-0 flex-1 truncate text-xs font-medium">{name}</span>
            <span className="shrink-0 text-[10px] text-zinc-400">{limits}</span>
            {selected === name ? <Check className="h-3.5 w-3.5 shrink-0 text-emerald-500" /> : null}
          </button>)}
          {!visible.length ? <div className="px-3 py-8 text-center text-xs text-zinc-500">{query.trim() ? "没有匹配的模型。" : "尚未配置模型。请在模型设置中添加。"}</div> : null}
        </div>
        <div className="border-t border-zinc-200 px-4 py-3 dark:border-zinc-800">
          <div className="mb-2 flex items-center justify-between gap-3 text-[11px]"><span className="text-zinc-500">思考强度</span><span className="inline-flex items-center gap-2"><b className="font-medium text-zinc-700 dark:text-zinc-200">{reasoningEnabled ? level.label : "服务商默认"}</b><kbd className="rounded border border-zinc-200 px-1 text-[9px] text-zinc-400 dark:border-zinc-700">Ctrl+T</kbd></span></div>
          <input aria-label="思考强度" title={level.hint} type="range" min={0} max={REASONING_LEVELS.length - 1} step={1} value={index} disabled={!reasoningEnabled} onChange={(event) => setDraftReasoning(REASONING_LEVELS[Number(event.target.value)].value)} onPointerUp={() => onReasoningChange?.(draftReasoning)} onKeyUp={() => onReasoningChange?.(draftReasoning)} onBlur={() => onReasoningChange?.(draftReasoning)} className="reasoning-range cursor-pointer" />
          <div className="mt-1 flex justify-between text-[9px] uppercase tracking-wide text-zinc-400"><span>更快</span><span>最高</span></div>
        </div>
        <div className="flex items-center gap-2 border-t border-zinc-200 px-3 py-2.5 dark:border-zinc-800">
          <button type="button" onClick={() => { onClose?.(); onManage?.() }} className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-[11px] text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"><Settings2 className="h-3.5 w-3.5" />模型设置<ChevronRight className="h-3 w-3" /></button>
          <button type="button" disabled={!canUse} onClick={commit} className="ml-auto rounded-full bg-zinc-900 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40 dark:bg-white dark:text-zinc-900 dark:hover:bg-zinc-200">使用此模型</button>
        </div>
      </div>
    </div>
  </div>
}