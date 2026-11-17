const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const root = path.resolve(__dirname, '..')
const ts = require(path.join(root, 'desktop/agent-ui/node_modules/typescript'))
const bridgePath = path.join(root, 'desktop/agent-ui/lib/bridge.ts')
const source = fs.readFileSync(bridgePath, 'utf8')
const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2021 } }).outputText
const bridge = {}
let pending
const window = { pywebview: { api: { workspace_subscribe: () => new Promise(resolve => { pending = resolve }) } }, addEventListener() {} }
const context = { exports: bridge, window, globalThis: {}, setTimeout, clearTimeout, setInterval, clearInterval }
vm.runInNewContext(output, context, { filename: bridgePath })
const windowsA = ['C:', 'projects', 'A'].join(String.fromCharCode(92))
const windowsB = ['C:', 'projects', 'B'].join(String.fromCharCode(92))
assert.equal(bridge.sameProject(windowsA, 'c:/projects/a/'), true)
assert.equal(bridge.sameProject(windowsA, windowsB), false)
const partial = bridge.answerMetadata({ content: 'some answer', meta: { answer_status: 'partial', errors: [{ code: 'model_timeout', message: 'failed stage' }], coverage: { complete: false }, unsupported: ['claim'], reason: 'missing source' } })
assert.equal(partial.answer_status, 'partial')
assert.equal(partial.errors[0].code, 'model_timeout')
assert.equal(partial.unsupported[0], 'claim')
assert.equal(partial.coverage.complete, false)
assert.equal(partial.reason, 'missing source')
assert.equal(bridge.answerMetadata({ ok: true, content: 'not a receipt' }).answer_status, undefined)
const issuePath = path.join(root, 'desktop/agent-ui/lib/workspace-issues.ts')
const issueOutput = ts.transpileModule(fs.readFileSync(issuePath, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2021 },
}).outputText
const issueModule = {}
vm.runInNewContext(issueOutput, { exports: issueModule, module: { exports: issueModule } }, { filename: issuePath })
const grouped = issueModule.summarizeWorkspaceIssues([
  'ValueError: unsupported_or_unreadable',
  { code: 'extraction_failed', message: 'unsupported_or_unreadable', path: 'a.doc', stage: 'extract' },
  { code: 'extraction_failed', message: 'unsupported_or_unreadable', path: 'a.doc', stage: 'extract' },
  { code: 'ValueError', message: 'unsupported_or_unreadable', path: 'b.doc' },
  // Real backend shape: code carries the reason, error carries the class name.
  { code: 'unsupported_or_unreadable', error: 'ValueError', path: 'x.doc' },
  { code: 'extraction_failed', message: 'unsupported_or_unreadable', path: 'x.doc' },
  { code: 'time_limit', path: 'c.pdf', page: 2 },
])
assert.equal(grouped.count, 4, 'pathless duplicates must not inflate the per-source count')
assert.deepEqual(Array.from(grouped.groups, (row) => row.label).sort(), ['格式不支持或无法读取', '读取超时'])
assert.equal(grouped.groups.some((row) => row.label === '文件无法读取'), false, 'a class name must not create a duplicate group')
assert.equal(Array.from(grouped.groups).find((row) => row.label === '格式不支持或无法读取').count, 3)
// Exact production shape: each unreadable file appears once as a generic
// ValueError and once with the specific reason; the file must count once.
const realShaped = issueModule.summarizeWorkspaceIssues(
  ['a.doc', 'b.doc', 'c.doc', 'd.doc', 'e.doc', 'f.doc'].flatMap((file) => [
    { code: 'extraction_incomplete', error: 'ValueError', path: file },
    { code: 'extraction_failed', message: 'unsupported_or_unreadable', path: file },
  ]),
)
assert.equal(realShaped.count, 6)
assert.deepEqual(Array.from(realShaped.groups, (row) => row.label), ['格式不支持或无法读取'])
assert.equal(realShaped.groups[0].count, 6)
const pathlessOnly = issueModule.summarizeWorkspaceIssues([
  { code: 'ValueError', error: 'ValueError' },
  { code: 'ValueError', error: 'ValueError' },
])
assert.equal(pathlessOnly.count, 1, 'repeated pathless errors represent one blocker, not two sources')
assert.equal(pathlessOnly.groups[0].label, '文件无法读取')
const mixed = issueModule.summarizeWorkspaceIssues([
  { code: 'ocr_failed', path: 'scan.pdf' },
  { code: 'output_budget_exhausted', message: 'budget stop' },
  { code: 'output_budget_exhausted', message: 'budget stop', stage: 'explore' },
  { code: 'auth', message: 'provider rejected credentials' },
])
assert.equal(mixed.sourceCount, 1)
assert.equal(mixed.blockerCount, 2)
assert.equal(mixed.count, 3)
assert.deepEqual(Array.from(mixed.groups, (row) => row.label), ['OCR 识别失败', '本次探索输出预算已用尽', '模型服务鉴权失败'])
assert.equal(mixed.groups[1].kind, 'blocker')
assert.equal(issueModule.friendlyWorkspaceIssue({ code: 'ValueError', stage: 'explore', message: '探索检查点身份或来源版本不匹配' }).label, '探索检查点身份或来源版本不匹配')
assert.equal(grouped.groups.some((row) => /ValueError/.test(row.label)), false)
const page = fs.readFileSync(path.join(root, 'desktop/agent-ui/app/page.tsx'), 'utf8')
for (const command of ['understand_start', 'understand_status', 'understand_plan', 'workspace_sync', 'rag_index']) {
  assert.equal(page.includes(`call('${command}'`), false, `UI must not orchestrate ${command}`)
}
assert.equal(page.includes('Math.max(Number(rag'), false)
assert.ok(page.includes('answerMetadata(reply)'))
assert.ok(page.includes('item.answer_status'))
assert.ok(page.includes('result.extracted'))
assert.equal(page.includes('result.summarized'), false)
assert.ok(page.includes('result.map_totals'))
assert.ok(page.includes('result.coverage?.explore'))
assert.ok(page.includes("state.status === 'oriented'"))
assert.ok(page.includes("state.status === 'paused_budget'"))
assert.ok(page.includes("state.status === 'mapped_awaiting_model'"))
assert.equal(page.includes('本轮各阶段已完成'), false)
assert.equal(page.includes('本轮阶段已完成'), false)
assert.ok(page.includes('关键认知是带来源的暂定理解'))
assert.ok(page.includes('ArchitecturePanel'))
assert.ok(page.includes("call('architecture_generate'"), 'AI architecture generation must use the backend bridge')
assert.ok(page.includes("call('architecture_save'"), 'manual modules and owners must persist through the backend')
assert.ok(page.includes('AI 生成架构'))
assert.ok(page.includes('添加模块'))
assert.ok(page.includes('负责人'))
assert.ok(page.includes("['项目架构', '架构']"))
assert.ok(page.includes('await startAutoUnderstand(currentProject, null, true)'), 'explicit refresh starts a new investigation')
assert.ok(page.includes('void startAutoUnderstand(currentProject, null, false)'), 'periodic polls never force new model budget')
// Workspace status is icon-only until the user opens it.
assert.ok(page.includes("event?.type === 'partial_line'"), 'stream partial captions must be consumed')
assert.ok(page.includes('live-captions'), 'live captions must have a visible block')
assert.ok(page.includes('auto-project-fab'))
assert.ok(page.includes('const [open, setOpen] = useState(false)'))
assert.equal(page.includes('<AutoProjectCard state={autoProject} defaultOpen'), false)
assert.ok(page.includes('<AutoProjectCard key={currentProject} state={autoProject}'))
assert.ok(page.includes('useEffect(() => { setOpen(false) }, [state?.folder])'))
assert.equal(page.includes('setOpen(true) }, [state'), false, 'status changes never reopen the card')
// Expected unreadable files are grouped by the tested helper, not dumped raw.
assert.ok(page.includes("@/lib/workspace-issues"))
assert.ok(page.includes('summarizeWorkspaceIssues(result.errors)'))
assert.ok(page.includes('原始状态（调试）'))
assert.equal(page.includes("summary>{JSON.stringify({ coverage: result.coverage || {}, errors: result.errors || [] }, null, 2)}</pre></details>"), false)
// Render only the workspace component with a minimal hook/JSX harness. This
// checks click/poll/project transitions without booting a browser or user app.
const cardSource = page.slice(page.indexOf('const AUTO_STAGE_LABELS'), page.indexOf('function ThinkingCard'))
const cardOutput = ts.transpileModule(cardSource + '\nexports.AutoProjectCard = AutoProjectCard', {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2021, jsx: ts.JsxEmit.React },
}).outputText
const hookState = [], effectState = []
let hookIndex = 0, effectIndex = 0, effects = []
const cardModule = {}
const icons = Object.fromEntries(['RefreshCw', 'Sparkles', 'ChevronDown'].map((name) => [name, name]))
vm.runInNewContext(cardOutput, {
  exports: cardModule, ...icons, summarizeWorkspaceIssues: issueModule.summarizeWorkspaceIssues,
  React: { createElement: (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity) }) },
  useState(initial) {
    const slot = hookIndex++
    if (!(slot in hookState)) hookState[slot] = initial
    return [hookState[slot], (value) => { hookState[slot] = typeof value === 'function' ? value(hookState[slot]) : value }]
  },
  useEffect(callback, dependencies) {
    const slot = effectIndex++
    if (!effectState[slot] || dependencies.some((value, index) => value !== effectState[slot][index])) {
      effectState[slot] = Array.from(dependencies)
      effects.push(callback)
    }
  },
})
function renderCard(state) {
  hookIndex = 0; effectIndex = 0; effects = []
  let tree = cardModule.AutoProjectCard({ state, onRefresh() {} })
  if (effects.length) {
    effects.forEach((effect) => effect())
    hookIndex = 0; effectIndex = 0; effects = []
    tree = cardModule.AutoProjectCard({ state, onRefresh() {} })
  }
  return tree
}
function elements(tree) {
  if (!tree || typeof tree !== 'object') return []
  return [tree, ...tree.children.flatMap(elements)]
}
const stateA = { folder: 'C:/projects/A', status: 'running', result: { stages: {}, errors: [] } }
let tree = renderCard(stateA)
assert.ok(elements(tree).some((row) => row.props.className === 'auto-project-fab-row'))
elements(tree).find((row) => row.props['aria-label'] === '打开工作区同步详情').props.onClick()
tree = renderCard(stateA)
assert.ok(tree.props.className.includes('expanded'))
tree = renderCard({ ...stateA, status: 'paused_budget', result: { errors: [{ code: 'output_budget_exhausted' }] } })
assert.ok(tree.props.className.includes('expanded'), 'polling must preserve explicit open choice')
elements(tree).find((row) => row.props.className === 'auto-project-toggle').props.onClick()
tree = renderCard({ ...stateA, status: 'oriented' })
assert.equal(tree.props.className, 'auto-project-fab-row', 'completion must not reopen a collapsed card')
elements(tree).find((row) => row.props['aria-label'] === '打开工作区同步详情').props.onClick()
renderCard(stateA)
tree = renderCard({ ...stateA, folder: 'C:/projects/B' })
assert.equal(tree.props.className, 'auto-project-fab-row', 'project switch resets to icon-only')
;(async () => {
  let delivered = 0
  const stop = bridge.subscribeWorkspace('C:/projects/A', () => { delivered++ }, error => { throw error })
  await Promise.resolve()
  await Promise.resolve()
  stop()
  pending({ ok: true, project: 'C:/projects/A', job_id: 'a1' })
  await Promise.resolve()
  await Promise.resolve()
  assert.equal(delivered, 0, 'unsubscribing after A/B switch must suppress stale project responses')
  console.log('Workspace UI contract checks passed: project identity, partial/errors/coverage metadata, no frontend pipeline, cancelled subscription')
})().catch(error => { console.error(error); process.exitCode = 1 })
