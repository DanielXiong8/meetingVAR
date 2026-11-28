"use client"
import { useEffect, useRef, useState } from "react"
import { Play, Square, Crosshair, Monitor, Mic, Radio, Users, Clock3, AlertTriangle, X, ArrowLeft, Check } from "lucide-react"
import { projectLabel } from "../../lib/meetingvar"

const softButton = "inline-flex items-center justify-center gap-2 rounded-full border border-zinc-200 bg-white px-4 py-2 text-sm font-medium text-zinc-800 shadow-sm transition hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 dark:hover:bg-zinc-800"
const darkButton = "inline-flex items-center justify-center gap-2 rounded-full bg-zinc-900 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-zinc-800 disabled:opacity-50 dark:bg-white dark:text-zinc-900"

export function StartMeetingDialog({ open, project, config, pending, onClose, onStart, onChooseProject }) {
  const [focus, setFocus] = useState(config?.interest_focus || "")
  const [askSpeakers, setAskSpeakers] = useState(config?.ask_speakers !== false)
  const [error, setError] = useState("")
  useEffect(() => { if (open) { setFocus(config?.interest_focus || ""); setAskSpeakers(config?.ask_speakers !== false); setError("") } }, [open, config?.interest_focus, config?.ask_speakers])
  if (!open) return null
  const apiMode = config?.interest_mode === "api"
  const apiReady = Boolean(config?.llm_base_url && config?.llm_model)
  const source = config?.audio_source === "mic" ? "麦克风" : config?.audio_source === "loopback" ? "系统声音" : "系统声音 + 麦克风"
  async function start() {
    setError("")
    const result = await onStart(focus, askSpeakers)
    if (!result?.ok) setError(result?.error || "会议无法开始。")
  }
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm" onClick={onClose}>
    <section className="w-full max-w-xl rounded-2xl border border-zinc-200 bg-white p-6 shadow-xl dark:border-zinc-800 dark:bg-zinc-950" onClick={(event) => event.stopPropagation()}>
      <div className="flex items-start justify-between gap-4"><div><span className="text-xs uppercase tracking-wider text-zinc-500">会议开始前</span><h2 className="mt-1 text-2xl font-bold tracking-tight">这次会议要关注什么？</h2><p className="mt-2 text-sm leading-6 text-zinc-500">meetingVAR 用这个关注点决定保留哪些时刻、承诺和证据。录制仍然是选择性的。</p></div><button onClick={onClose} aria-label="关闭开始会议" className="rounded-full p-2 hover:bg-zinc-100 dark:hover:bg-zinc-800"><X className="h-5 w-5" /></button></div>
      <label className="mt-5 grid gap-2 text-sm"><span className="font-medium">本次会议关注点</span><textarea value={focus} onChange={(event) => setFocus(event.target.value)} rows={4} placeholder="例如：交付日期变化、未解决的集成风险、负责人变更、首个版本的决策……" className="w-full resize-none rounded-xl border border-zinc-200 bg-white px-3 py-2.5 text-sm leading-6 outline-none placeholder:text-zinc-400 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 dark:border-zinc-700 dark:bg-zinc-900" /></label>
      <div className="mt-4 grid gap-2 rounded-xl border border-zinc-200 p-3 text-xs text-zinc-600 dark:border-zinc-800 dark:text-zinc-300">
        <div className="flex items-center justify-between gap-3"><span>项目</span><button onClick={onChooseProject} className="max-w-[70%] truncate text-right font-medium text-zinc-900 dark:text-zinc-100">{projectLabel(project)}</button></div>
        <div className="flex items-center justify-between gap-3"><span>音频</span><b className="font-medium">{source}</b></div>
        <div className="flex items-center justify-between gap-3"><span>触发方式</span><b className="font-medium">{apiMode ? (apiReady ? "模型 + 手动标记" : "API 未配置") : "仅手动标记"}</b></div>
        <div className="flex items-center justify-between gap-3"><span>屏幕</span><b className="font-medium">屏幕预览 + 选择性保留画面</b></div><label className="mt-2 flex items-center justify-between gap-3 border-t border-zinc-200 pt-3 dark:border-zinc-800"><span>询问新说话人姓名 <small className="mt-1 block text-[11px] font-normal text-zinc-500">出现新声音后暂停，便于你分配姓名。</small></span><input type="checkbox" checked={askSpeakers} onChange={(event) => setAskSpeakers(event.target.checked)} className="h-4 w-4 accent-zinc-900 dark:accent-white" /></label>
      </div>
      {apiMode && !apiReady ? <p className="mt-3 flex items-start gap-2 rounded-lg bg-amber-50 p-3 text-xs text-amber-800 dark:bg-amber-950/40 dark:text-amber-200"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />API 兴趣判断需要 接口地址（Base URL） 和模型。请在设置中配置，或改为仅手动标记。</p> : null}
      {error ? <p className="mt-3 rounded-lg bg-red-50 p-3 text-xs text-red-700 dark:bg-red-950/40 dark:text-red-300">{error}</p> : null}
      <div className="mt-6 flex justify-end gap-2"><button className={softButton} onClick={onClose}>取消</button><button className={darkButton} disabled={pending || !project || (apiMode && !apiReady)} onClick={() => void start()}><Play className="h-4 w-4" />{!project ? "请先选择项目" : pending ? "正在开始……" : "开始会议"}</button></div>
    </section>
  </div>
}

