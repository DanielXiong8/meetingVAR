"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import { X, Save, Plus, Trash2, Eye, EyeOff, Server, Cpu, AudioLines, Check, Pencil, Settings2 } from "lucide-react"
import { call } from "../../lib/bridge"
import { PROVIDER_PRESETS, contextLimit, loadProviderId, saveProviderId } from "./model-options"

const field = "w-full rounded-xl border border-zinc-200 bg-white px-3 py-2 text-sm outline-none transition-colors focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 dark:border-zinc-700 dark:bg-zinc-900"
const pill = "inline-flex items-center justify-center gap-2 rounded-full px-4 py-2 text-sm font-medium transition-colors"
const TABS = [
  { id: "general", label: "常规", icon: Server },
  { id: "models", label: "模型设置", icon: Cpu },
  { id: "capture", label: "音频与采集", icon: AudioLines },
]

function compact(value) {
  const number = Math.max(0, Number(value) || 0)
  return number >= 1000000 ? `${(number / 1000000).toFixed(1)}M` : number >= 1000 ? `${(number / 1000).toFixed(1)}K` : String(number)
}

function Switch({ checked, onChange, label }) {
  return <button type="button" role="switch" aria-checked={checked} aria-label={label} onClick={() => onChange?.(!checked)} className={"relative h-5 w-9 shrink-0 rounded-full transition-colors " + (checked ? "bg-emerald-500" : "bg-zinc-300 dark:bg-zinc-700")}>
    <span className={"absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform " + (checked ? "translate-x-4" : "translate-x-0")} />
  </button>
}

