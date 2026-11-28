"use client"
import { Menu, ChevronDown, LayoutGrid, FolderOpen, PanelRight } from "lucide-react"
import GhostIconButton from "./GhostIconButton"

export default function Header({ sidebarCollapsed, setSidebarOpen, openClips, project, onProject, onToggleProjectTree }) {
  return (
    <div className="sticky top-0 z-30 flex items-center gap-2 border-b border-zinc-200/60 bg-white/80 px-4 py-3 backdrop-blur dark:border-zinc-800 dark:bg-zinc-900/70">
      {sidebarCollapsed && (
        <button onClick={() => setSidebarOpen(true)} className="md:hidden inline-flex items-center justify-center rounded-lg p-2 hover:bg-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:hover:bg-zinc-800" aria-label="打开侧边栏">
          <Menu className="h-5 w-5" />
        </button>
      )}
      <button onClick={onProject} className="inline-flex max-w-56 items-center gap-2 rounded-full border border-zinc-200 bg-white px-3 py-2 text-sm font-semibold tracking-tight hover:bg-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:border-zinc-800 dark:bg-zinc-950 dark:hover:bg-zinc-800">
        <FolderOpen className="h-4 w-4 shrink-0 text-zinc-500" />
        <span className="truncate">{project}</span>
        <ChevronDown className="h-4 w-4 shrink-0" />
      </button>
      <div className="ml-auto flex items-center gap-2">
        <GhostIconButton label="项目文件" onClick={onToggleProjectTree}><PanelRight className="h-4 w-4" /></GhostIconButton><GhostIconButton label="会议库" onClick={openClips}><LayoutGrid className="h-4 w-4" /></GhostIconButton>
      </div>
    </div>
  )
}