function timecode(startedAt) {
  if (!startedAt) return "00:00"
  const seconds = Math.max(0, Math.floor((Date.now() - startedAt) / 1000))
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`
}

function SpeakerPrompt({ ask, onAnswer, pending }) {
  const [name, setName] = useState("")
  useEffect(() => { setName("") }, [ask?.spk])
  if (!ask) return null
  return <div className="fixed inset-0 z-[55] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"><section className="w-full max-w-md rounded-2xl border border-zinc-200 bg-white p-5 shadow-xl dark:border-zinc-800 dark:bg-zinc-950"><div className="flex items-start justify-between"><div><span className="text-xs uppercase tracking-wider text-zinc-500">新说话人</span><h3 className="mt-1 text-xl font-semibold">谁在说话？</h3></div><Users className="h-5 w-5 text-zinc-400" /></div><p className="mt-3 text-sm text-zinc-600 dark:text-zinc-300"><b>{ask.spk}</b>{ask.sample ? <> 说： “{ask.sample}”</> : " 被检测到。"}</p><p className="mt-2 text-xs leading-5 text-zinc-500">姓名有助于把原话和承诺归到人。也可以跳过，保留匿名声纹 ID。</p><input autoFocus value={name} onChange={(event) => setName(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void onAnswer(name) }} placeholder="姓名或角色" className="mt-4 w-full rounded-xl border border-zinc-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 dark:border-zinc-700 dark:bg-zinc-900" /><div className="mt-4 flex justify-end gap-2"><button className={softButton} disabled={pending} onClick={() => void onAnswer("")}>跳过</button><button className={darkButton} disabled={pending || !name.trim()} onClick={() => void onAnswer(name.trim())}><Check className="h-4 w-4" />保存姓名</button></div></section></div>
}

export function LiveMeetingConsole({ live, recording, pending, project, config, on停止, onVar, onAnswer, onBack }) {
  const [now, setNow] = useState(Date.now())
  const bottomRef = useRef(null)
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer) }, [])
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }) }, [live.lines.length])
  const partials = Object.values(live.partials || {})
  const rms = Number(live.audio?.rms || 0)
  const screen = live.screen || {}
  const ended = !recording && Boolean(live.summary) || (!recording && live.phase === "idle" && live.lines.length > 0)
  return <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
    <div className="flex flex-wrap items-center gap-3 border-b border-zinc-200 px-5 py-3 dark:border-zinc-800"><button className="inline-flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-900 dark:hover:text-white" onClick={onBack}><ArrowLeft className="h-3.5 w-3.5" />返回</button><span className={"h-2.5 w-2.5 rounded-full " + (recording ? "animate-pulse bg-red-500" : "bg-zinc-400")} /><div><b className="text-sm">{recording ? "会议进行中" : ended ? "会议已结束" : "会议控制台"}</b><div className="text-xs text-zinc-500">{projectLabel(project)} · {recording ? timecode(live.startedAt) : live.phase}</div></div><div className="ml-auto flex gap-2">{recording ? <button className={softButton} disabled={pending} onClick={() => void onVar()}><Crosshair className="h-4 w-4" />保存这一刻</button> : null}<button className={recording ? softButton : darkButton} disabled={pending} onClick={() => recording ? on停止() : onBack()}>{recording ? <Square className="h-4 w-4" /> : <ArrowLeft className="h-4 w-4" />}{recording ? (pending ? "正在停止……" : "结束会议") : "返回对话"}</button></div></div>
    <div className="grid min-h-0 flex-1 gap-4 overflow-auto p-5 lg:grid-cols-[minmax(0,1.15fr)_minmax(320px,.85fr)]">
      <div className="min-w-0 space-y-4">
        <section className="overflow-hidden rounded-2xl border border-zinc-200 bg-zinc-100 dark:border-zinc-800 dark:bg-zinc-900">
          <div className="relative aspect-video bg-zinc-950/90">{screen.preview ? <img src={screen.preview} alt="实时屏幕捕获预览" className="h-full w-full object-contain" /> : <div className="flex h-full flex-col items-center justify-center gap-2 text-zinc-500"><Monitor className="h-7 w-7" /><span className="text-sm">{recording ? "等待第一帧屏幕画面" : "屏幕捕获未运行"}</span><span className="text-xs">只有已保存的时刻会保留画面。</span></div>}<div className="absolute left-3 top-3 rounded-full bg-black/55 px-2.5 py-1 font-mono text-[10px] text-white/80">{screen.fps_target ? `${screen.fps_target} FPS 目标` : live.phase}</div></div>
          <div className="flex flex-wrap items-center gap-3 px-3 py-2 text-xs text-zinc-500"><span className="inline-flex items-center gap-1"><Monitor className="h-3.5 w-3.5" />{screen.frame_count || 0} 帧</span><span>{screen.buffer_mb ? `${screen.buffer_mb} MB 缓冲` : "缓冲准备中"}</span>{screen.error ? <span className="text-red-500">{screen.error}</span> : null}<span className="ml-auto">{live.phase}</span></div>
        </section>
        <div className="grid gap-3 sm:grid-cols-3">
          <div className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800"><div className="flex items-center gap-2 text-xs text-zinc-500"><Mic className="h-3.5 w-3.5" />音频</div><div className="mt-2 flex h-5 items-end gap-1">{Array.from({ length: 18 }).map((_, index) => <span key={index} className="w-full rounded-sm bg-zinc-400/60 dark:bg-zinc-500" style={{ height: `${Math.max(3, Math.min(20, rms * 55 + ((index * 7) % 8)))}px` }} />)}</div><p className="mt-2 text-[11px] text-zinc-500">{live.asr?.processing ? "识别中……" : live.asr?.latency ? `ASR ${live.asr.latency}s` : "等待语音"}</p></div>
          <div className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800"><div className="flex items-center gap-2 text-xs text-zinc-500"><Radio className="h-3.5 w-3.5" />关注点</div><p className="mt-2 text-sm font-medium">{live.interest || (config.interest_mode === "manual" ? "仅手动标记" : "等待兴趣 API")}</p><p className="mt-1 text-[11px] text-zinc-500">{live.hits.length} 条已保存命中</p></div>
          <div className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800"><div className="flex items-center gap-2 text-xs text-zinc-500"><Users className="h-3.5 w-3.5" />说话人</div><p className="mt-2 truncate text-sm font-medium">{live.speakers.join(" / ") || "等待声音"}</p><p className="mt-1 text-[11px] text-zinc-500">{live.asks.length ? "等待姓名确认" : "本次会话的声纹 ID"}</p></div>
        </div>
        {live.warnings?.length ? <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">{live.warnings.map((warning, index) => <p key={index}>{warning}</p>)}</div> : null}
        {live.summary ? <section className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm dark:border-emerald-900 dark:bg-emerald-950/30"><b>会议已保存</b><p className="mt-1 text-xs text-emerald-800 dark:text-emerald-200">{live.summary.session || live.summary.session_id || "会话已完成"} · {live.summary.lines || live.lines.length} 条已确认转写</p><button className={softButton + " mt-3"} onClick={onBack}>返回对话</button></section> : null}
      </div>
      <section className="flex min-h-[420px] min-w-0 flex-col rounded-2xl border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-950">
        <div className="flex items-center justify-between border-b border-zinc-200 px-4 py-3 dark:border-zinc-800"><div><h3 className="text-sm font-semibold">实时转写</h3><p className="mt-1 text-[11px] text-zinc-500">临时行会在确认后被替换。</p></div><span className="inline-flex items-center gap-1 rounded-full bg-zinc-100 px-2 py-1 text-[10px] text-zinc-500 dark:bg-zinc-800"><Clock3 className="h-3 w-3" />{timecode(live.startedAt)}</span></div>
        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">{live.lines.length ? live.lines.map((line) => <div key={line.id} className="text-sm"><div className="mb-1 flex gap-2 text-[11px] text-zinc-400"><span className="font-mono">{line.clock}</span><span>{line.spk}</span></div><p className="leading-6 text-zinc-800 dark:text-zinc-200">{line.text}</p></div>) : <div className="flex h-full flex-col items-center justify-center py-12 text-center text-zinc-400"><Mic className="mb-2 h-6 w-6" /><p className="text-sm">实时字幕会显示在这里。</p><p className="mt-1 max-w-xs text-xs">临时文本会原地更新；已确认的行可回看。</p></div>}{partials.length ? <div className="rounded-xl border border-dashed border-zinc-300 bg-zinc-50 p-3 dark:border-zinc-700 dark:bg-zinc-900"><div className="mb-1 text-[10px] uppercase tracking-wider text-zinc-400">临时</div>{partials.map((text, index) => <p key={index} className="text-sm leading-6 text-zinc-500">{String(text)}</p>)}</div> : null}<div ref={bottomRef} /></div>
      </section>
    </div>
    {live.asks?.length ? <SpeakerPrompt ask={live.asks[0]} pending={pending} onAnswer={onAnswer} /> : null}
  </div>
}