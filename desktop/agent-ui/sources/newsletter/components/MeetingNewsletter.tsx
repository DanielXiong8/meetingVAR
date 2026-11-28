"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { ArrowRightIcon } from "@radix-ui/react-icons"
import { AnimatePresence, motion } from "framer-motion"
import { Button, buttonVariants } from "./ui/button"
import { inputVariants } from "./ui/input"
import { cn } from "../lib/utils"

const DURATION = 0.3
const DELAY = DURATION
const EASE_OUT = "easeOut"
const EASE_OUT_OPACITY = [0.25, 0.46, 0.45, 0.94] as const
const SPRING = { type: "spring" as const, stiffness: 60, damping: 10, mass: 0.8 }

export const Newsletter = () => {
  const [isOpen, setIsOpen] = useState(false)
  const [name, setName] = useState("")
  const router = useRouter()
  function enter(e: React.FormEvent) {
    e.preventDefault()
    sessionStorage.setItem("fusion-meeting", name.trim() || "Meeting evidence")
    router.push("/workspace")
  }
  return <div className="flex overflow-hidden relative flex-col gap-4 justify-center items-center pt-10 w-full h-full short:lg:pt-10 pb-footer-safe-area 2xl:pt-footer-safe-area px-sides short:lg:gap-4 lg:gap-8">
    <motion.div layout="position" transition={{ duration: DURATION, ease: EASE_OUT }}><h1 className="font-serif text-6xl italic short:lg:text-8xl sm:text-8xl lg:text-9xl text-foreground">meetingVAR</h1></motion.div>
    <div className="flex flex-col items-center min-h-0 shrink w-full"><AnimatePresence mode="popLayout" propagate>
      {!isOpen && <motion.div key="newsletter" initial="hidden" animate="visible" exit="exit" variants={{ visible: { scale: 1, transition: { delay: DELAY, duration: DURATION, ease: EASE_OUT } }, hidden: { scale: 0.9, transition: { duration: DURATION, ease: EASE_OUT } }, exit: { y: -150, scale: 0.9, transition: { duration: DURATION, ease: EASE_OUT } } }}>
        <div className="flex flex-col gap-4 w-full max-w-xl md:gap-6 lg:gap-8">
          <form onSubmit={enter} className="relative w-[min(576px,calc(100vw-2rem))]">
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: DURATION, ease: EASE_OUT, delay: DELAY }}>
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name this workspace" aria-label="Workspace name" className={inputVariants()} />
            </motion.div>
            <motion.button type="submit" aria-label="Enter meeting workspace" className={cn(buttonVariants({ variant: "iconButton", size: "icon-xl" }), "absolute right-0 top-0")} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: DURATION, ease: EASE_OUT, delay: DELAY }}><ArrowRightIcon className="w-4 h-4 text-current" /></motion.button>
          </form>
          <motion.p initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: DURATION, ease: EASE_OUT, delay: DELAY }} className="text-base short:lg:text-lg sm:text-lg lg:text-xl !leading-[1.1] font-medium text-center text-foreground text-pretty">Meetings, remembered beautifully.<br />Every decision has a source.</motion.p>
        </div>
      </motion.div>}
      <motion.div layout="position" transition={SPRING} key="button" className={isOpen ? "my-6" : "mt-6"}><Button className="relative px-8" onClick={() => setIsOpen(!isOpen)} shine={!isOpen}><motion.span animate={{ x: isOpen ? -16 : 0 }} transition={{ duration: DURATION, ease: EASE_OUT }} className="inline-block">How it works</motion.span>{isOpen && <motion.div className={cn(buttonVariants({ variant: "iconButton", size: "icon" }), "absolute -top-px -right-px aspect-square")} initial={{ opacity: 0, scale: 0.8, rotate: -40 }} animate={{ opacity: 1, scale: 1, rotate: 0 }} transition={{ duration: DURATION, ease: EASE_OUT, delay: DELAY }}><span className="text-xl">×</span></motion.div>}</Button></motion.div>
      {isOpen && <motion.div key="manifesto" initial="hidden" animate="visible" exit="exit" variants={{ visible: { opacity: 1, scale: 1, transition: { delay: DELAY, duration: DURATION, ease: EASE_OUT } }, hidden: { opacity: 0, scale: 0.9, transition: { duration: DURATION, ease: EASE_OUT } }, exit: { opacity: 0, scale: 0.9, transition: { duration: DURATION, ease: EASE_OUT_OPACITY } } }} className="relative flex min-h-0 flex-shrink overflow-hidden text-sm md:text-base max-h-[calc(70dvh-var(--footer-safe-area))] flex-col gap-8 text-center backdrop-blur-xl text-balance border-2 border-border/50 bg-primary/20 max-w-3xl text-foreground rounded-3xl ring-1 ring-offset-primary/10 ring-border/10 ring-offset-2 shadow-button"><article className="relative overflow-y-auto italic p-6 h-full [&_p]:my-4"><p>Ask a meeting why a decision changed. Follow a promise across versions. Return to the exact words and the moment they were spoken.</p><p>meetingVAR keeps meeting evidence local. Start with a project, capture only the moments that matter, and open every answer beside its source.</p><p>Enter a workspace name above, or leave it blank to begin. Use the meeting library for saved clips and the evidence buttons for verification.</p></article></motion.div>}
    </AnimatePresence></div>
  </div>
}
