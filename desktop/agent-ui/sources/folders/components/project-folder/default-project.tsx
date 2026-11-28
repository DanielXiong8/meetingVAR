"use client"

import { useEffect, useRef, useState } from "react"
import { motion } from "framer-motion"
import type { Project } from "../../lib/data"
import type { ImagePosition } from "./types"
import { SlotNumber } from "./slot-number"
import { MenuButton } from "./menu-button"
import { Sparkles } from "./sparkles"

// Rauno-style easing: smooth deceleration
const EASE_OUT_EXPO = [0.16, 1, 0.3, 1] as const

const TRANSITION_DURATION = 0.3 // Declare TRANSITION_DURATION
const TRANSITION_EASE = EASE_OUT_EXPO // Declare TRANSITION_EASE

interface DefaultProjectProps {
  project: Project
  isHovered: boolean
  setIsHovered: (value: boolean) => void
  isGenerating: boolean
  generationComplete: boolean
  progress: number
  sparklesFading: boolean
  showImages: boolean
  showGeneratingFooter: boolean
  imagePositions: ImagePosition[]
  clipCount: number
  remainingEta: string
  formattedDate: string
  onRemove?: () => void
  onCancel?: () => void
  onRename?: (title: string) => void | Promise<unknown>
}

export function DefaultProject({
  project,
  isHovered,
  setIsHovered,
  isGenerating,
  generationComplete,
  progress,
  sparklesFading,
  showImages,
  showGeneratingFooter,
  imagePositions,
  clipCount,
  remainingEta,
  formattedDate,
  onRemove,
  onCancel,
  onRename,
}: DefaultProjectProps) {
  const [isMenuOpen, setIsMenuOpen] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const [draft, setDraft] = useState("")
  const cancelledRef = useRef(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const isActive = (isHovered || isMenuOpen || renaming) && !isGenerating

  useEffect(() => {
    if (!renaming) return
    const input = inputRef.current
    if (input) { input.focus(); input.select() }
  }, [renaming])

  function beginRename() {
    setIsMenuOpen(false)
    cancelledRef.current = false
    setDraft(project.title)
    setRenaming(true)
  }

  function commitRename() {
    if (cancelledRef.current) { cancelledRef.current = false; return }
    const next = draft.trim()
    setRenaming(false)
    if (next === project.title.trim()) return
    void onRename?.(next)
  }

  function cancelRename() {
    cancelledRef.current = true
    setRenaming(false)
  }

  return (
    <div
      className={`group relative w-[288px] ${isGenerating ? "cursor-default" : "cursor-pointer"}`}
      style={{
        perspective: "1200px",
        zIndex: isActive ? 50 : 1,
      }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => !isMenuOpen && !renaming && setIsHovered(false)}
    >
      <div
        className="relative w-[288px]"
        style={{ perspective: "1200px" }}
      >
        {/* 返回 panel */}
        <motion.div
          className="relative z-0 rounded-2xl"
          animate={{
            rotateX: isActive ? 15 : 0,
            backgroundColor: isGenerating ? "#111111" : "#1e1e1e",
          }}
          transition={{
            rotateX: {
              type: "spring",
              stiffness: 200,
              damping: 25,
              mass: 0.8,
            },
            backgroundColor: {
              duration: TRANSITION_DURATION,
              ease: TRANSITION_EASE,
            },
          }}
          style={{
            height: "224px",
            border: "1px solid rgba(255, 255, 255, 0.06)",
            transformStyle: "preserve-3d",
            transformOrigin: "center bottom",
          }}
        >
          {project.isGenerating && <Sparkles count={16} fading={sparklesFading} variant="generating" />}
          <motion.div
            className="absolute inset-0"
            animate={{
              rotateX: isActive ? -15 : 0,
            }}
            transition={{
              type: "spring",
              stiffness: 200,
              damping: 25,
              mass: 0.8,
            }}
            style={{
              transformStyle: "flat",
              transformOrigin: "center bottom",
            }}
          >
            {[...Array(5)].map((_, imgIndex) => {
              const pos = imagePositions[imgIndex]
              const imageUrl = project.images?.[imgIndex % (project.images?.length || 1)] || "/placeholder.svg"
              const shouldShowImages = !project.isGenerating || showImages

              const centerIndex = 2
              const distanceFromCenter = Math.abs(imgIndex - centerIndex)
              const zIndex = 10 - distanceFromCenter

              const brightness = distanceFromCenter === 0 ? 1 : distanceFromCenter === 1 ? 0.55 : 0.3
              const blurAmount = distanceFromCenter === 0 ? 0 : distanceFromCenter === 1 ? 0.5 : 1.5
              const yOffset = -16 * (1 - distanceFromCenter / centerIndex) || 0
              const scale = distanceFromCenter === 0 ? 1.05 : distanceFromCenter === 1 ? 0.95 : 0.88

              // Final positions (where images should be when visible)
              const xPos = isActive ? pos.x * 1.4 : pos.x
              const yPos = isActive ? -8 + yOffset : 8 + yOffset
              const rotation = isActive ? pos.rotate * 1.3 : pos.rotate
              const finalScale = isActive ? scale * 1.02 : scale

              // Center-out stagger: center card (index 2) first, then adjacent, then outer
              const staggerDelay = distanceFromCenter * 0.08

              return (
                <motion.div
                  key={imgIndex}
                  className="absolute left-1/2 top-0"
                  initial={false}
                  animate={{
                    x: `calc(-50% + ${xPos}px)`,
                    y: yPos,
                    rotate: rotation,
                    scale: shouldShowImages ? finalScale : 0.8,
                    opacity: shouldShowImages ? 1 : 0,
                  }}
                  transition={{
                    type: "spring",
                    stiffness: 100,
                    damping: 16,
                    mass: 1,
                    delay: shouldShowImages ? staggerDelay : 0,
                    opacity: { duration: 0.4, ease: "easeOut", delay: shouldShowImages ? staggerDelay : 0 },
                  }}
                  style={{ zIndex }}
                >
                  <div className="h-[160px] w-[100px] overflow-hidden rounded-lg">
                    <motion.img
                      src={imageUrl || "/placeholder.svg"}
                      alt={"预览 " + (imgIndex + 1)}
                      className="h-full w-full object-cover"
                      animate={{
                        filter: `brightness(${isActive ? Math.min(1, brightness + 0.2) : brightness}) contrast(1.08) saturate(${1 - distanceFromCenter * 0.2}) blur(${isActive ? 0 : blurAmount}px)`,
                      }}
                      transition={{
                        duration: TRANSITION_DURATION,
                        ease: TRANSITION_EASE,
                      }}
                    />
                  </div>
                </motion.div>
              )
            })}
          </motion.div>
        </motion.div>

        {/* Front panel */}
        <motion.div
          className="absolute bottom-0 left-0 right-0 z-10 rounded-2xl overflow-hidden"
          animate={{
            rotateX: isActive ? -25 : 0,
            backgroundColor: isGenerating ? "rgba(20, 20, 20, 0.85)" : "rgba(26, 26, 26, 0.8)",
          }}
          transition={{
            rotateX: {
              type: "spring",
              stiffness: 180,
              damping: 22,
              mass: 0.8,
            },
            backgroundColor: {
              duration: TRANSITION_DURATION,
              ease: TRANSITION_EASE,
            },
          }}
          style={{
            backdropFilter: "blur(16px)",
            WebkitBackdropFilter: "blur(16px)",
            border: "1px solid rgba(255, 255, 255, 0.06)",
            transformStyle: "preserve-3d",
            transformOrigin: "center bottom",
          }}
        >
          <div className="relative py-4 px-4">
            {renaming ? (
              <div data-rename className="relative z-10">
                <input
                  ref={inputRef}
                  value={draft}
                  maxLength={80}
                  placeholder="会议名称"
                  aria-label="会议名称"
                  onChange={(event) => setDraft(event.target.value)}
                  onClick={(event) => event.stopPropagation()}
                  onMouseDown={(event) => event.stopPropagation()}
                  onBlur={commitRename}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") { event.preventDefault(); commitRename() }
                    else if (event.key === "Escape") { event.preventDefault(); cancelRename() }
                  }}
                  className="w-full rounded-md bg-white/[0.06] px-2 py-1 text-base font-semibold text-white outline-none ring-1 ring-white/20 placeholder:text-white/30"
                />
              </div>
            ) : (
              <h3
                className={`font-semibold text-white/70 text-base leading-snug line-clamp-2 min-h-[2.75rem] relative z-0 transition-colors duration-300 ${!isGenerating ? "group-hover:text-white" : ""}`}
              >
                {project.title}
              </h3>
            )}
          </div>
          <div className="relative h-[48px]">
            {/* Top border */}
            <div className="absolute inset-x-0 top-0 h-[1px] bg-white/[0.04]" />
            
            {/* Progress bar - only show during active generation */}
            {isGenerating && progress < 100 && (
              <motion.div
                className="absolute top-0 left-0 h-[1px] bg-white/10"
                initial={{ width: "0%" }}
                animate={{ width: `${progress}%` }}
                transition={{ duration: 0.1, ease: "linear" }}
              />
            )}
            
            {/* Footer content - derive from showImages for reliability */}
            <div className="relative h-full">
              {isGenerating && !showImages ? (
                <motion.div
                  key="generating"
                  className="absolute inset-0 flex items-center justify-between px-2 pl-4"
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -6 }}
                  transition={{
                    type: "spring",
                    stiffness: 300,
                    damping: 30,
                    mass: 0.8,
                    opacity: { duration: 0.15 },
                  }}
                >
                  <div className="flex items-center gap-1.5">
                    <motion.svg
                      className="w-3 h-3 text-white/60"
                      viewBox="0 0 24 24"
                      fill="currentColor"
                      animate={{ rotate: 360 }}
                      transition={{ duration: 6, repeat: Infinity, ease: "linear" }}
                    >
                      <path d="M12 0L14 10L24 12L14 14L12 24L10 14L0 12L10 10L12 0Z" />
                    </motion.svg>
                    <span className="text-[13px] text-transparent bg-clip-text bg-gradient-to-r from-white/50 via-white/80 to-white/50 bg-[length:200%_100%] animate-shimmer">生成中</span>
                    {progress < 95 && <span className="text-[13px] text-white/50">{remainingEta}</span>}
                  </div>
                  {(onRemove || onRename) ? <MenuButton project={project} open={isMenuOpen} onOpenChange={setIsMenuOpen} onRemove={onRemove} onCancel={onCancel} onRename={undefined} isVisible={true} /> : null}
                </motion.div>
              ) : (
                <motion.div
                  key="default"
                  className="absolute inset-0 flex items-center justify-between px-2 pl-4"
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{
                    type: "spring",
                    stiffness: 120,
                    damping: 18,
                    mass: 1,
                    opacity: { duration: 0.35, ease: "easeOut" },
                  }}
                >
                  <div className="flex items-center gap-1.5">
                    <SlotNumber value={clipCount} isSpinning={false} />
                    <span className="text-[13px] text-white/60">{project.itemLabel || "片段"}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-white/50">{formattedDate}</span>
                    {(onRemove || onRename) ? <MenuButton project={project} open={isMenuOpen} onOpenChange={setIsMenuOpen} onRemove={onRemove} onCancel={onCancel} onRename={onRename ? beginRename : undefined} isVisible={true} /> : null}
                  </div>
                </motion.div>
              )}
            </div>
          </div>
        </motion.div>
      </div>

    </div>
  )
}
