import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import type { IncrementalDetails, IncrementalRun, Onboarding } from '../src/services/contracts'
import { counts, routingCategoryLabel, stageLabel, stateLabel } from '../src/services/contracts'
import {
  createIncrementalClient, incrementalConfirmation, incrementalControlLabel, incrementalNavigationDetail, incrementalRoute,
  incrementalRunLabel, incrementalStateLabel, onboardingCompleted, onboardingRecoveryTask, onboardingStageNames, onboardingStages, incrementalDeltaText,
} from '../src/services/incremental'

const completed: Onboarding = {
  stage: 'completed', state: 'completed', completed_at: '2026-10-14T01:00:00Z',
  acquisition: { done: 8, failed: 1 }, summary: { audit: { processed: 3, position_count: 4, passed: true } },
}
const failed: IncrementalRun = {
  id: 'run-1', status: 'blocked', started_at: '2026-10-14T02:00:00Z', finished_at: '2026-10-14T02:01:00Z',
  summary: { delta: { state: 'ready', reason: null, notice_count: 2, position_count: 3,
    new_notices: 1, changed_notices: 1 }, error: { code: 'AUDIT_NOT_READY' } },
  error: { code: 'FETCH_FAILED' },
}

test('completed is a terminal onboarding stage, never an incremental timeline step', () => {
  assert.equal(onboardingStages.at(-1), 'completed')
  assert.equal(onboardingStageNames.at(-1), '接入完成')
  assert.equal(stageLabel('completed'), '接入完成')
  assert.equal(stageLabel('active'), '接入完成')
  assert.equal(stateLabel('completed'), '已完成')
  assert.equal(onboardingCompleted(completed), true)
  for (const stage of ['not_started', 'inventory', 'classification', 'authoring', 'audit', 'active']) {
    assert.equal(onboardingCompleted({ stage, state: 'completed' }), false)
  }
  for (const state of ['not_started', 'ready', 'running', 'blocked']) {
    assert.equal(onboardingCompleted({ stage: 'completed', state }), false)
  }
  const maintaining: Onboarding = { ...completed, maintenance: { stage: 'audit', state: 'blocked', error: { code: 'REPAIR_FAILED' } } }
  assert.equal(onboardingCompleted(maintaining), true)
})

test('navigation defaults off and handles disabled in-flight state without claiming completion', () => {
  assert.match(incrementalNavigationDetail(), /默认未开启/)
  assert.match(incrementalNavigationDetail({ enabled: false, state: 'running' }), /已关闭.*批次处理中/)
  assert.match(incrementalNavigationDetail({ enabled: true, state: 'blocked' }), /已开启.*阻塞/)
  assert.equal(incrementalStateLabel('idle'), '等待下一批次')
  assert.equal(incrementalStateLabel('unexpected'), '未知状态')
  assert.equal(incrementalRoute('source/a?b'), '/pages/admin/incremental/index?id=source%2Fa%3Fb')
})

test('confirmations distinguish future batches, in-flight completion, cost and preserved history', () => {
  assert.match(incrementalConfirmation(true).content, /接入完成.*模型费用.*清除阻塞.*保留历史.*冻结/)
  assert.match(incrementalConfirmation(false).content, /暂停所有后续有界处理轮次.*同一运行周期.*当前已开始的小批次会安全结束.*重新开启后续跑.*不代表取消.*历史记录会保留/)
  assert.equal(incrementalRunLabel('failed'), '失败')
  assert.equal(incrementalRunLabel('running'), '处理中')
  assert.match(incrementalRunLabel('unrecognized'), /待核验/)
  assert.match(incrementalDeltaText(failed.summary.delta), /仅含本轮新增.*不含未变化公告/)
})

