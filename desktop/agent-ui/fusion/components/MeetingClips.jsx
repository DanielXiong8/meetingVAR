"use client"
import { useEffect, useMemo, useState } from "react"
import { ChevronDown, FolderOpen } from "lucide-react"
import { ProjectFolder } from "../../sources/folders/components/project-folder"
import { NewProjectSlot } from "../../sources/folders/components/new-project-slot"
import { call, sameProject } from "../../lib/bridge"
import { formatSessionStamp, projectLabel, sessionTimestamp, sessionTitle } from "../../lib/meetingvar"

const FILE_THUMBS = [
  "/project-thumbs/meeting-notes.svg",
  "/project-thumbs/repository.svg",
  "/project-thumbs/roadmap.svg",
  "/project-thumbs/commitments.svg",
  "/project-thumbs/meeting-map.svg",
]
const PREVIEW_FILES = [
  ["会议纪要", "notes"],
  ["代码仓库", "code"],
  ["路线图", "slide"],
  ["承诺表", "table"],
  ["证据地图", "board"],
]
const IMAGE_RE = /\.(png|jpe?g|webp|gif|svg)$/i
const UNASSIGNED = "__unassigned__"
function flattenFiles(nodes, files = []) {
  for (const node of nodes || []) {
    if (node.dir) flattenFiles(node.children || [], files)
    else if (IMAGE_RE.test(String(node.rel || node.name || ""))) files.push(node)
  }
  return files
}
function projectKey(path) {
  return String(path || "").replaceAll("\\", "/").replace(/\/+$/, "").toLowerCase()
}
function uniqueProjects(project, projects, sessions) {
  const seen = new Map()
  for (const path of [project, ...(projects || []), ...(sessions || []).map((session) => session.project)]) {
    const value = String(path || "")
    const key = projectKey(value)
    if (key && !seen.has(key)) seen.set(key, value)
  }
  return [...seen.values()]
}

