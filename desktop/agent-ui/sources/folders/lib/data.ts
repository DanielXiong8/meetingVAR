export interface Project {
  id: string
  title: string
  clipCount: number
  createdAt: string
  images: string[]
  itemLabel?: string
  /** Real timestamp label for session-backed cards (falls back to the demo's synthetic one). */
  dateLabel?: string
  isGenerating?: boolean
  progress?: number
  eta?: string
  isFailed?: boolean
}

export const projects: Project[] = [
  { id: "1", title: "会议纪要与决定", clipCount: 14, createdAt: "2026-11-19", images: ["/project-thumbs/meeting-notes.svg", "/project-thumbs/repository.svg", "/project-thumbs/roadmap.svg", "/project-thumbs/commitments.svg", "/project-thumbs/meeting-map.svg"] },
  { id: "2", title: "代码仓库讨论", clipCount: 6, createdAt: "2026-11-18", images: ["/project-thumbs/repository.svg", "/project-thumbs/meeting-map.svg", "/project-thumbs/meeting-notes.svg", "/project-thumbs/roadmap.svg", "/project-thumbs/commitments.svg"] },
  { id: "3", title: "路线图评审", clipCount: 5, createdAt: "2026-11-17", images: ["/project-thumbs/roadmap.svg", "/project-thumbs/commitments.svg", "/project-thumbs/meeting-notes.svg", "/project-thumbs/meeting-map.svg", "/project-thumbs/repository.svg"] },
  { id: "4", title: "承诺台账", clipCount: 8, createdAt: "2026-11-16", images: ["/project-thumbs/commitments.svg", "/project-thumbs/meeting-notes.svg", "/project-thumbs/roadmap.svg", "/project-thumbs/repository.svg", "/project-thumbs/meeting-map.svg"] },
  { id: "5", title: "证据地图", clipCount: 8, createdAt: "2026-11-15", images: ["/project-thumbs/meeting-map.svg", "/project-thumbs/repository.svg", "/project-thumbs/commitments.svg", "/project-thumbs/roadmap.svg", "/project-thumbs/meeting-notes.svg"] },
]