export default function MeetingSettings({ open, onClose, onNotice, config: incoming = {}, onSaved, models = [], onModelsChange }) {
  const [config, setConfig] = useState(incoming)
  const [tab, setTab] = useState("models")
  const [provider, setProvider] = useState("custom")
  const [showKey, setShowKey] = useState(false)
  const [draftModel, setDraftModel] = useState("")
  const [editing, setEditing] = useState("")
  const [editingValue, setEditingValue] = useState("")
  const [busy, setBusy] = useState(false)
  const [test, setTest] = useState({ state: "idle", message: "" })
  const baseRef = useRef(null)

  useEffect(() => {
    if (!open) return
    let cancelled = false
    call("get_config").then((value) => {
      if (cancelled || !value) return
      const next = { ...incoming, ...value }
      setConfig(next)
      setProvider(loadProviderId(next))
    })
    return () => { cancelled = true }
  }, [open])

  const activeModel = String(config.llm_model || "")
  const modelOptions = useMemo(() => [...new Set([activeModel, ...models].filter(Boolean))], [activeModel, models])
  const preset = PROVIDER_PRESETS.find((item) => item.id === provider) || PROVIDER_PRESETS[0]
  const providerActive = provider === "local" ? config.llm_provider === "local" : config.llm_provider !== "local"
  const contextValue = config.llm_provider === "custom" ? config.llm_api_context_tokens : config.llm_context_tokens
  if (!open) return null

  function pickProvider(next) {
    const item = PROVIDER_PRESETS.find((candidate) => candidate.id === next)
    if (!item) return
    setProvider(next)
    if (next === "local") {
      setConfig((current) => ({ ...current, llm_provider: "local" }))
    } else {
      setConfig((current) => {
        const staleBase = PROVIDER_PRESETS.some((presetItem) => presetItem.id !== "custom" && presetItem.base && presetItem.base === current.llm_base_url)
        const staleModel = PROVIDER_PRESETS.some((presetItem) => presetItem.id !== "custom" && presetItem.model && presetItem.model === current.llm_model)
        return {
          ...current,
          llm_provider: "custom",
          llm_base_url: item.base || (staleBase ? "" : current.llm_base_url || ""),
          llm_model: item.model || (staleModel ? "" : current.llm_model || ""),
        }
      })
      if (item.model && !modelOptions.includes(item.model)) onModelsChange?.([...modelOptions, item.model])
    }
    setTest({ state: "idle", message: "" })
  }

  function toggleProvider(next) {
    if (next) {
      if (provider === "local") setConfig((current) => ({ ...current, llm_provider: "local" }))
      else setConfig((current) => ({ ...current, llm_provider: "custom" }))
    } else {
      setProvider("local")
      setConfig((current) => ({ ...current, llm_provider: "local" }))
    }
  }

  function addModel() {
    const name = draftModel.trim()
    if (!name) return
    const next = [...new Set([...modelOptions, name])]
    onModelsChange?.(next)
    setConfig((current) => ({ ...current, llm_model: name, llm_provider: provider === "local" ? "local" : "custom" }))
    setDraftModel("")
  }

  function removeModel(name) {
    const next = modelOptions.filter((item) => item !== name)
    onModelsChange?.(next)
    if (activeModel === name) setConfig((current) => ({ ...current, llm_model: next[0] || "" }))
  }

  function renameModel(name) {
    const nextName = editingValue.trim()
    if (!nextName || nextName === name) { setEditing(""); return }
    const next = [...new Set(modelOptions.map((item) => item === name ? nextName : item))]
    onModelsChange?.(next)
    setConfig((current) => ({ ...current, llm_model: current.llm_model === name ? nextName : current.llm_model }))
    setEditing("")
    setEditingValue("")
  }

  function addCustomProvider() {
    setProvider("custom")
    setConfig((current) => ({ ...current, llm_provider: "custom" }))
    setTab("models")
    setTimeout(() => baseRef.current?.focus(), 30)
  }

  async function testConnection() {
    setBusy(true); setTest({ state: "testing", message: "正在调用已配置的接口……" })
    try {
      await call("set_config", { ...config, llm_provider: provider === "local" ? "local" : "custom" })
      const result = await call("test_llm")
      if (result?.ok) setTest({ state: "ok", message: `已连接 · ${result.models?.[0] || config.llm_model || "模型"}` })
      else setTest({ state: "error", message: result?.error || "连接失败" })
    } catch (error) { setTest({ state: "error", message: String(error?.message || error) }) } finally { setBusy(false) }
  }

  async function save() {
    setBusy(true)
    try {
      saveProviderId(provider)
      const result = await call("set_config", { ...config, llm_provider: provider === "local" ? "local" : "custom" })
      if (result?.error) onNotice?.(result.error)
      else { onNotice?.("模型设置已保存。"); onSaved?.(); onClose() }
    } finally { setBusy(false) }
  }

  const title = TABS.find((item) => item.id === tab)?.label || "设置"
  return <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/55 p-0 backdrop-blur-sm sm:p-4" onClick={onClose}>
    <section className="flex h-full w-full max-w-[1240px] overflow-hidden border border-zinc-200 bg-white shadow-2xl dark:border-zinc-800 dark:bg-zinc-950 sm:h-[min(830px,92vh)] sm:rounded-2xl" onClick={(event) => event.stopPropagation()}>
      <aside className="hidden w-56 shrink-0 border-r border-zinc-200 bg-zinc-50/80 p-3 dark:border-zinc-800 dark:bg-zinc-900/50 sm:flex sm:flex-col">
        <div className="flex items-center gap-2 px-2 py-3"><span className="flex h-6 w-6 items-center justify-center rounded-lg bg-zinc-900 text-white dark:bg-white dark:text-zinc-900"><Settings2 className="h-3.5 w-3.5" /></span><span className="text-sm font-semibold">设置</span></div>
        <nav className="mt-3 space-y-1">
          {TABS.map((item) => <button key={item.id} onClick={() => setTab(item.id)} className={"flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-[13px] transition-colors " + (tab === item.id ? "bg-white text-zinc-900 shadow-sm dark:bg-zinc-800 dark:text-white" : "text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800/70")}><item.icon className="h-4 w-4" />{item.label}</button>)}
        </nav>
        <div className="mt-auto rounded-xl border border-zinc-200/80 bg-white/60 p-3 text-[11px] leading-5 text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900/60">本地优先。凭据保存在桌面端配置里，不进入对话历史。</div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 shrink-0 items-center justify-between border-b border-zinc-200 px-5 dark:border-zinc-800">
          <div className="flex items-center gap-3"><div><div className="text-[10px] uppercase tracking-[0.14em] text-zinc-400">meetingVAR</div><h2 className="mt-0.5 text-[15px] font-semibold tracking-tight">{title}</h2></div>{tab === "models" ? <button type="button" onClick={addCustomProvider} className="ml-2 inline-flex items-center gap-1 rounded-full border border-zinc-200 px-2.5 py-1 text-[11px] text-zinc-600 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-900"><Plus className="h-3 w-3" />添加服务商</button> : null}</div>
          <button onClick={onClose} aria-label="关闭设置" className="rounded-full p-2 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"><X className="h-5 w-5" /></button>
        </header>
        <div className="min-h-0 flex-1 overflow-hidden">
          {tab === "general" ? <div className="h-full overflow-y-auto p-6"><div className="max-w-2xl space-y-5">
            <div><h3 className="text-sm font-semibold">引擎</h3><p className="mt-1 text-xs text-zinc-500">启动本地会议引擎所用的路径。</p></div>
            <label className="grid gap-1.5 text-sm"><span className="text-xs font-medium text-zinc-600 dark:text-zinc-300">引擎 Python</span><input className={field} value={config.engine_python || ""} onChange={(e) => setConfig({ ...config, engine_python: e.target.value })} placeholder="Python 3.10 的路径" /></label>
            <label className="grid gap-1.5 text-sm"><span className="text-xs font-medium text-zinc-600 dark:text-zinc-300">仓库目录</span><input className={field} value={config.repo_dir || ""} onChange={(e) => setConfig({ ...config, repo_dir: e.target.value })} /></label>
          </div></div> : null}
          {tab === "capture" ? <div className="h-full overflow-y-auto p-6"><div className="max-w-2xl space-y-5">
            <div><h3 className="text-sm font-semibold">采集</h3><p className="mt-1 text-xs text-zinc-500">选择性存储始终开启；这里只选择输入源和触发方式。</p></div>
            <label className="grid gap-1.5 text-sm"><span className="text-xs font-medium text-zinc-600 dark:text-zinc-300">音频来源</span><select className={field} value={config.audio_source || "both"} onChange={(e) => setConfig({ ...config, audio_source: e.target.value })}><option value="both">系统声音 + 麦克风</option><option value="loopback">仅系统声音</option><option value="mic">仅麦克风</option></select></label>
            <label className="grid gap-1.5 text-sm"><span className="text-xs font-medium text-zinc-600 dark:text-zinc-300">采集模式</span><select className={field} value={config.interest_mode || "manual"} onChange={(e) => setConfig({ ...config, interest_mode: e.target.value })}><option value="manual">仅手动标记</option><option value="api">模型 + 手动标记</option></select></label>
            <p className="rounded-xl border border-zinc-200 bg-zinc-50 p-3 text-xs leading-6 text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900/50">采集引擎仍遵循选择性存储。这些设置不会上传会议内容。</p>
          </div></div> : null}
          {tab === "models" ? <div className="flex h-full min-h-0">
            <div className="w-[214px] shrink-0 overflow-y-auto border-r border-zinc-200 p-2.5 dark:border-zinc-800">
              <div className="px-2 pb-2 pt-1 text-[10px] font-semibold uppercase tracking-[0.14em] text-zinc-400">服务商</div>
              {["内置", "预设"].map((group) => <div key={group} className="mb-2">
                <div className="px-2 py-1 text-[10px] text-zinc-400">{group}</div>
                {PROVIDER_PRESETS.filter((item) => item.group === group).map((item) => {
                  const selected = provider === item.id
                  const isActive = selected && providerActive
                  return <button key={item.id} onClick={() => pickProvider(item.id)} className={"flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left transition-colors " + (selected ? "bg-zinc-100 dark:bg-zinc-800" : "hover:bg-zinc-50 dark:hover:bg-zinc-900")}>
                    <span className={"flex h-7 w-7 shrink-0 items-center justify-center rounded-lg " + (selected ? "bg-zinc-900 text-white dark:bg-white dark:text-zinc-900" : "bg-zinc-100 text-zinc-500 dark:bg-zinc-800")}><Cpu className="h-3.5 w-3.5" /></span>
                    <span className="min-w-0 flex-1"><span className="block truncate text-xs font-medium">{item.name}</span><span className="mt-0.5 block truncate text-[10px] text-zinc-400">{item.note}</span></span>
                    {isActive ? <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500" title="当前服务商" /> : null}
                  </button>
                })}
              </div>)}
            </div>
            <div className="min-w-0 flex-1 overflow-y-auto p-5 sm:p-6">
              <div className="mx-auto max-w-3xl">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex min-w-0 items-center gap-3"><span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-zinc-200 bg-zinc-50 text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900"><Cpu className="h-5 w-5" /></span><div className="min-w-0"><h3 className="truncate text-base font-semibold">{preset.name}</h3><p className="mt-0.5 text-[11px] text-zinc-500">{preset.note} · 同一时间只启用一个服务商</p></div></div>
                  <div className="flex shrink-0 items-center gap-2 text-[11px] text-zinc-500"><span>{providerActive ? "已启用" : "未启用"}</span><Switch checked={providerActive} onChange={toggleProvider} label="启用此服务商" /></div>
                </div>
                {provider !== "local" ? <div className="mt-6 space-y-4">
                  <label className="grid gap-1.5"><span className="text-xs font-medium text-zinc-600 dark:text-zinc-300">接口地址（Base URL）</span><input ref={baseRef} className={field} value={config.llm_base_url || ""} onChange={(e) => setConfig({ ...config, llm_base_url: e.target.value })} placeholder="https://api.example.com/v1" /></label>
                  <label className="grid gap-1.5"><span className="text-xs font-medium text-zinc-600 dark:text-zinc-300">API 格式</span><select className={field} value="chat"><option value="chat">Chat Completions (/chat/completions)</option></select></label>
                  <label className="grid gap-1.5"><span className="text-xs font-medium text-zinc-600 dark:text-zinc-300">API 密钥</span><div className="relative"><input type={showKey ? "text" : "password"} className={field + " pr-10 font-mono text-xs"} value={config.llm_api_key || ""} onChange={(e) => setConfig({ ...config, llm_api_key: e.target.value })} autoComplete="off" placeholder="由本地桌面引擎保存" /><button type="button" onClick={() => setShowKey(!showKey)} aria-label={showKey ? "隐藏 API 密钥" : "显示 API 密钥"} className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200">{showKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}</button></div></label>
                </div> : <div className="mt-6 rounded-xl border border-zinc-200 bg-zinc-50 p-4 text-xs leading-6 text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900/50">本地服务商使用这个桌面应用管理的模型运行时，不需要 API 密钥，也不会发出外部请求。</div>}
                <div className="mt-7">
                  <div className="mb-2 flex items-center justify-between"><div><h4 className="text-sm font-medium">模型</h4><p className="mt-0.5 text-[11px] text-zinc-500">这些模型会出现在输入框的模型选择器中。</p></div><span className="text-[11px] text-zinc-400">已配置 {modelOptions.length} 个</span></div>
                  <div className="overflow-hidden rounded-xl border border-zinc-200 dark:border-zinc-800">
                    {modelOptions.map((name) => editing === name ? <div key={name} className="flex items-center gap-2 border-b border-zinc-100 bg-zinc-50 px-3 py-2 dark:border-zinc-800 dark:bg-zinc-900/50">
                      <input autoFocus value={editingValue} onChange={(event) => setEditingValue(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") renameModel(name); if (event.key === "Escape") setEditing("") }} className="min-w-0 flex-1 rounded-md border border-zinc-200 bg-white px-2 py-1 text-xs outline-none dark:border-zinc-700 dark:bg-zinc-950" />
                      <button onClick={() => renameModel(name)} className="rounded px-2 py-1 text-[11px] text-emerald-600 hover:bg-emerald-50 dark:hover:bg-emerald-950">保存</button>
                      <button onClick={() => setEditing("")} className="rounded px-2 py-1 text-[11px] text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800">取消</button>
                    </div> : <div key={name} className="flex items-center gap-3 border-b border-zinc-100 px-3 py-2.5 last:border-b-0 dark:border-zinc-800">
                      <button onClick={() => setConfig({ ...config, llm_model: name, llm_provider: provider === "local" ? "local" : "custom" })} aria-label={`使用 ${name}`} className={"flex h-4 w-4 shrink-0 items-center justify-center rounded-full border " + (activeModel === name ? "border-emerald-500 bg-emerald-500 text-white" : "border-zinc-300 dark:border-zinc-600")}>{activeModel === name ? <Check className="h-2.5 w-2.5" /> : null}</button>
                      <span className="min-w-0 flex-1 truncate text-xs font-medium">{name}</span>
                      <span className="shrink-0 text-[10px] text-zinc-400">{compact(contextLimit(config))} 上下文</span>
                      <button onClick={() => { setEditing(name); setEditingValue(name) }} aria-label={`编辑 ${name}`} className="rounded p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"><Pencil className="h-3 w-3" /></button>
                      <button onClick={() => removeModel(name)} aria-label={`移除 ${name}`} className="rounded p-1 text-zinc-400 hover:text-red-500"><Trash2 className="h-3 w-3" /></button>
                      <Switch checked={activeModel === name} onChange={(on) => { if (on) setConfig({ ...config, llm_model: name, llm_provider: provider === "local" ? "local" : "custom" }); else setConfig({ ...config, llm_model: "" }) }} label={`启用 ${name}`} />
                    </div>)}
                    {!modelOptions.length ? <div className="px-3 py-5 text-xs text-zinc-500">还没有配置模型。</div> : null}
                    <div className="flex items-center gap-2 bg-zinc-50/60 p-2.5 dark:bg-zinc-900/40"><input value={draftModel} onChange={(e) => setDraftModel(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addModel() } }} placeholder="添加模型名称" className={field + " !rounded-lg !py-1.5 !text-xs"} /><button onClick={addModel} className="inline-flex shrink-0 items-center gap-1 rounded-full border border-zinc-200 px-3 py-1.5 text-[11px] hover:bg-white dark:border-zinc-700 dark:hover:bg-zinc-800"><Plus className="h-3 w-3" />添加模型</button></div>
                  </div>
                </div>
                <div className="mt-5 grid gap-4 sm:grid-cols-2">
                  <label className="grid gap-1.5"><span className="text-xs font-medium text-zinc-600 dark:text-zinc-300">上下文窗口（tokens）</span><input type="number" min={4096} className={field} value={contextValue || (config.llm_provider === "custom" ? 500000 : 16384)} onChange={(e) => { const value = Number(e.target.value) || 4096; setConfig({ ...config, ...(config.llm_provider === "custom" ? { llm_api_context_tokens: value } : { llm_context_tokens: value }) }) }} /></label>
                  <div className="rounded-xl border border-zinc-200 bg-zinc-50 p-3 dark:border-zinc-800 dark:bg-zinc-900/50"><div className="text-xs font-medium text-zinc-700 dark:text-zinc-200">思考强度</div><p className="mt-1 text-[10px] leading-4 text-zinc-500">在输入框中设置。默认不发送 <code>reasoning_effort</code>；低/中/高只会转发给 OpenAI 兼容服务商。</p></div>
                </div>
                <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-zinc-200 pt-4 dark:border-zinc-800"><button disabled={busy} onClick={() => void testConnection()} className={pill + " border border-zinc-200 text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-900"}>{busy ? "测试中……" : "测试连接"}</button>{test.message ? <span className={"text-xs " + (test.state === "ok" ? "text-emerald-600" : test.state === "error" ? "text-red-500" : "text-zinc-500")}>{test.message}</span> : null}</div>
              </div>
            </div>
          </div> : null}
        </div>
        <footer className="flex h-16 shrink-0 items-center justify-between border-t border-zinc-200 px-5 dark:border-zinc-800"><p className="text-[11px] text-zinc-500">API 密钥由本地桌面引擎保存，不会出现在对话中。</p><div className="flex gap-2"><button onClick={onClose} className={pill + " border border-zinc-200 text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-900"}>取消</button><button disabled={busy} onClick={() => void save()} className={pill + " bg-zinc-900 text-white shadow-sm hover:bg-zinc-800 disabled:opacity-50 dark:bg-white dark:text-zinc-900"}><Save className="h-4 w-4" />保存</button></div></footer>
      </div>
    </section>
  </div>
}