import { makeId } from "./utils"

export const INITIAL_CONVERSATIONS = [
  {
    id: "c1",
    title: "会议证据",
    updatedAt: new Date().toISOString(),
    messageCount: 0,
    preview: "询问决定、承诺，以及它们背后的原话……",
    pinned: false,
    folder: "会议",
    messages: [],
  },
]

export const INITIAL_TEMPLATES = [
  {
    id: "t1",
    name: "追踪承诺",
    content: "追踪这个项目最近会议中做出的承诺。列出负责人、截止日期、当前状态，以及每次变更对应的原话。",
    snippet: "跟随一个承诺穿过多次会议，包括后续变更……",
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  },
  {
    id: "t2",
    name: "找回那一刻",
    content: "找出交付日期或范围被改变的那一刻。返回原话、说话人、时间戳和上下文。",
    snippet: "定位一个决定背后的原话和时间窗口……",
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  },
  {
    id: "t3",
    name: "未完成行动",
    content: "列出最近会议中未完成的行动项，按负责人分组，附上原话和截止日期。",
    snippet: "回看未完成的行动，以及它们是在哪里达成的……",
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  },
]

export const INITIAL_FOLDERS = [{ id: "f1", name: "会议" }]