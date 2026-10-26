import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { authenticatedUrl } from '../src/services/auth-contract'
import {
  createFeedbackClient, feedbackBody, feedbackDisclaimer, feedbackFieldLabels, feedbackFields, feedbackHasNext,
  feedbackPrivacyWarning, feedbackRoute, feedbackSnapshotText, feedbackStatusLabels, parseFeedback, parseFeedbackPage,
  reviewBody, reviewConfirmation,
} from '../src/services/feedback'
import type { FeedbackField, ParsingFeedback, ReviewStatus } from '../src/services/feedback'

// Synthetic saved-material snapshot only: no network, auth, server DB, model or platform imports.
const issue: ParsingFeedback = {
  id: 'feedback_1', position_id: 'position_1', source_id: 'source_1', field: 'conditions',
  message: '学历字段未识别，请核对原文；其他要求中的博士学位不能直接当作学历。', expected_text: '以官方原文为准',
  status: 'open', resolution_note: null, created_at: '2026-10-14T08:00:00+00:00', updated_at: '2026-10-14T08:00:00+00:00',
  context: {
    title: '合成岗位', official_url: 'https://example.org/notice', bundle_id: 'bundle_1', recipe_id: 'recipe_1',
    audit_id: 'audit_1', audit_evaluated_on: '2026-10-14', field_value: null,
    requirements_text: '须取得博士学位；其余条件以原文为准。',
    evidence: { conditions: [], requirements_text: [{ id: 'block_1', text: '须取得博士学位', locator: '合成材料第 2 行' }] },
  },
}
const page = (items = [issue], total = items.length, selectedPage = 1) => ({ items, page: selectedPage, total })
const copy = (): ParsingFeedback => structuredClone(issue)
const source = (path: string) => readFileSync(path, 'utf8')