test('incremental requests use their own endpoint and send only the explicit boolean', async () => {
  const calls: { path: string; data?: Record<string, unknown> }[] = []
  const running: IncrementalRun = { ...failed, id: 'run-2', status: 'running', finished_at: null }
  const runs = [running, failed]
  const response: IncrementalDetails = { enabled: false, state: 'running', latest_run: running, runs }
  const frozen = JSON.stringify(completed)
  const client = createIncrementalClient(async <T>(path: string, data?: Record<string, unknown>) => {
    calls.push({ path, data })
    return response as T
  })
  assert.equal(await client.read('source/a'), response)
  assert.equal(await client.setEnabled('source/a', true, completed), response)
  // Disabling remains possible even if onboarding is unfinished or maintenance blocks the source.
  const result = await client.setEnabled('source/a', false, { stage: 'audit', state: 'blocked' })
  assert.equal(result.state, 'running')
  assert.equal(result.latest_run?.finished_at, null)
  assert.equal(result.runs, runs)
  assert.equal(result.runs[1].summary.delta.position_count, 3)
  assert.equal(result.runs[1].error?.code, 'FETCH_FAILED')
  assert.equal(JSON.stringify(completed), frozen)
  assert.deepEqual(calls, [
    { path: '/admin/sources/source%2Fa/incremental', data: undefined },
    { path: '/admin/sources/source%2Fa/incremental', data: { enabled: true } },
    { path: '/admin/sources/source%2Fa/incremental', data: { enabled: false } },
  ])
})

test('unfinished sources cannot enable or start batches; empty identity never issues a request', () => {
  let calls = 0
  const client = createIncrementalClient(async <T>() => { calls++; return {} as T })
  assert.throws(() => client.setEnabled('x', true, { stage: 'audit', state: 'blocked' }), /接入尚未完成/)
  assert.throws(() => client.setEnabled('x', true, { stage: 'completed', state: 'running' }), /接入尚未完成/)
  assert.throws(() => client.read(''), /缺少来源标识/)
  assert.throws(() => client.setEnabled(' ', false, completed), /缺少来源标识/)
  assert.equal(calls, 0)
})

test('GET and POST preserve independent recent-20 history, nullable latest result and errors', async () => {
  const runs = Array.from({ length: 20 }, (_, i) => ({ ...failed, id: `run-${20 - i}` }))
  const blocked: IncrementalDetails = { enabled: true, state: 'blocked', latest_run: null, error: { code: 'WORKER_FAILED' }, runs }
  const empty: IncrementalDetails = { enabled: false, state: 'disabled', runs: [] }
  let value = blocked
  const client = createIncrementalClient(async <T>() => value as T)
  assert.equal(await client.read('x'), blocked)
  assert.equal((await client.setEnabled('x', true, completed)).runs, runs)
  assert.equal(blocked.error?.code, 'WORKER_FAILED')
  value = empty
  assert.equal(await client.read('x'), empty)
  assert.equal(empty.latest_run, undefined)
})