export default function MeetingClips({ sessions = [], desktop, project = "", projects = [], onBack, onSelect, onChooseProject, onSelectProject, onRename }) {
  const [covers, setCovers] = useState({})
  const [projectCovers, setProjectCovers] = useState({})
  // 会议库按项目隔离：只展示当前项目名下的会议，不再把全库会话混在一页。
  // 没有项目归属的历史会话单独放 "未分配"，否则会从每个项目视图里消失。
  const unassignedCount = useMemo(() => sessions.filter((session) => !session?.project).length, [sessions])
  const [scope, setScope] = useState(project)
  useEffect(() => { setScope(project) }, [project])
  const scoped = useMemo(() => {
    if (scope === UNASSIGNED) return sessions.filter((session) => !session?.project)
    return scope ? sessions.filter((session) => session?.project && sameProject(String(session.project), scope)) : []
  }, [sessions, scope])
  const sessionCounts = useMemo(() => {
    const counts = {}
    for (const session of sessions) {
      const key = projectKey(session?.project)
      if (key) counts[key] = (counts[key] || 0) + 1
    }
    return counts
  }, [sessions])
  const options = useMemo(() => {
    const list = uniqueProjects(project, projects, sessions)
    return unassignedCount ? [...list, UNASSIGNED] : list
  }, [project, projects, sessions, unassignedCount])
  const scopeLabel = scope === UNASSIGNED ? "未分配" : projectLabel(scope)

  useEffect(() => {
    if (!desktop || !scoped.length) { setCovers({}); setProjectCovers({}); return }
    let cancelled = false
    setCovers({})
    ;(async () => {
      const next = {}
      await Promise.all(scoped.slice(0, 8).map(async (session) => {
        try {
          const detail = await call("session_detail", session.sid)
          const frames = (detail?.clips || []).flatMap((clip) => clip.frames || []).map((frame) => frame.url).filter(Boolean)
          next[session.sid] = frames.slice(0, 5)
        } catch {}
      }))
      const thumbnails = {}
      const scanTarget = scope === UNASSIGNED ? "" : scope
      if (scanTarget) {
        try {
          const scan = await call("scan_project", scanTarget)
          const images = flattenFiles(Array.isArray(scan?.tree) ? scan.tree : scan?.tree?.children || []).slice(0, 5)
          const loaded = []
          for (const image of images) {
            const file = await call("read_project_file", scanTarget, image.rel)
            const source = file?.data_url || file?.url
            if (source) loaded.push(source)
          }
          thumbnails[projectKey(scanTarget)] = loaded
        } catch {}
      }
      if (!cancelled) { setCovers(next); setProjectCovers(thumbnails) }
    })()
    return () => { cancelled = true }
  }, [desktop, scoped, scope])

  const meetings = useMemo(() => scoped.map((session, index) => {
    const frames = covers[session.sid] || []
    const projectImages = projectCovers[projectKey(session.project)] || []
    const fallbackFiles = FILE_THUMBS.map((_, offset) => FILE_THUMBS[(index + offset) % FILE_THUMBS.length])
    const primary = frames[0] || projectImages[0] || fallbackFiles[2]
    const secondary = frames.slice(1, 3).concat(projectImages.slice(1, 3), fallbackFiles.filter((image) => image !== primary))
    const images = [secondary[1] || primary, secondary[0] || primary, primary, secondary[2] || primary, secondary[3] || primary]
    const stamp = sessionTimestamp(session)
    return {
      id: session.sid,
      session,
      title: sessionTitle(session),
      clipCount: Number(session.clips || 0),
      createdAt: stamp ? stamp.toISOString() : new Date().toISOString(),
      dateLabel: stamp ? formatSessionStamp(stamp) : "",
      images,
      imageSource: frames.length ? "captured frames" : projectImages.length ? "project files" : "file previews",
    }
  }), [scoped, covers, projectCovers])

  const previewFiles = useMemo(() => !desktop && !sessions.length ? PREVIEW_FILES.map(([title], index) => {
    const primary = FILE_THUMBS[index]
    const rest = FILE_THUMBS.filter((image) => image !== primary)
    return {
      id: `preview-${index}`,
      title,
      clipCount: 1,
      itemLabel: "文件",
      createdAt: new Date().toISOString(),
      images: [rest[1], rest[0], primary, rest[2], rest[3]],
      imageSource: "file previews",
    }
  }) : [], [desktop, sessions.length])
  const cards = meetings.length ? meetings : previewFiles

  return <div className="h-full overflow-auto bg-[#191919] folders-page"><main className="min-h-screen p-8 pt-10"><div className="mx-auto folders-width" style={{ width: "912px" }}>
    <div className="flex items-center justify-between h-12 mb-2"><div className="flex min-w-0 items-center gap-4"><button className="text-white/50 hover:text-white text-xs" onClick={onBack}>← 对话</button><h1 className="inline text-xl font-semibold text-white tracking-tight">会议</h1>{project ? <label className="relative inline-flex min-w-0 items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.04] py-1 pl-2.5 pr-7 text-xs text-white/70 transition-colors hover:border-white/20"><FolderOpen className="h-3.5 w-3.5 shrink-0 text-white/40" /><select aria-label="项目" value={scope} onChange={(event) => { const next = event.target.value; if (!next) return; setScope(next); if (next !== UNASSIGNED && !sameProject(next, project)) onSelectProject?.(next) }} className="max-w-[220px] cursor-pointer appearance-none truncate bg-transparent text-xs text-white/80 outline-none">{options.map((path) => <option key={path} value={path} className="bg-[#1a1a1a] text-white">{path === UNASSIGNED ? `未分配（${unassignedCount}）` : `${projectLabel(path)} (${sessionCounts[projectKey(path)] || 0})`}</option>)}</select><ChevronDown className="pointer-events-none absolute right-2 h-3.5 w-3.5 text-white/40" /></label> : null}</div><button className="text-sm font-medium text-black rounded-lg hover:bg-white/90 transition-colors py-1.5 bg-card-foreground px-3" onClick={onChooseProject}>打开项目</button></div>
    <p className="mb-6 text-xs text-white/35">{scope === UNASSIGNED ? `显示 ${scoped.length} 场未分配会议。它们还没有关联到项目。` : project ? `显示 ${scopeLabel} 的 ${scoped.length} 场会议。其他项目保持独立。` : "选择一个项目，查看它记录过的会议。"}</p>
    {!project && !cards.length ? <div className="rounded-2xl border border-dashed border-white/10 px-6 py-16 text-center"><h2 className="text-lg font-medium text-white/80">未选择项目</h2><p className="mx-auto mt-2 max-w-sm text-sm leading-6 text-white/40">打开一个项目查看会议。每个项目维护自己的会议库。</p><button onClick={onChooseProject} className="mt-5 rounded-lg bg-card-foreground px-4 py-2 text-sm font-medium text-black">打开项目</button></div> : cards.length ? <div className="grid gap-6" style={{ gridTemplateColumns: "repeat(3, 288px)" }}><NewProjectSlot onClick={onChooseProject} />{cards.map((card, i) => <ProjectFolder key={card.id} project={card} index={i} onClick={card.session ? () => onSelect(card.session) : undefined} onRename={card.session ? (title) => onRename?.(card.session.sid, title) : undefined} />)}</div> : <div className="rounded-2xl border border-dashed border-white/10 px-6 py-16 text-center"><h2 className="text-lg font-medium text-white/80">这个项目还没有会议</h2><p className="mx-auto mt-2 max-w-sm text-sm leading-6 text-white/40">你为 {projectLabel(project)} 录制或导入的会议会显示在这里。其他项目不会混进来。</p><button onClick={onChooseProject} className="mt-5 rounded-lg bg-card-foreground px-4 py-2 text-sm font-medium text-black">打开项目</button></div>}
    <p className="mt-8 text-center text-xs text-white/30">{!desktop && previewFiles.length ? "浏览器预览使用文件类型缩略图；桌面端会显示捕获的会议画面和真实项目图片。" : !meetings.length ? "" : meetings.some((m) => m.imageSource === "captured frames") ? "优先使用捕获的会议画面，其次使用项目图片。" : "没有可用的捕获画面；封面使用项目图片或文件类型缩略图。"}</p>
  </div></main></div>
}