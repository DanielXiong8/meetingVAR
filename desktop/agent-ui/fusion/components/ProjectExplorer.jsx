"use client"
import { useMemo, useState } from "react"
import { ChevronRight, ChevronDown, File, FileCode2, FileImage, FileText, Folder, FolderOpen, RefreshCw, X, ExternalLink, PanelRightClose, Plus } from "lucide-react"

function iconFor(node) {
  if (node.dir) return null
  const suffix = String(node.suffix || node.name || "").toLowerCase()
  if (/\.(png|jpe?g|gif|webp|svg|bmp)$/.test(suffix)) return <FileImage className="h-3.5 w-3.5" />
  if (/\.(tsx?|jsx?|py|java|go|rs|cs|cpp|c|json|ya?ml|toml|css|html|sql)$/.test(suffix)) return <FileCode2 className="h-3.5 w-3.5" />
  return <FileText className="h-3.5 w-3.5" />
}

function TreeNode({ node, depth = 0, onOpenFile }) {
  const [open, setOpen] = useState(depth < 1)
  const pad = 10 + depth * 13
  if (node.dir) return <div>
    <button onClick={() => setOpen((value) => !value)} className="flex w-full items-center gap-1.5 rounded-md py-1.5 pr-2 text-left text-xs text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800" style={{ paddingLeft: `${pad}px` }}>
      {open ? <ChevronDown className="h-3 w-3 shrink-0 text-zinc-400" /> : <ChevronRight className="h-3 w-3 shrink-0 text-zinc-400" />}
      {open ? <FolderOpen className="h-3.5 w-3.5 shrink-0 text-zinc-500" /> : <Folder className="h-3.5 w-3.5 shrink-0 text-zinc-500" />}
      <span className="truncate">{node.name}</span>
    </button>
    {open ? <div>{Array.isArray(node.children) ? node.children.map((child, index) => <TreeNode key={`${child.rel || child.name}-${index}`} node={child} depth={depth + 1} onOpenFile={onOpenFile} />) : null}</div> : null}
  </div>
  return <button onClick={() => onOpenFile(node.rel || node.name)} className="flex w-full items-center gap-1.5 rounded-md py-1.5 pr-2 text-left text-xs text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800" style={{ paddingLeft: `${pad + 16}px` }} title={node.rel || node.name}>
    {iconFor(node)}<span className="truncate">{node.name}</span>
  </button>
}

export function ProjectExplorer({ project, tree, loading, onClose, onRefresh, onOpenFile, onChooseProject }) {
  return <aside className="project-explorer">
    <div className="flex items-center justify-between border-b border-zinc-200 px-3 py-3 dark:border-zinc-800"><div><div className="text-[10px] uppercase tracking-wider text-zinc-400">项目文件</div><div className="mt-1 max-w-[200px] truncate text-xs font-medium">{project || "未选择项目"}</div></div><div className="flex items-center gap-1"><button onClick={onRefresh} aria-label="刷新项目文件" className="rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"><RefreshCw className="h-4 w-4" /></button><button onClick={onClose} aria-label="关闭项目文件" className="rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"><PanelRightClose className="h-4 w-4" /></button></div></div>
    <div className="min-h-0 flex-1 overflow-y-auto px-1.5 py-2">{loading ? <p className="px-2 py-4 text-xs text-zinc-500">正在读取项目文件……</p> : tree.length ? tree.map((node, index) => <TreeNode key={`${node.rel || node.name}-${index}`} node={node} onOpenFile={onOpenFile} />) : <div className="px-3 py-8 text-center"><Folder className="mx-auto h-7 w-7 text-zinc-300 dark:text-zinc-700" /><p className="mt-3 text-xs leading-5 text-zinc-500">选择一个本地项目以浏览文件。</p><button onClick={onChooseProject} className="mt-3 inline-flex items-center gap-1.5 rounded-full border border-zinc-200 px-3 py-1.5 text-xs hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-900"><Plus className="h-3.5 w-3.5" />打开项目</button></div>}</div>
    <div className="border-t border-zinc-200 px-3 py-2 text-[10px] text-zinc-400 dark:border-zinc-800">只读项目视图</div>
  </aside>
}