test('incremental page has a native title, registered admin route and confirmed permission-gated control', () => {
  const config = readFileSync('src/pages/admin/incremental/index.config.ts', 'utf8')
  assert.match(config, /navigationBarTitleText: '增量抓取'/)
  assert.doesNotMatch(config, /navigationStyle: 'custom'/)
  assert.match(readFileSync('src/app.config.ts', 'utf8'), /'pages\/admin\/incremental\/index'/)
  const page = readFileSync('src/pages/admin/incremental/index.tsx', 'utf8')
  assert.match(page, /<AdminGate/)
  assert.match(page, /state\.mutate/)
  assert.match(page, /Taro\.showModal\(incrementalConfirmation\(enabled\)\)/)
  assert.match(page, /if \(!answer\.confirm\) return false\s+await client\.setEnabled/)
  assert.match(page, /runs\.map/)
  assert.doesNotMatch(page, /<Steps|summary\.audit.*source|source\.onboarding\.summary/)
  const status = readFileSync('src/pages/admin/status/index.tsx', 'utf8')
  assert.match(status, /incrementalRoute\(source\.id\)/)
  assert.match(status, /onboardingRecoveryTask\(source\.onboarding\)/)
  assert.match(status, /\{\(blockedTask \|\| recovery\?\.can_repair\) &&/)
  assert.match(status, /\/resume/)
  assert.match(status, /\/repair/)
  assert.match(status, /source\.onboarding\.completed_at/)
})


test('disabled running records show paused-until-enabled without rewriting run history', () => {
  const run: IncrementalRun = { ...failed, status: 'running', finished_at: null }
  const control: IncrementalDetails = { enabled: false, state: 'disabled', latest_run: run, runs: [run] }
  const before = JSON.stringify(control)
  assert.match(incrementalControlLabel(control), /已暂停.*重新开启/)
  assert.match(incrementalNavigationDetail(control), /已关闭.*已暂停.*重新开启/)
  assert.match(incrementalRunLabel(run.status, control.enabled), /已暂停.*重新开启/)
  assert.equal(incrementalRunLabel(run.status, true), '处理中')
  assert.equal(incrementalRunLabel('complete', false), '已完成')
  assert.equal(incrementalRunLabel('failed', false), '失败')
  assert.equal(JSON.stringify(control), before)
  const inactiveMaintenance: Onboarding = { ...completed, maintenance: null }
  assert.equal(onboardingCompleted(inactiveMaintenance), true)
  const page = readFileSync('src/pages/admin/incremental/index.tsx', 'utf8')
  assert.match(page, /当前已开始的小批次会安全结束.*同一运行周期内的后续有界轮次也已暂停/)
  assert.match(page, /不代表取消/)
  assert.match(page, /增量控制错误（不影响接入完成结果）/)
})

test('incremental page reads paginated notice differences, not global audit/resource totals', async () => {
  const calls: string[] = []
  const client = createIncrementalClient(async <T>(path: string) => { calls.push(path); return {} as T })
  await client.run('source/a', 'run-1', 2)
  assert.deepEqual(calls, ['/admin/sources/source%2Fa/incremental/runs/run-1?page=2'])
  for (const value of ['', '../other', 'bad?query']) assert.throws(() => client.run('source', value), /批次/)
  assert.throws(() => client.run('source', 'run-1', 0), /分页/)
  assert.equal(calls.length, 1)
  const page = readFileSync('src/pages/admin/incremental/index.tsx', 'utf8')
  assert.match(page, /client\.run\(id, runId, page\)/)
  assert.match(page, /detail\.notices\.items\.map/)
  assert.match(page, /notice\.positions\.map/)
  assert.match(page, /notice\.positions_truncated/)
  assert.match(page, /notice\.positions_unavailable/)
  assert.match(page, /Taro\.setClipboardData\(\{ data: notice.url \}\)/)
  assert.doesNotMatch(page, /summary\.audit|resource_changes|new_resources|<Card/)
  assert.match(page, /仅展示本批次变化，关联岗位不代表新增空缺/)
})

test('incremental pending/missing comparisons remain unknown, not zero-change assertions', () => {
  assert.match(incrementalDeltaText({ ...failed.summary.delta, state: 'pending' }), /尚在处理中.*不是完整结果/)
  assert.match(incrementalDeltaText({ ...failed.summary.delta, state: 'unavailable', notice_count: null, position_count: null }), /不能据此判断没有增量/)
  assert.match(incrementalDeltaText({ ...failed.summary.delta, notice_count: 0, position_count: 0 }), /未发现新增或材料变化/)
})

test('blocked maintenance retains shared recovery actions without unfreezing admitted onboarding', () => {
  const admitted: Onboarding = {
    ...completed, summary: { ...completed.summary, error: { code: 'HISTORICAL_ERROR' } },
    maintenance: { stage: 'authoring', state: 'blocked', error: { code: 'WORKER_INTERRUPTED' } },
    recovery: { limits: { max_depth: 100, max_resources: 4000 }, suggested_limits: {},
      blocking_limits: [], depth_limited_resources: 0, can_resume: true, can_repair: true, requires_operator: false },
  }
  const before = JSON.stringify(admitted)
  assert.equal(onboardingCompleted(admitted), true)
  assert.deepEqual(onboardingRecoveryTask(admitted), {
    blockedTask: true, label: '维护处理', errorCode: 'WORKER_INTERRUPTED', resumeTitle: '恢复维护任务',
  })
  const noMaintenanceCode: Onboarding = { ...admitted, maintenance: { stage: 'authoring', state: 'blocked', error: { code: null } } }
  assert.equal(onboardingRecoveryTask(noMaintenanceCode).errorCode, undefined)
  for (const state of ['ready', 'running', 'completed']) {
    const resumed: Onboarding = { ...admitted, maintenance: { stage: 'authoring', state } }
    assert.equal(onboardingRecoveryTask(resumed).blockedTask, false)
    assert.equal(onboardingCompleted(resumed), true)
  }
  assert.equal(onboardingRecoveryTask({ ...completed, maintenance: null }).blockedTask, false)
  const unfinished: Onboarding = { ...completed, stage: 'authoring', state: 'blocked', maintenance: null,
    summary: { error: { code: 'INIT_FAILED' } } }
  assert.deepEqual(onboardingRecoveryTask(unfinished), {
    blockedTask: true, label: '阻塞处理', errorCode: 'INIT_FAILED', resumeTitle: '恢复接入任务',
  })
  assert.equal(JSON.stringify(admitted), before)
  const status = readFileSync('src/pages/admin/status/index.tsx', 'utf8')
  const actions = status.slice(status.indexOf('{(blockedTask || recovery?.can_repair) &&'), status.indexOf("<AdminPanel title='解析版本'"))
  assert.match(actions, /recoveryTask\?\.label/)
  assert.match(actions, /blockedExplanation\(recoveryTask\?\.errorCode, recovery\)/)
  assert.match(actions, /recovery\?\.can_resume/)
  assert.match(actions, /recovery\?\.can_repair/)
  assert.match(actions, /\/resume/)
  assert.match(actions, /\/repair/)
  assert.doesNotMatch(actions, /!completed/)
})


test('source overview delegates dense onboarding records to a native-title detail page', () => {
  const status = readFileSync('src/pages/admin/status/index.tsx', 'utf8')
  const detail = readFileSync('src/pages/admin/onboarding-detail/index.tsx', 'utf8')
  const config = readFileSync('src/pages/admin/onboarding-detail/index.config.ts', 'utf8')
  assert.match(status, /pages\/admin\/onboarding-detail\/index\?id=\$\{encodeURIComponent\(source\.id\)\}/)
  assert.match(detail, /接入基线与摘要已冻结.*维护和增量批次变化/)
  assert.doesNotMatch(status, /onboardingStages\.map|<Metric|cheap_coverage|audit\?\.statuses|recovery\.limits|authoring_job/)
  assert.match(config, /navigationStyle: 'default', navigationBarTitleText: '接入详情'/)
  assert.match(detail, /<AdminGate/)
  assert.match(detail, /useAdminData/)
  assert.match(detail, /onboardingStages\.map/)
  assert.match(detail, /接入摘要（已冻结）/)
  assert.match(detail, /baseline_recipe_id/)
  assert.match(detail, /baseline_audit_id/)
  assert.match(detail, /cheap_coverage[\s\S]*不是准确率/)
  for (const field of ['statuses', 'routing_categories', 'extraction_paths', 'field_issues_by_code', 'coverage_issues_by_code', 'errors_by_code', 'warnings_by_code']) {
    assert.ok(detail.includes(`counts(audit?.${field})`))
  }
  assert.match(detail, /counts\(source\.onboarding\.acquisition\)/)
  assert.match(detail, /recovery\.limits\.max_depth/)
  assert.match(detail, /recovery\.limits\.max_resources/)
  assert.match(detail, /authoring_job\.id/)
  assert.match(detail, /repair\.task_id/)
  assert.match(detail, /接入后维护（独立状态）/)
  assert.match(detail, /增量批次.*独立记录/)
  assert.match(detail, /className='source-data-value' selectable/)
  assert.doesNotMatch(status + detail, /navigateBack|back-button|<Steps/)
  assert.doesNotMatch(detail, /\/resume|\/repair|showModal/)
})

test('source recipe rows separate current and candidate states with short IDs and full-ID copy', () => {
  const status = readFileSync('src/pages/admin/status/index.tsx', 'utf8')
  assert.match(status, /label: '当前版本'.*active_revision_id.*已采用.*尚未采用/)
  assert.match(status, /label: '候选版本'.*candidate_revision_id.*尚未采用.*完整审查通过.*暂无候选/)
  assert.match(status, /className='source-revision-row'/)
  assert.match(status, /revision\.id\.slice\(0, 12\)/)
  assert.match(status, /<ActionButton[^>]*onClick=\{\(\) => copy\(revision\.id!\)\}/)
  assert.match(status, /setClipboardData\(\{ data: value \}\)/)
  assert.match(status, /复制失败/)
  assert.match(status, /<Feedback error=\{copyError\}/)
  assert.match(status, /尚未采用 · 需完整审查通过/)
})

test('source recovery actions retain explicit cost confirmation and frozen maintenance semantics', () => {
  const status = readFileSync('src/pages/admin/status/index.tsx', 'utf8')
  assert.match(status, /showModal\(\{ title: recoveryTask\?\.resumeTitle, content: resumeConfirmation\(recovery\) \}\)/)
  assert.match(status, /if \(!answer\.confirm\) return false\s+await request\(`[^`]*\/resume`, resumeLimits\(recovery\)\)/)
  assert.match(status, /showModal\(\{ title: '请求 Agent 修复', content: '.*模型费用.*不会直接激活未经审计的结果/)
  assert.match(status, /if \(!answer\.confirm\) return false\s+await request\(`[^`]*\/repair`, \{\}\)/)
  assert.match(readFileSync('src/pages/admin/onboarding-detail/index.tsx', 'utf8'), /维护状态不改变已冻结的接入摘要/)
  assert.match(status, /提高限制并继续/)
  assert.match(status, /请求 Agent 修复/)
  assert.match(status, /onClick=\{state\.reload\}/)
})

test('quick is cost only: supported facts use the same parse labels as detailed facts', async () => {
  const { incrementalNoticeParseLabel } = await import('../src/services/incremental')
  for (const extraction_path of ['quick', 'detailed'] as const) {
    const path = extraction_path === 'quick' ? '低成本路径' : '详细路径'
    assert.equal(incrementalNoticeParseLabel({ status: 'success', extraction_path, position_count: 1 }), `${path} · 完整解析`)
    assert.equal(incrementalNoticeParseLabel({ status: 'partial', extraction_path, position_count: 2 }), `${path} · 部分材料解析`)
    assert.equal(incrementalNoticeParseLabel({ status: 'review', extraction_path, position_count: 0 }), `${path} · 解析待复核`)
    assert.equal(incrementalNoticeParseLabel({ status: 'failed', extraction_path, position_count: 0 }), `${path} · 解析失败`)
  }
  assert.equal(incrementalNoticeParseLabel({ status: 'partial', extraction_path: 'quick', position_count: 0 }),
    '低成本路径 · 归档无岗位事实（非过期确认）')
  assert.equal(incrementalNoticeParseLabel({ status: 'success', extraction_path: 'quick', position_count: 0 }),
    '低成本路径 · 归档无岗位事实（非过期确认）')
  assert.equal(incrementalNoticeParseLabel({ status: 'review', extraction_path: null, position_count: 0 }), '路径待核验 · 解析待复核')
})

test('confirmed cancellation status is separate from execution cost and archive expiry', async () => {
  const { incrementalNoticeParseLabel } = await import('../src/services/incremental')
  assert.equal(incrementalNoticeParseLabel({ status: 'cancelled', extraction_path: 'detailed', position_count: 0 }), '公告取消关联已确认')
})

test('admin projections show routing as cost hints, not notice validity or fact quality', () => {
  for (const category of ['fixed_active', 'fixed_expired', 'undated_low_priority', 'undated_high_priority'] as const) {
    assert.match(routingCategoryLabel(category), /仅成本路由/)
    assert.doesNotMatch(routingCategoryLabel(category), /已过期|未到截止日|资格|完整解析/)
    assert.match(counts({ [category]: 2 }), /仅成本路由.*2/)
  }
  assert.equal(routingCategoryLabel('unknown'), '成本路由未知')
  assert.equal(routingCategoryLabel(null), '成本路由待核验')
  assert.match(counts({ quick: 3, detailed: 1 }), /低成本路径（非期限结论） 3.*详细路径 1/)
  const incremental = readFileSync('src/pages/admin/incremental/index.tsx', 'utf8')
  const detail = readFileSync('src/pages/admin/onboarding-detail/index.tsx', 'utf8')
  const status = readFileSync('src/pages/admin/status/index.tsx', 'utf8')
  assert.doesNotMatch(incremental + detail + status, /quality_tier|notice\.validity|audit\?\.validity|audit\?\.application_period|statuses\.coarse/)
  assert.match(incremental, /routingCategoryLabel\(notice\.routing_category\)/)
  assert.match(incremental, /notice\.field_issue_codes/)
  assert.match(incremental, /notice\.coverage_issue_codes/)
  assert.match(incremental, /notice\.review_required/)
  assert.match(incremental, /hasFactValue\(position\.facts\.deadline_on\)/)
  assert.match(incremental, /listFact\(position\.facts\.deadline_on, ''\)/)
  assert.match(incremental, /低成本归档暂无岗位事实，不代表已确认过期/)
  assert.match(status, /summary\.statuses\.archived/)
  assert.match(status, /非过期确认/)
  assert.match(status, /summary\.review_required/)
  assert.match(detail, /成本路由（非期限结论）/)
  assert.match(detail, /实际提取路径（非质量等级）/)
})
