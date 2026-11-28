export const REASONING_LEVELS = [
  { value: "", label: "默认", hint: "使用服务商默认；不发送 reasoning_effort" },
  { value: "low", label: "低", hint: "更快，推理更少" },
  { value: "medium", label: "中", hint: "均衡推理" },
  { value: "high", label: "高", hint: "最充分" },
]

export function reasoningIndex(value: string | undefined) {
  const index = REASONING_LEVELS.findIndex((level) => level.value === (value || ""))
  return index < 0 ? 0 : index
}

export function reasoningLevel(index: number) {
  return REASONING_LEVELS[Math.max(0, Math.min(REASONING_LEVELS.length - 1, index))]
}

export function reasoningSupported(config: any) {
  return String(config?.llm_provider || "local") === "custom"
}

export const MODEL_STORAGE_KEY = "meetingvar.models"

export function loadModels(currentModel?: string) {
  if (typeof window === "undefined") return []
  try {
    const parsed = JSON.parse(localStorage.getItem(MODEL_STORAGE_KEY) || "[]")
    const models = Array.isArray(parsed) ? parsed.filter((value): value is string => typeof value === "string" && Boolean(value.trim())) : []
    if (currentModel && !models.includes(currentModel)) models.unshift(currentModel)
    return [...new Set(models)]
  } catch {
    return currentModel ? [currentModel] : []
  }
}

export function saveModels(models: string[]) {
  try { localStorage.setItem(MODEL_STORAGE_KEY, JSON.stringify([...new Set(models.filter(Boolean))])) } catch { /* storage is optional */ }
}

export function contextLimit(config: any) {
  const api = String(config?.llm_provider || "local") === "custom"
  const value = api ? config?.llm_api_context_tokens : config?.llm_context_tokens
  return Math.max(4096, Number(value) || (api ? 500000 : 16384))
}

export const PROVIDER_PRESETS = [
  { id: "local", name: "本地模型", note: "本机 llama.cpp", base: "", model: "", group: "内置" },
  { id: "custom", name: "自定义 API", note: "OpenAI 兼容 API", base: "", model: "", group: "内置" },
  { id: "deepseek", name: "DeepSeek", note: "api.deepseek.com", base: "https://api.deepseek.com/v1", model: "deepseek-chat", group: "预设" },
  { id: "agnes", name: "Agnes", note: "agnes.ai", base: "https://api.agnes.ai/v1", model: "agnes-3.0-flash", group: "预设" },
]

export const PROVIDER_STORAGE_KEY = "meetingvar.provider"

export function inferProviderId(config: any) {
  if (String(config?.llm_provider || "local") === "local") return "local"
  const base = String(config?.llm_base_url || "").toLowerCase()
  if (base.includes("deepseek")) return "deepseek"
  if (base.includes("agnes")) return "agnes"
  return "custom"
}

export function loadProviderId(config: any) {
  const local = String(config?.llm_provider || "local") === "local"
  try {
    const saved = localStorage.getItem(PROVIDER_STORAGE_KEY)
    if (saved && PROVIDER_PRESETS.some((item) => item.id === saved)) {
      if (local ? saved === "local" : saved !== "local") return saved
    }
  } catch { /* storage is optional */ }
  return inferProviderId(config)
}

export function saveProviderId(id: string) {
  try { localStorage.setItem(PROVIDER_STORAGE_KEY, id) } catch { /* storage is optional */ }
}
