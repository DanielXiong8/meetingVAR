"use client"

import type { Project } from "../../lib/data"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "../ui/dropdown-menu"
import { MoreVertical, Pencil, Trash2, X } from "lucide-react"

interface MenuButtonProps {
  project: Project
  open?: boolean
  onOpenChange?: (open: boolean) => void
  onRemove?: () => void
  onCancel?: () => void
  onRename?: () => void
  hideEdit?: boolean
  isVisible?: boolean
}

export function MenuButton({ project, open, onOpenChange, onRemove, onCancel, onRename, hideEdit, isVisible = false }: MenuButtonProps) {
  return (
    <DropdownMenu open={open} onOpenChange={onOpenChange}>
      <DropdownMenuTrigger asChild>
        <button
          data-menu
          className={`p-1.5 rounded-md hover:bg-white/[0.08] transition-all duration-150 focus:outline-none focus-visible:ring-1 focus-visible:ring-white/20 ${
            isVisible ? "opacity-100" : "opacity-0"
          }`}
          onClick={(e) => e.stopPropagation()}
        >
          <MoreVertical className="w-4 h-4 text-white/40" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        side="top"
        sideOffset={4}
        className="w-40 bg-[#1a1a1a] border-white/[0.08] rounded-lg shadow-[0_8px_30px_rgba(0,0,0,0.5)]"
        onClick={(e) => e.stopPropagation()}
        onCloseAutoFocus={(event) => event.preventDefault()}
      >
        {project.isGenerating ? (
          <DropdownMenuItem
            className="group text-white/70 hover:text-red-400 focus:text-red-400 focus:bg-white/[0.06] cursor-pointer gap-2 transition-colors"
            onSelect={onCancel}
          >
            <X className="w-4 h-4 transition-colors group-hover:text-red-400 group-focus:text-red-400" />
            取消
          </DropdownMenuItem>
        ) : (
          <>
            {!hideEdit && (
              <DropdownMenuItem
                className="text-white/70 focus:text-white focus:bg-white/[0.06] cursor-pointer gap-2"
                onSelect={onRename}
              >
                <Pencil className="w-4 h-4" />
                重命名
              </DropdownMenuItem>
            )}
            {onRemove ? (
              <DropdownMenuItem
                className="group text-white/70 hover:text-red-400 focus:text-red-400 focus:bg-white/[0.06] cursor-pointer gap-2 transition-colors"
                onSelect={onRemove}
              >
                <Trash2 className="w-4 h-4 transition-colors group-hover:text-red-400 group-focus:text-red-400" />
                移除
              </DropdownMenuItem>
            ) : null}
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