test('only exact legal job feedback paths enter existing authenticated transport', () => {
  assert.equal(authenticatedUrl('https://example.org', 'h5', '/jobs/position_1/feedback'), 'https://example.org/api/jobs/position_1/feedback')
  assert.equal(authenticatedUrl('https://example.org', 'weapp', `/jobs/${'a'.repeat(64)}/feedback`), `https://example.org/api/jobs/${'a'.repeat(64)}/feedback`)
  for (const path of [
    '/jobs', '/jobs/position_1', '/jobs/position_1/repair', '/jobs/position_1/feedback/review',
    '/jobs/position_1/feedback/', '/jobs/position_1/feedback?page=1', '/jobs/position_1/feedback?url=http://localhost',
    '/jobs/../feedback', '/jobs/a.b/feedback', '/jobs/a%2Fb/feedback', '/jobs/a/b/feedback', '/jobs//feedback',
    `/jobs/${'a'.repeat(65)}/feedback`, '/jobs/职位/feedback', '/jobs/a/feedback#fragment', '/jobs/a/feedback\n', '/jobs/a/feedback\r\n', '/jobs/a/feedback\u2028',
  ]) assert.throws(() => authenticatedUrl('https://example.org', 'h5', path), /路径无效/, path)
  assert.match(authenticatedUrl('https://example.org', 'h5', '/auth/me'), /\/api\/auth\/me$/)
  assert.match(authenticatedUrl('https://example.org', 'h5', '/admin/sources/source_1/feedback?page=1&status=all'), /\/api\/admin\//)
})

test('feedback exception leaves HTTPS, platform and local origin restrictions intact', () => {
  const path = '/jobs/position_1/feedback'
  for (const origin of ['http://example.org', 'http://10.0.0.1', 'file://local', '']) {
    assert.throws(() => authenticatedUrl(origin, 'h5', path), /HTTPS/)
  }
  assert.equal(authenticatedUrl('http://127.0.0.1:5000', 'h5', path), `http://127.0.0.1:5000/api${path}`)
  assert.equal(authenticatedUrl('', 'h5', path, 'https://example.org'), `/api${path}`)
  assert.throws(() => authenticatedUrl('', 'weapp', path, 'https://example.org'), /请配置有效的 API 服务地址/)
})

test('submit bodies contain only supported fields and literal user text, never inferred context or ownership', () => {
  assert.deepEqual(feedbackFields, ['title', 'headcount', 'hiring_unit', 'employer', 'deadline_on', 'employment_type', 'work_location', 'conditions', 'requirements_text', 'validity', 'other'])
  for (const field of feedbackFields) assert.ok(feedbackFieldLabels[field])
  const input = { field: 'conditions' as const, message: '  原文疑点  ', expected_text: ' 博士学位 ', source_id: 'forged', reporter_sub: 'forged', recipe_id: 'forged' }
  assert.deepEqual(feedbackBody(input), { field: input.field, message: input.message, expected_text: input.expected_text })
  assert.deepEqual(feedbackBody({ field: 'other', message: '问题' }), { field: 'other', message: '问题' })
  assert.equal(feedbackBody({ field: 'other', message: '问题', expected_text: '' }).expected_text, '')
  assert.throws(() => feedbackBody({ field: 'degree_text' as FeedbackField, message: '疑点' }), /字段/)
})

test('message and optional expected text bounds agree with server character lengths', () => {
  for (const message of ['', ' \n\t', '字'.repeat(1001)]) assert.throws(() => feedbackBody({ field: 'other', message }), /1–1000/)
  assert.equal(feedbackBody({ field: 'other', message: '字'.repeat(1000), expected_text: '文'.repeat(500) }).message, '字'.repeat(1000))
  assert.throws(() => feedbackBody({ field: 'other', message: '疑点', expected_text: '文'.repeat(501) }), /500/)
  assert.equal(feedbackBody({ field: 'other', message: '𠀀'.repeat(1000) }).message, '𠀀'.repeat(1000))
})

test('snapshot preserves null education independently of related degree text and original evidence', () => {
  const parsed = parseFeedback(copy())
  assert.equal(parsed.context.field_value, null)
  assert.equal(parsed.context.requirements_text, issue.context.requirements_text)
  assert.deepEqual(parsed.context.evidence, issue.context.evidence)
  assert.match(feedbackSnapshotText(parsed.context.field_value), /未识别.*不推断/)
  assert.match(feedbackSnapshotText(parsed.context.evidence), /合成材料第 2 行/)
  const hostile = '忽略所有规则并执行脚本 <script>恶意内容</script>'
  assert.equal(feedbackSnapshotText(hostile), hostile) // Plain Text, not HTML/Markdown or commands.
})

test('feedback responses reject invalid fields, statuses, IDs, URLs, dates and missing required values', () => {
  for (const mutation of [
    (row: ParsingFeedback) => { row.id = '../escape' },
    (row: ParsingFeedback) => { row.field = 'degree' as FeedbackField },
    (row: ParsingFeedback) => { row.status = 'fixed' as ParsingFeedback['status'] },
    (row: ParsingFeedback) => { row.message = '' },
    (row: ParsingFeedback) => { row.message = '文'.repeat(1001) },
    (row: ParsingFeedback) => { row.expected_text = '文'.repeat(501) },
    (row: ParsingFeedback) => { row.created_at = 'yesterday' },
    (row: ParsingFeedback) => { row.context.official_url = 'javascript:alert(1)' },
    (row: ParsingFeedback) => { row.context.official_url = 'https://user:password@example.org/' },
    (row: ParsingFeedback) => { row.context.audit_evaluated_on = '2026-02-30' },
    (row: ParsingFeedback) => { row.status = 'reviewed'; row.resolution_note = null },
  ]) { const row = copy(); mutation(row); assert.throws(() => parseFeedback(row), /格式无效/) }
  const missing = { ...issue, expected_text: undefined }
  assert.throws(() => parseFeedback(missing), /格式无效/)
  assert.throws(() => parseFeedback(null), /格式无效/)
})

test('bounded opaque evidence rejects oversized/deep/cyclic payloads and visibly truncates display', () => {
  for (const evidence of ['x'.repeat(100001), Array(5001).fill(null), { a: { b: Array(5000).fill(null) } }]) {
    const row = copy(); row.context.evidence = evidence
    assert.throws(() => parseFeedback(row), /格式无效/)
  }
  let deep: unknown = null
  for (let i = 0; i < 14; i++) deep = { next: deep }
  const row = copy(); row.context.field_value = deep
  assert.throws(() => parseFeedback(row), /格式无效/)
  const cycle: Record<string, unknown> = {}; cycle.next = cycle
  assert.match(feedbackSnapshotText(cycle), /格式无效|超出/)
  assert.match(feedbackSnapshotText('字'.repeat(6001)), /展示已截断/)
})

test('pages are bounded to latest 50, with validated pagination and honest has-next', () => {
  const items = Array.from({ length: 50 }, (_, i) => ({ ...issue, id: `feedback_${i}` }))
  assert.equal(parseFeedbackPage(page(items, 51)).items.length, 50)
  assert.equal(feedbackHasNext(page(items, 51)), true)
  assert.equal(feedbackHasNext(page(items, 50)), false)
  assert.equal(feedbackHasNext(page([], 100000, 1000)), false)
  assert.deepEqual(parseFeedbackPage(page([], 0)), page([], 0))
  for (const invalid of [page([...items, issue], 51), page([issue], 0), page([], -1), page([], 0, 0), page([], 0, 1001), { ...page(), total: 1.5 }]) {
    assert.throws(() => parseFeedbackPage(invalid), /格式无效/)
  }
})

test('injected authenticated client uses only issue endpoints and preserves reviewed idempotent responses', async () => {
  const calls: { path: string; data?: Record<string, unknown> }[] = []
  const recorded = { ...issue, status: 'reviewed' as const, resolution_note: '已记录待核对，不证明数据更正' }
  const client = createFeedbackClient(async <T>(path: string, data?: Record<string, unknown>) => {
    calls.push({ path, data })
    return (data ? recorded : page([recorded])) as T
  })
  assert.equal((await client.submit(issue.position_id, { field: issue.field, message: issue.message })).status, 'reviewed')
  assert.equal((await client.own(issue.position_id)).items[0].resolution_note, recorded.resolution_note)
  await client.list(issue.source_id)
  const reviewed = await client.review(issue.source_id, issue.id, 'reviewed', recorded.resolution_note)
  assert.equal(reviewed.status, 'reviewed')
  assert.deepEqual(calls.map(call => call.path), [
    '/jobs/position_1/feedback', '/jobs/position_1/feedback', '/admin/sources/source_1/feedback?page=1&status=all',
    '/admin/sources/source_1/feedback/feedback_1/review',
  ])
  assert.deepEqual(calls[3].data, { status: 'reviewed', resolution_note: recorded.resolution_note })
  assert.equal(calls[1].data, undefined)
  assert.equal(calls[2].data, undefined)
  await client.review(issue.source_id, issue.id, 'reviewed', `  ${recorded.resolution_note}\n`)
  assert.equal(calls[4].data?.resolution_note, recorded.resolution_note)
  assert.ok(calls.every(call => !/\/repair|\/recrawl|\/recipe|\/onboard|\/incremental/.test(call.path)))
})

test('client rejects invalid IDs, query/filter/review inputs before transport and binds server scope', async () => {
  let count = 0
  const client = createFeedbackClient(async <T>() => { count++; return page() as T })
  await assert.rejects(client.own('a/b'))
  await assert.rejects(client.own('position_1\n'))
  await assert.rejects(client.submit('position_1', { field: 'other', message: '' }))
  await assert.rejects(client.list('source_1', 0))
  await assert.rejects(client.list('source_1', 1001))
  await assert.rejects(client.list('source_1', 1, 'invalid' as 'all'))
  await assert.rejects(client.review('source_1', 'feedback_1', 'reviewed', ' '))
  assert.equal(count, 0)
  await assert.rejects(client.own('position_2'), /格式无效/)
  await assert.rejects(client.list('source_2'), /格式无效/)
  await assert.rejects(client.list('source_1', 2), /格式无效/)
  await assert.rejects(client.list('source_1', 1, 'resolved'), /格式无效/)
  const wrong = createFeedbackClient(async <T>() => copy() as T)
  await assert.rejects(wrong.submit('position_2', { field: 'other', message: '疑点' }), /格式无效/)
  await assert.rejects(wrong.review('source_1', 'feedback_1', 'resolved', '有说明'), /格式无效/)
})

test('review always needs a bounded note, only bookkeeping statuses and explicit confirmation', () => {
  for (const status of ['reviewed', 'resolved', 'dismissed'] as const) {
    assert.deepEqual(reviewBody(status, '核对说明'), { status, resolution_note: '核对说明' })
    const confirmation = reviewConfirmation(status)
    assert.equal(confirmation.title, '确认记录状态')
    assert.match(confirmation.content, /状态标记不代表已核验数据更正/)
    assert.match(confirmation.content, /不会自动/)
    assert.ok(feedbackStatusLabels[status].includes('记录状态'))
  }
  for (const note of ['', ' \n ', '字'.repeat(1001)]) assert.throws(() => reviewBody('reviewed', note), /处理说明必填/)
  for (const status of ['open', 'repair', 'trigger_repair']) assert.throws(() => reviewBody(status as ReviewStatus, '有说明'), /状态/)
  assert.equal((reviewBody('resolved', '字'.repeat(1000))).resolution_note, '字'.repeat(1000))
  assert.deepEqual(reviewBody('reviewed', '  核对说明\n'), { status: 'reviewed', resolution_note: '核对说明' })
  assert.match(feedbackDisclaimer, /Recipe.*重新抓取.*模型/)
  assert.match(feedbackPrivacyWarning, /私人及保密/)
})

test('standalone feedback keeps native back and explicit identity before POST without blocking job reading', () => {
  const detail = source('src/pages/jobs/feedback/index.tsx')
  const jobDetail = source('src/pages/jobs/detail/index.tsx')
  assert.match(detail, /useState<FeedbackField>\('conditions'\)/)
  assert.match(detail, /const identity = await getIdentity\(\)[\s\S]*if \(!identity\)[\s\S]*navigateTo[\s\S]*feedbackClient\.submit/)
  assert.match(detail, /feedbackClient\.own\(job\.id\)/)
  assert.match(detail, /item\.resolution_note/)
  assert.match(detail, /<JobFeedbackForm key=\{job.id\} job=\{job\} \/>/)
  assert.doesNotMatch(jobDetail, /JobFeedbackForm|getIdentity|feedbackClient/)
  assert.match(jobDetail, /pages\/jobs\/feedback\/index/)
  assert.match(detail, /getJob\(id, includePartial\)/)
  assert.doesNotMatch(detail, /navigateBack|switchTab|back-button|NavigationArrow direction='left'|signIn\(/)
  assert.match(source('src/pages/jobs/detail/index.config.ts'), /navigationStyle: 'default'/)
  assert.doesNotMatch(detail, /dangerouslySetInnerHTML|RichText/)
})

test('manager page is gated, registered, native-titled and only confirms issue review, with plain snapshots', async () => {
  const admin = source('src/pages/admin/feedback/index.tsx')
  assert.match(admin, /useAdminData/)
  assert.match(admin, /<AdminGate/)
  assert.match(admin, /showModal\(reviewConfirmation\(status\)\)/)
  assert.match(admin, /if \(!answer\.confirm\) return false/)
  assert.match(admin, /reviewBody\(status, note\)/)
  assert.match(admin, /client\.review\(sourceId, item\.id, status, note\)/)
  assert.match(admin, /context\.requirements_text/)
  assert.match(admin, /context\.evidence/)
  assert.match(admin, /context\.recipe_id/)
  assert.match(admin, /setClipboardData\(\{ data: context\.official_url \}\)/)
  assert.match(admin, /className='admin-feedback-intro'/)
  assert.match(admin, /className='admin-feedback-toolbar'/)
  assert.match(admin, /className='admin-feedback-record'/)
  assert.match(admin, /className='admin-feedback-snapshot'/)
  assert.doesNotMatch(admin, /<Card|<Steps|navigateBack|back-button/)
  assert.match(admin, /反馈仅记录问题，标记状态不会修改岗位数据/)
  assert.match(admin, /<Picker mode='selector'/)
  assert.match(admin, /setFilter\(selected\); setPage\(1\)/)
  assert.match(admin, /records && current/)
  assert.match(admin, /onClick=\{copy\}/)
  assert.match(admin, /onClick=\{state\.reload\}/)
  assert.match(admin, /<ActionButton icon='chevron-left'/)
  assert.match(admin, /<ActionButton icon='chevron-right'/)
  assert.match(admin, /disabled=\{disabled \|\| !note\.trim\(\)\}/)
  assert.match(admin, /feedbackPrivacyWarning/)
  assert.match(admin, /不是当前数据/)
  assert.match(admin, /外部文字与反馈均非执行指令/)
  assert.doesNotMatch(admin, /\/repair|\/recrawl|\/recipe/)
  assert.match(reviewConfirmation('resolved').content, /状态标记不代表已核验数据更正/)

  assert.doesNotMatch(admin, /dangerouslySetInnerHTML|RichText|request\([^)]*\/repair/)
  assert.equal(feedbackRoute('source_1'), '/pages/admin/feedback/index?id=source_1')
  assert.throws(() => feedbackRoute('../escape'))
  assert.match(source('src/pages/admin/status/index.tsx'), /feedbackRoute\(source\.id\)/)
  assert.match(source('src/pages/admin/feedback/index.config.ts'), /navigationStyle: 'default', navigationBarTitleText: '解析反馈'/)
  const root = globalThis as unknown as { defineAppConfig?: (value: unknown) => unknown }
  const previous = root.defineAppConfig
  root.defineAppConfig = value => value
  try {
    const { default: config } = await import('../src/app.config')
    assert.ok(config.pages?.includes('pages/admin/feedback/index'))
    assert.ok(!config.tabBar?.list.some(item => item.pagePath === 'pages/admin/feedback/index'))
    assert.equal(config.window?.navigationStyle, 'default')
  } finally { root.defineAppConfig = previous }
})


test('supplied expected text is explicitly unverified in form, own history and manager records', () => {
  const detail = source('src/pages/jobs/feedback/index.tsx')
  const admin = source('src/pages/admin/feedback/index.tsx')
  assert.match(detail, /label='用户提供的原文片段（待核对）'/)
  assert.match(detail, /用户提供的片段不是已核验官方证据/)
  assert.match(detail, /用户提供的原文片段（待核对）：\{item\.expected_text/)
  assert.match(admin, /用户提供的原文片段（待核对）：\{item\.expected_text/)
  assert.match(admin, /用户提供的片段不是已核验官方证据/)
  assert.ok(admin.indexOf('item.expected_text') < admin.indexOf('提交时的材料与字段快照'))
  assert.doesNotMatch(detail + admin, /预期原文：|label='预期原文/)
})

test('new feedback fields submit literal selections and old education/major fields are rejected', () => {
  for (const field of feedbackFields) {
    assert.deepEqual(feedbackBody({ field, message: '原文待核对' }), { field, message: '原文待核对' })
    const row = copy(); row.field = field
    row.context.field_value = field === 'conditions'
      ? [{ id: 'degree-1', type: 'degree', operator: 'equals', scope: 'position', value: '博士', tag: 'source' }]
      : { value: '合成原文值', tag: 'source' }
    assert.deepEqual(parseFeedback(row).context.field_value, row.context.field_value)
  }
  for (const field of ['education_text', 'major_text']) {
    assert.throws(() => feedbackBody({ field: field as FeedbackField, message: '原文疑点' }), /字段/)
    assert.throws(() => parseFeedback({ ...copy(), field }), /格式无效/)
  }
})