export function ProjectFileViewer({ file, onClose, onOpenExternal }) {
  if (!file) return null
  const kind = String(file.kind || "")
  const meta = [file.kind_label || kind, file.size ? `${(Number(file.size) / 1024).toFixed(1)} KB` : "", file.language || ""].filter(Boolean).join(" · ")
  const text = String(file.text || "")
  const isMedia = kind === "image" || kind === "video" || kind === "audio"
  const source = file.data_url || file.url || ""
  return <div className="fixed inset-0 z-[65] flex items-center justify-center bg-black/55 p-4 backdrop-blur-sm" onClick={onClose}>
    <section className="flex max-h-[88vh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-zinc-200 bg-white shadow-xl dark:border-zinc-800 dark:bg-zinc-950" onClick={(event) => event.stopPropagation()}>
      <header className="flex items-start justify-between gap-4 border-b border-zinc-200 px-5 py-3 dark:border-zinc-800"><div className="min-w-0"><h2 className="truncate text-sm font-semibold">{file.name || file.rel || "文件"}</h2><p className="mt-1 text-[11px] text-zinc-500">{meta}</p>{file.mismatch ? <p className="mt-1 text-[11px] text-amber-600">文件后缀与检测到的内容不一致。</p> : null}</div><div className="flex shrink-0 items-center gap-1"><button onClick={() => onOpenExternal(file.rel)} className="inline-flex items-center gap-1 rounded-full border border-zinc-200 px-3 py-1.5 text-xs hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-900"><ExternalLink className="h-3.5 w-3.5" />用系统应用打开</button><button onClick={onClose} aria-label="关闭文件查看器" className="rounded-full p-2 hover:bg-zinc-100 dark:hover:bg-zinc-800"><X className="h-4 w-4" /></button></div></header>
      <div className="min-h-0 flex-1 overflow-auto bg-zinc-50 p-4 dark:bg-zinc-900/60">
        {kind === "image" && source ? <img src={source} alt={file.name} className="mx-auto max-h-[70vh] max-w-full rounded-lg object-contain" /> : null}
        {kind === "video" && source ? <video src={source} controls className="mx-auto max-h-[70vh] max-w-full rounded-lg" /> : null}
        {kind === "audio" && source ? <audio src={source} controls className="mx-auto mt-8 w-full max-w-xl" /> : null}
        {kind === "pdf" && source ? <iframe src={source} title={file.name} className="h-[70vh] w-full rounded-lg bg-white" /> : null}
        {kind === "zip" ? <div className="rounded-lg border border-zinc-200 bg-white p-4 text-xs dark:border-zinc-800 dark:bg-zinc-950"><p className="mb-3 text-zinc-500">{file.entry_count || file.entries?.length || 0} 个条目 · 未解压</p>{(file.entries || []).map((entry, index) => <div className="flex justify-between border-t border-zinc-100 py-1.5 dark:border-zinc-800" key={index}><span>{entry.name}</span><span className="text-zinc-400">{entry.dir ? "文件夹" : `${(Number(entry.size) / 1024).toFixed(1)} KB`}</span></div>)}</div> : null}
        {!isMedia && kind !== "zip" && text ? <div className="overflow-auto rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-950"><div className="flex min-w-max"><pre className="select-none border-r border-zinc-200 px-3 py-3 text-right font-mono text-[10px] leading-5 text-zinc-300 dark:border-zinc-800 dark:text-zinc-700">{text.split("\n").map((_, index) => index + 1).join("\n")}</pre><pre className="whitespace-pre-wrap px-4 py-3 font-mono text-[11px] leading-5 text-zinc-700 dark:text-zinc-300">{text}</pre></div></div> : null}
        {!isMedia && kind !== "zip" && !text ? <p className="py-12 text-center text-sm text-zinc-500">无法在应用内预览。请用系统应用打开此文件。</p> : null}
      </div>
    </section>
  </div>
}