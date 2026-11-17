// Workspace diagnostics keep source failures and operation blockers separate.
// Repeated polls / stage receipts must not inflate counts or hide model failures.
export const WORKSPACE_ERROR_LABELS: Record<string, string> = {
  unsupported_or_unreadable: '格式不支持或无法读取',
  extraction_failed: '文本提取失败',
  extraction_incomplete: '提取未完成',
  source_incomplete: '来源提取未完整',
  source_unreadable: '文件无法读取',
  source_too_large: '文件过大',
  byte_limit: '超出单次读取上限',
  page_limit: '超出页数上限',
  time_limit: '读取超时',
  source_changed_during_read: '读取期间文件发生变化',
  source_changed_during_job: '资料持续变化，调查已暂停',
  source_stale: '来源已变化，旧认知不再有效',
  pdf_open_failed: 'PDF 无法打开',
  incomplete_extraction: '提取未完成',
  source_unverified: '来源无法核验',
  ocr_failed: 'OCR 识别失败',
  parse_failed: '代码结构解析失败',
  awaiting_model: '等待显式配置的模型',
  model_context_required: '模型配置尚未就绪',
  not_configured: '尚未配置模型',
  invalid_config: '模型配置无效',
  auth: '模型服务鉴权失败',
  quota: '模型服务额度不足',
  model_not_found: '配置的模型不可用',
  network: '模型服务连接失败',
  timeout: '模型服务响应超时',
  rate_limit: '模型服务限流',
  input_budget_exhausted: '本次探索输入预算已用尽',
  output_budget_exhausted: '本次探索输出预算已用尽',
  time_budget_exhausted: '本次探索时间预算已用尽',
  step_budget_exhausted: '本次探索步骤预算已用尽',
  tool_budget_exhausted: '本次探索读取预算已用尽',
  input_too_large: '当前证据超过模型上下文上限',
  exploration_incomplete: '关键认知尚未建立',
  invalid_explorer_action: '模型未返回有效探索动作',
  claims_rejected: '部分认知待补原文依据',
  map_unavailable: '项目地图尚不可用',
  map_busy: '项目地图由另一任务更新中',
  index_busy: '本地索引由另一任务更新中',
  index_incomplete: '本地索引尚未完整提交',
  launch_failed: '后台调查启动失败',
  ValueError: '文件无法读取',
}

export type WorkspaceIssue = { label: string; path: string }

export function friendlyWorkspaceIssue(raw: any): WorkspaceIssue {
  if (typeof raw === 'string') {
    const match = raw.match(/^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$/)
    const code = match?.[1] || ''
    const message = (match?.[2] || raw).trim()
    return { label: WORKSPACE_ERROR_LABELS[message] || WORKSPACE_ERROR_LABELS[code] || message || code || '未知问题', path: '' }
  }
  const code = String(raw?.code || '')
  const message = String(raw?.message || raw?.error || '')
  const candidates = [code, message, String(raw?.error || '')].filter(Boolean)
  const generic = new Set(['ValueError', 'extraction_failed', 'extraction_incomplete'])
  const specific = candidates.find((value) => WORKSPACE_ERROR_LABELS[value] && !generic.has(value))
  const fallback = candidates.find((value) => WORKSPACE_ERROR_LABELS[value])
  const label = specific ? WORKSPACE_ERROR_LABELS[specific]
    : code === 'ValueError' && message && message !== 'ValueError' ? message
      : fallback ? WORKSPACE_ERROR_LABELS[fallback]
        : message || code || '未知问题'
  return { label, path: String(raw?.path || '') }
}

export function summarizeWorkspaceIssues(errors: any) {
  const groups = new Map<string, { paths: Set<string>; anonymous: boolean }>()
  for (const raw of Array.isArray(errors) ? errors : []) {
    const { label, path } = friendlyWorkspaceIssue(raw)
    if (!label) continue
    const group = groups.get(label) || { paths: new Set<string>(), anonymous: false }
    if (path) group.paths.add(path)
    else group.anonymous = true
    groups.set(label, group)
  }
  const genericLabels = new Set(['文件无法读取', '文本提取失败', '提取未完成', '来源提取未完整', '未知问题'])
  const specificPaths = new Set<string>()
  for (const [label, group] of groups) {
    if (!genericLabels.has(label)) group.paths.forEach((path) => specificPaths.add(path))
  }
  for (const [label, group] of groups) {
    if (!genericLabels.has(label)) continue
    for (const path of Array.from(group.paths)) {
      if (specificPaths.has(path)) group.paths.delete(path)
    }
  }
  const visible = Array.from(groups).filter(([, group]) => group.paths.size > 0 || group.anonymous)
  const sourceCount = new Set(visible.flatMap(([, group]) => Array.from(group.paths))).size
  // A pathless duplicate of a per-source error is redundant. A distinct
  // pathless blocker (budget/auth/model/map) is not: never hide it behind OCR.
  const blockers = visible.filter(([label, group]) => group.paths.size === 0 && group.anonymous
    && !(sourceCount && genericLabels.has(label)))
  const rows = visible.filter(([, group]) => group.paths.size > 0 || blockers.some(([, item]) => item === group))
  return {
    count: sourceCount + blockers.length,
    sourceCount,
    blockerCount: blockers.length,
    groups: rows.map(([label, group]) => ({
      label,
      paths: Array.from(group.paths),
      count: group.paths.size || 1,
      kind: group.paths.size ? 'source' as const : 'blocker' as const,
    })),
  }
}
