import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { ApiError } from '../src/services/api-error'
import { evidenceRows, getJob, getJobs, isDate, jobCautions, jobDetailRoute, jobsPath, jobsUrl, officialUrl, parseJob, parseJobsPage, periodLabel } from '../src/services/jobs'
import type { ConditionValues, Job, JobsPage, JobsTransport, PositionCondition } from '../src/services/jobs'

function job(): Job {
  return {
    id: 'position-one', source_id: 'source', source_name: '示例官方来源',
    official_url: 'https://example.org/notice', bundle_id: 'bundle', recipe_id: 'recipe', audit_id: 'audit', admission_audit_id: 'run',
    audit_evaluated_on: '2026-10-13', audit_evaluated_at: '2026-10-13T04:00:00+00:00',
    expiry_evaluated_on: '2026-10-14', expiry_precision: 'day', last_successful_check: '2026-10-13T03:00:00+00:00',
    facts: {
      schema_version: 'position-facts-v3', position_code:{value:null,tag:'unknown'}, title: { value: '实验助理', tag: 'source' },
      hiring_unit: { value: null, tag: 'unknown' }, employer: { value: '合成用人单位', tag: 'ai_inferred' },
      headcount: { value: 2, tag: 'source' }, headcount_description: { value: '计划 2 人', tag: 'source' },
      deadline_on: { value: null, tag: 'unknown' }, application_rule: { value: null, tag: 'unknown' },
      employment_type: { value: null, tag: 'unknown' }, work_location: { value: null, tag: 'unknown' },
      requirements_text: { value: null, tag: 'unknown' },
      application_start_on: { value: null, tag: 'unknown' }, application_dates: { value: null, tag: 'unknown' },
      staffing_status: { value: null, tag: 'unknown' },
    conditions: [
      { id: 'education-1', type: 'education', operator: 'at_least', scope: 'notice', value: {level: 'bachelor'}, tag: 'source' },
      { id: 'degree-1', type: 'degree', operator: 'equals', scope: 'position', value: {level: 'bachelor'}, tag: 'source' },
      { id: 'major-1', type: 'major', operator: 'in', scope: 'position', value: {names: ['计算机', '数学'], unrestricted: false}, tag: 'ai_inferred' },
    ],
    condition_logic: { operator: 'all', items: [{ condition_id: 'education-1' },
      { operator: 'any', items: [{ condition_id: 'degree-1' }, { condition_id: 'major-1' }] }] },
    },
    evidence: { title: [{ id: 'doc:r1c0', text: '实验助理', locator: 'sheet 岗位表 row 2 column 1' }] },
    validity: { status: 'unknown', deadline: null, reason: 'No explicit deadline',
      verification_scope: 'position', evidence_ids: [], evaluated_at: '2026-10-14T00:00:00+08:00', current_hiring_status: 'not_verified' },
    extraction_path: 'detailed',
    field_issues: [], coverage_issues: [],
    material_status: 'complete', extraction_status: 'parsed', review_status: 'needs_review', review_required: false, repair_required: false, warnings: [],
  }
}
function page(item = job()): JobsPage {
  return { items: [item], page: 1, page_size: 20, total: 1, has_next: false, include_partial: true, evaluated_on: '2026-10-14', timezone: 'Asia/Shanghai', filters: { education: 'all', employment: 'all' } }
}

test('parses the audited job and preserves literal evidence, URL and timestamps', () => {
  const value = job()
  assert.deepEqual(parseJob(value), value)
  assert.deepEqual(parseJobsPage(page()), page())
  assert.deepEqual(evidenceRows(value), [{ field: 'title', block: value.evidence.title[0] }])
  assert.equal(value.audit_evaluated_on, '2026-10-13')
  assert.equal(value.expiry_evaluated_on, '2026-10-14')
})

test('unknown, long-term and until-filled rules never imply current hiring', () => {
  const value = job()
  value.facts.application_rule = {value:'not_stated', tag:'source'}
  assert.equal(periodLabel(value), '原文未说明报名期限')
  value.facts.application_rule = {value: 'open_ended', tag:'source'}
  assert.equal(periodLabel(value), '原文声明长期有效，是否仍招聘待核验')
  value.facts.application_rule.value = 'until_filled'
  assert.equal(periodLabel(value), '原文声明招满即止，是否招满待核验')
  value.facts.application_rule = {value:null, tag:'unknown'}
  assert.equal(periodLabel(value), '报名期限待确认')
  assert.ok(jobCautions(value).some(text => text.includes('尚未核验')))
  assert.ok(jobCautions(value).some(text => text.includes('不能默认有事业编制')))
})

test('fixed deadline labels retain date-level precision and do not claim open hiring', () => {
  const value = job()
  value.validity.status = 'not_expired'; value.validity.deadline = '2026-10-16'
  assert.equal(periodLabel(value), '固定截止 2026-10-16')
  value.validity.deadline = value.expiry_evaluated_on
  assert.match(periodLabel(value), /截止当日.*具体时间/)
  value.validity.status = 'expired'
  assert.equal(periodLabel(value), '已过固定截止期限')
})

test('partial material is included by default and explicit complete-only scope remains available', () => {
  const value = job(); value.material_status = 'partial'
  value.warnings = [{ code: 'INCOMPLETE_MATERIAL', message: 'Attachment unavailable' }]
  assert.equal(parseJobsPage(page(value)).items[0].material_status, 'partial')
  assert.throws(() => parseJobsPage({ ...page(value), include_partial: false }))
  assert.equal(parseJobsPage({ ...page(value), include_partial: true }).items[0].warnings[0].code, 'INCOMPLETE_MATERIAL')
  assert.ok(jobCautions(value).some(text => text.includes('材料不完整')))
})

test('rejects malformed, unreviewed, expired-list and misleading live-hiring data', () => {
  for (const update of [
    { facts: {} }, { id: '../private' }, { facts: { ...job().facts, headcount: { value: -1, tag: 'source' } } }, { evidence: { title: [{}] } },
    { official_url: 'javascript:alert(1)' }, { review_required: "yes" }, { repair_required: "yes" },
    { material_status: 'failed' }, { extraction_status: 'silently_confirmed' }, { expiry_precision: 'second' },
    { facts: { ...job().facts, deadline_on: { value: '2026-02-30', tag: 'source' } } }, { audit_evaluated_at: '2026-10-13 12:00' },
    { validity: { ...job().validity, current_hiring_status: 'open' } },
    { validity: { ...job().validity, application_period: { kind: 'guaranteed_open' } } },
  ]) assert.throws(() => parseJob({ ...job(), ...update }))
  const expired = job(); expired.validity.status = 'expired'
  assert.throws(() => parseJobsPage(page(expired)))
  for (const value of [null, '', [], {}]) assert.throws(() => parseJob(value))
})

test('validates pagination metadata and separates audit day from current evaluation', () => {
  for (const update of [
    { page: 0 }, { page: 1001 }, { page_size: 51 }, { total: -1 }, { total: 0 }, { timezone: 'UTC' },
    { has_next: true }, { evaluated_on: '2026-10-15' }, { evaluated_on: '2026-02-30' }, { include_partial: 'true' },
  ]) assert.throws(() => parseJobsPage({ ...page(), ...update }))
  assert.equal(parseJobsPage({ ...page(), page: 2, items: [] }).items.length, 0)
})

test('bounded search is encoded literally, trimmed and resets through caller-owned pagination', () => {
  assert.equal(jobsPath({ q: '  计算机 & 岗位%  ', page: 2, includePartial: true }), '/jobs?page=2&page_size=20&q=%E8%AE%A1%E7%AE%97%E6%9C%BA%20%26%20%E5%B2%97%E4%BD%8D%25&include_partial=true')
  assert.equal(jobsPath(), '/jobs?page=1&page_size=20&q=&include_partial=true')
  for (const query of [{ page: 0 }, { page: 1001 }, { pageSize: 51 }, { page: 1.5 }, { q: 'x'.repeat(101) }, { q: '\u0000' }]) assert.throws(() => jobsPath(query))
})

test('public transport supports H5 proxy and HTTPS WeChat without auth/session access', async () => {
  assert.equal(jobsUrl('', 'h5', jobsPath()), '/api' + jobsPath())
  assert.equal(jobsUrl('https://example.org/', 'weapp', '/jobs/position-one'), 'https://example.org/api/jobs/position-one')
  assert.throws(() => jobsUrl('', 'weapp', '/jobs'))
  assert.throws(() => jobsUrl('http://localhost:8089', 'weapp', '/jobs'))
  const calls: string[] = []
  const transport: JobsTransport = async url => { calls.push(url); return { statusCode: 200, data: page() } }
  const value = await getJobs({ q: '助理' }, transport, '', 'h5')
  assert.equal(value.items[0].facts.title.value, '实验助理')
  assert.equal(calls.length, 1)
  assert.match(calls[0], /^\/api\/jobs\?page=1&/)
  const detail = await getJob('position-one', false, async () => ({ statusCode: 200, data: job() }), '', 'h5')
  assert.equal(detail.id, 'position-one')
})

test('maps HTTP/network failures and makes only one read attempt', async () => {
  for (const status of [404, 503]) {
    let calls = 0
    await assert.rejects(getJobs({}, async () => { calls++; return { statusCode: status, data: {} } }, '', 'h5'), error => error instanceof ApiError && error.status === status)
    assert.equal(calls, 1)
  }
  await assert.rejects(getJobs({}, async () => { throw new Error('offline') }, '', 'h5'), /连接不上岗位服务/)
  await assert.rejects(getJobs({}, async () => ({ statusCode: 200, data: {} }), '', 'h5'), /数据格式无效/)
})

test('detail rejects incorrect IDs, preserves explicit material scope and displays expired jobs', async () => {
  const value = job()
  await assert.rejects(getJob('another', false, async () => ({ statusCode: 200, data: value }), '', 'h5'))
  await assert.rejects(getJob('../admin', false, async () => { throw new Error('must not send') }, '', 'h5'), /编号无效/)
  value.material_status = 'partial'
  await assert.rejects(getJob(value.id, false, async () => ({ statusCode: 200, data: value }), '', 'h5'))
  assert.equal((await getJob(value.id, true, async () => ({ statusCode: 200, data: value }), '', 'h5')).material_status, 'partial')
  value.validity.status = 'expired'
  value.validity.deadline = '2026-10-12'
  value.facts.deadline_on = {value:'2026-10-12',tag:'source'}
  value.facts.application_dates = {value:{start:null,end:{date:'2026-10-12',year:2026,month:10,day:12,year_status:'explicit',tag:'source',time:null}},tag:'source'}
  assert.equal((await getJob(value.id, true, async () => ({ statusCode: 200, data: value }), '', 'h5')).validity.status, 'expired')
})

test('literal URL/evidence content cannot become an executable navigation target', () => {
  for (const url of ['javascript:alert(1)', 'file:///etc/passwd', 'https://user:pass@example.org/', 'https://example.org\\evil', 'https://example.org/\n']) assert.throws(() => officialUrl(url))
  assert.equal(officialUrl('https://example.org/notice?x=1#original'), 'https://example.org/notice?x=1#original')
  const value = job(); value.evidence.title[0].text = '<script>alert(1)</script>'
  assert.equal(evidenceRows(parseJob(value))[0].block.text, '<script>alert(1)</script>')
})

test('date validation rejects impossible days and accepts leap days', () => {
  assert.equal(isDate('2026-02-30'), false)
  assert.equal(isDate('2026-13-01'), false)
  assert.equal(isDate('2028-02-29'), true)
  assert.equal(isDate('2026-2-1'), false)
})


test('job details use a separate native navigation page with explicit material scope', async () => {
  assert.equal(jobDetailRoute('position-one'), '/pages/jobs/detail/index?id=position-one&include_partial=true')
  assert.equal(jobDetailRoute('position-one', true), '/pages/jobs/detail/index?id=position-one&include_partial=true')
  for (const id of ['', '../private', 'position-one&include_partial=true']) assert.throws(() => jobDetailRoute(id))
  const globals = globalThis as unknown as { defineAppConfig?: (value: unknown) => unknown; definePageConfig?: (value: unknown) => unknown }
  const beforeApp = globals.defineAppConfig; const beforePage = globals.definePageConfig
  globals.defineAppConfig = value => value; globals.definePageConfig = value => value
  try {
    const { default: app } = await import('../src/app.config')
    const { default: page } = await import('../src/pages/jobs/detail/index.config')
    assert.ok(app.pages?.includes('pages/jobs/detail/index'))
    assert.ok(!app.tabBar?.list.some(tab => tab.pagePath === 'pages/jobs/detail/index'))
    assert.equal(page.navigationStyle, 'default')
    assert.equal(page.navigationBarTitleText, '岗位详情')
  } finally { globals.defineAppConfig = beforeApp; globals.definePageConfig = beforePage }
  const list = readFileSync('src/pages/jobs/index.tsx', 'utf8')
  const detail = readFileSync('src/pages/jobs/detail/index.tsx', 'utf8')
  assert.match(list, /Taro\.navigateTo/)
  assert.match(list, /useReachBottom/)
  assert.doesNotMatch(list + detail, /返回岗位列表并刷新|setSelected|navigateBack|navigationStyle: 'custom'/)
})

test('literal education/employment paths preserve defaults and encode combined search', () => {
  assert.equal(jobsPath({ education: 'bachelor', employment: 'labor_dispatch', q: ' 学士 & A/B? ', includePartial: true, page: 3 }),
    '/jobs?page=3&page_size=20&q=%E5%AD%A6%E5%A3%AB%20%26%20A%2FB%3F&include_partial=true&education=bachelor&employment=labor_dispatch')
  for (const education of ['all', 'bachelor', 'master', 'doctorate', 'unknown'] as const) {
    assert.ok(jobsPath({ education }).endsWith(`&education=${education}`))
    assert.doesNotMatch(jobsPath({ education }), /&employment=/)
  }
  for (const employment of ['all', 'public_institution_staff', 'personnel_agency', 'labor_dispatch', 'labor_contract', 'other', 'unknown'] as const) {
    assert.ok(jobsPath({ employment }).endsWith(`&employment=${employment}`))
    assert.doesNotMatch(jobsPath({ employment }), /&education=/)
  }
  for (const query of [
    { education: 'doctoral' }, { education: 'Bachelor' }, { education: '' }, { education: null },
    { education: 'master&employment=all' }, { employment: 'permanent' }, { employment: null },
    { employment: false }, { includePartial: 'true' }, { q: 123 },
  ]) assert.throws(() => jobsPath(query as unknown as Parameters<typeof jobsPath>[0]), /参数无效/)
})

test('list parser validates filter enums and selected metadata, without inferring title keywords', () => {
  const filters = { education: 'doctorate', employment: 'unknown' } as const
  const item = job(); item.facts.title.value = '博士后助理'; item.facts.requirements_text = {value: '协助博士招生', tag:'source'}; item.facts.conditions = []; item.facts.condition_logic = null
  const value = { ...page(item), filters }
  // Selection belongs to server education/degree conditions, never title or requirements inference.
  assert.deepEqual(parseJobsPage(value, filters).filters, filters)
  assert.deepEqual(parseJobsPage(value, filters).items[0].facts.conditions, [])
  for (const metadata of [null, [], {}, { education: 'all' }, { employment: 'all' },
    { education: 'doctoral', employment: 'all' }, { education: 'all', employment: 'permanent' },
    { education: 1, employment: 'all' }]) {
    assert.throws(() => parseJobsPage({ ...page(), filters: metadata }), /数据格式无效/)
  }
  assert.throws(() => parseJobsPage(value, { education: 'bachelor', employment: 'unknown' }), /筛选条件不一致/)
  assert.throws(() => parseJobsPage(value, { education: 'doctorate', employment: 'labor_dispatch' }), /筛选条件不一致/)
  assert.throws(() => parseJobsPage(value, {}), /筛选条件不一致/)
  assert.throws(() => parseJobsPage({ ...page(), include_partial: false }, {}), /数据格式无效/)
})

test('new list metadata is required even without selected filters', async () => {
  const { filters: _filters, ...withoutFilters } = page()
  const missing: JobsTransport = async () => ({ statusCode: 200, data: withoutFilters })
  await assert.rejects(getJobs({}, missing, '', 'h5'), /数据格式无效/)
  await assert.rejects(getJobs({ education: 'all', employment: 'all' }, missing, '', 'h5'), /数据格式无效/)
  let path = ''
  const filters = { education: 'master', employment: 'labor_dispatch' } as const
  const current: JobsTransport = async url => { path = url; return { statusCode: 200, data: { ...page(), filters } } }
  assert.deepEqual((await getJobs(filters, current, '', 'h5')).filters, filters)
  assert.match(path, /&education=master&employment=labor_dispatch$/)
  await assert.rejects(getJobs({ education: 'bachelor' }, current, '', 'h5'), /筛选条件不一致/)
})

test('filter changes reset pagination while retaining committed search and other selections', async () => {
  const { initialJobsQuery, changeJobsFilters, educationChoices, employmentChoices, materialChoices } = await import('../src/pages/jobs/filters')
  const previous = { ...initialJobsQuery(), page: 7, q: '计算机', education: 'doctorate' as const, includePartial: true }
  const changed = changeJobsFilters(previous, { employment: 'labor_dispatch' })
  assert.deepEqual(changed, { ...previous, page: 1, employment: 'labor_dispatch' })
  assert.equal(previous.page, 7)
  assert.deepEqual(changeJobsFilters(changed, { education: 'unknown', includePartial: false }),
    { ...changed, page: 1, education: 'unknown', includePartial: false })
  assert.deepEqual(initialJobsQuery(), { page: 1, pageSize: 20, q: '', includePartial: true, education: 'all', employment: 'all', deadline: 'all', degree: 'all', staffing: 'all', experience: 'all' })
  assert.deepEqual(educationChoices.map(choice => choice.label), ['全部学历', '专科', '本科', '研究生', '硕士研究生', '博士研究生', '学历不限', '学历未识别'])
  assert.deepEqual(employmentChoices.map(choice => choice.label), ['全部用工', '人事代理', '劳务派遣', '劳动合同制', '其他用工', '用工未识别'])
  assert.deepEqual(materialChoices, ['仅完整材料', '全部材料'])
})

test('mobile feed uses native scrolling/refresh and a quiet empty state', () => {
  const list = readFileSync('src/pages/jobs/index.tsx', 'utf8')
  const style = readFileSync('src/pages/jobs/jobs.scss', 'utf8')
  const config = readFileSync('src/pages/jobs/index.config.ts', 'utf8')
  assert.doesNotMatch(list, /Picker|jobs-extra-filters|jobs-filter-chips/)
  assert.match(list, /<JobFilterSheet/)
  const sheet = readFileSync('src/pages/jobs/filter-sheet.tsx', 'utf8')
  assert.match(sheet, /ScrollView/)
  assert.match(sheet, /role='dialog'/)
  assert.match(sheet, /catchMove/)
  assert.match(sheet, /Taro.hideTabBar/)
  assert.match(sheet, /Taro.showTabBar/)
  assert.match(sheet, /applyJobsFilters\(query, pending\)/)
  assert.match(sheet, /确认筛选/)
  const definitions = readFileSync('src/pages/jobs/filters.ts', 'utf8')
  for (const label of ['学历要求', '用工形式', '报名期限', '工作经验', '编制身份', '学位要求']) assert.ok(definitions.includes(label))
  assert.match(list, /useReachBottom/)
  assert.match(list, /usePullDownRefresh/)
  assert.match(config, /enablePullDownRefresh: true/)
  assert.match(list, /useJobsFeed\(query\)/)
  assert.match(list, /onClick=\{resetFilters\}/)
  assert.match(list, /重试读取列表/)
  assert.match(list, /重试加载/)
  assert.match(list, /jobs-empty-title/)
  assert.doesNotMatch(list, /EmptyState|上一页|下一页|第 \{result.page\} 页|回到第一页|jobs-list-note|jobs-list-footnote|useDidShow/)
  assert.match(list, /jobDetailRoute\(id, query.includePartial === true\)/)
  assert.match(style, /button \{ min-height: 44px; \}/)
  assert.match(style, /\.jobs-filter-entry \{[^}]*min-height: 44px/)
})

test('only v3 facts are accepted; unknown business fields stay explicit and origin is not required', () => {
  const value = job()
  assert.ok(!('facts_origin' in parseJob(value)))
  const { facts: _facts, ...metadata } = value
  assert.throws(() => parseJob({ ...metadata, title: '旧岗位', headcount: 2, education_text: '本科',
    major_text: '计算机', employment_arrangement: 'labor_dispatch', deadline_on: null }), /数据格式无效/)
  for (const key of Object.keys(value.facts).filter(key => !['schema_version', 'conditions', 'condition_logic', 'title'].includes(key))) {
    const facts = { ...value.facts, [key]: { value: null, tag: 'unknown' } }
    assert.deepEqual(parseJob({ ...value, facts }).facts, facts)
    assert.throws(() => parseJob({ ...value, facts: { ...value.facts, [key]: { value: null, tag: 'guessed' } } }))
    assert.throws(() => parseJob({ ...value, facts: { ...value.facts, [key]: undefined } }))
  }
  for (const facts of [null, { ...value.facts, schema_version: 'position-facts-v1' },
    { ...value.facts, title: { value: 123, tag: 'source' } },
    { ...value.facts, headcount: { value: 1.5, tag: 'source' } },
    { ...value.facts, title: { value: null, tag: 'unknown' } },
    { ...value.facts, employer: { value: '单位', tag: 'unknown' } },
    { ...value.facts, employer: { value: null, tag: 'ai_inferred' } },
    { ...value.facts, employment_type: { value: 'permanent', tag: 'source' } }]) {
    assert.throws(() => parseJob({ ...value, facts }), /数据格式无效/)
  }
})

test('conditions accept separate types, operators and scopes, including bounded structured values', async () => {
  const { conditionTypes, conditionOperators, employmentTypes } = await import('../src/services/jobs')
  for (const type of conditionTypes) for (const operator of conditionOperators) for (const scope of ['notice', 'position'] as const) {
    const value = job()
    const typedValues: ConditionValues = {
      education: {level:'bachelor'}, degree:{level:'master'}, study_form:{form:'full_time'},
      major:{names:['数学'],unrestricted:false}, age:{minimum:null,maximum:35,reference_date:null},
      work_experience:{minimum_years:2,kind:'工作'}, graduation_status:{status:'应届'},
      graduation_year:{years:[2025,2026]}, political_status:{statuses:['中共党员']},
      license:{names:['教师资格证']}, language:{name:'英语',requirement:'六级'}, other:{text:'以原文为准'},
    }
    value.facts.conditions = [{ id: 'c1', type, operator, scope, tag: 'source', value: typedValues[type] } as PositionCondition]
    value.facts.condition_logic = { condition_id: 'c1' }
    assert.deepEqual(parseJob(value).facts.conditions, value.facts.conditions)
  }
  for (const employment of employmentTypes) {
    const value = job(); value.facts.employment_type = { value: employment, tag: 'source' }
    assert.equal(parseJob(value).facts.employment_type.value, employment)
    const filters = { education: 'all', employment } as const
    assert.deepEqual(parseJobsPage({ ...page(value), filters }, filters).filters, filters)
  }
  const value = job(); value.facts.conditions = []; value.facts.condition_logic = null
  value.facts.headcount = {value: 0, tag: 'source'}
  assert.deepEqual(parseJob(value).facts.conditions, [])
  assert.equal(parseJob(value).facts.headcount.value, 0)
})

test('malformed or duplicate conditions and dangling/inline/ambiguous logic are rejected', () => {
  const value = job(); const condition = value.facts.conditions[0]
  for (const update of [{ id: '' }, { type: 'education_text' }, { operator: 'gte' }, { scope: 'global' },
    { tag: 'inferred' }, { value: undefined }, { value: NaN }, { value: 'x'.repeat(100001) }]) {
    assert.throws(() => parseJob({ ...value, facts: {...value.facts, conditions: [{ ...condition, ...update }], condition_logic: null} }))
  }
  assert.throws(() => parseJob({ ...value, facts: {...value.facts, conditions: [condition, condition], condition_logic: null} }))
  for (const logic of [undefined, [], {}, { condition_id: 'missing' }, { condition_id: condition.id, value: 'inline' },
    { operator: 'and', items: [{ condition_id: condition.id }] }, { operator: 'all', items: [] },
    { operator: 'any', items: [{ type: 'major', value: '计算机' }] },
    { operator: 'all', items: [{ condition_id: condition.id }], condition_id: condition.id }]) {
    assert.throws(() => parseJob({ ...value, facts: {...value.facts, condition_logic: logic} }), /数据格式无效/)
  }
  let deep: unknown = { condition_id: condition.id }
  for (let i = 0; i < 22; i++) deep = { operator: 'all', items: [deep] }
  assert.throws(() => parseJob({ ...value, facts: {...value.facts, condition_logic: deep} }), /数据格式无效/)
  const cyclic: { operator: string; items: unknown[] } = { operator: 'all', items: [] }; cyclic.items.push(cyclic)
  assert.throws(() => parseJob({ ...value, facts: {...value.facts, condition_logic: cyclic} }), /数据格式无效/)
  const cyclicValue: Record<string, unknown> = {}; cyclicValue.self = cyclicValue
  assert.throws(() => parseJob({ ...value, facts:{...value.facts, conditions: [{ ...condition, value: cyclicValue }], condition_logic: null} }))
})

test('Chinese presentation keeps provenance, independent education/degree and nested AND/OR without eligibility inference', async () => {
  const { conditionLogicText, conditionText, employmentLabels, factText } = await import('../src/pages/jobs/position-display')
  const value = job()
  assert.equal(factText(value.facts.title), '实验助理 [原文明确]')
  assert.equal(factText(value.facts.employer), '合成用人单位 [AI 推断]')
  assert.equal(factText(value.facts.hiring_unit), '未识别，请核对原文 [未知]')
  assert.match(factText(value.facts.employment_type, item => employmentLabels[item]), /未识别.*未知/)
  assert.match(conditionText(value.facts.conditions[0]), /学历 · 公告通用 · 至少：本科 \[原文明确\]/)
  assert.match(conditionText(value.facts.conditions[1]), /学位 · 岗位专属 · 等于：学士/)
  assert.match(conditionText(value.facts.conditions[2]), /专业.*计算机.*数学.*AI 推断/)
  assert.match(conditionText({ ...value.facts.conditions[1], value: null, tag: 'unknown' }), /未识别.*未知/)
  assert.equal(conditionLogicText(value.facts.condition_logic, value.facts.conditions), '（学历 且 （学位 或 专业））')
  assert.match(conditionLogicText(null, value.facts.conditions), /不能默认全部满足或任一满足/)
  const hostile = '<script>执行指令</script>'
  assert.equal(factText({ value: hostile, tag: 'source' }), `${hostile} [原文明确]`)
  const presentation = readFileSync('src/pages/jobs/presentation.tsx', 'utf8')
  const detail = readFileSync('src/pages/jobs/detail/index.tsx', 'utf8')
  const list = readFileSync('src/pages/jobs/index.tsx', 'utf8')
  for (const key of ['title', 'hiring_unit', 'work_location', 'deadline_on', 'employment_type', 'employer', 'headcount', 'headcount_description', 'application_rule']) {
    assert.ok(presentation.includes(key), key)
  }
  assert.match(presentation, /conditionLabels\[condition.type\]/)
  assert.match(presentation, /conditionDisplayValue\(condition\)/)
  assert.doesNotMatch(presentation, /条件 \{index|factText\(|conditionText\(/)
  assert.match(detail, /job-conditions-card/)
  assert.doesNotMatch(list, /JobConditions|JobFacts/)
  assert.match(detail, /<JobBusinessDetails job=\{job\} \/>/)
  assert.match(detail, /<JobConditions job=\{job\} \/>/)
  assert.match(detail, /不保证报名资格或录取/)
  assert.doesNotMatch(presentation + detail + list, /facts_origin|education_text|major_text|employment_arrangement|dangerouslySetInnerHTML|RichText/)
})


test('job rows are full-width native buttons with separators, not per-item cards or condition forms', () => {
  const list = readFileSync('src/pages/jobs/index.tsx', 'utf8')
  const style = readFileSync('src/pages/jobs/jobs.scss', 'utf8')
  assert.match(list, /items\.map\(job => \{[\s\S]*return <Button className='jobs-row' key=\{job.id\} onClick=\{\(\) => openDetail\(job.id\)\}/)
  assert.doesNotMatch(list, /<Card|<JobConditions|<JobFacts|jobs-detail-action/)
  for (const key of ['title', 'hiring_unit', 'headcount']) assert.ok(list.includes(`listFact(job.facts.${key},`), key)
  for (const copy of ['计划 ${value} 人', 'jobTags(job)', 'employmentTraits(job)', 'last_successful_check']) assert.ok(list.includes(copy), copy)
  assert.match(list, /listDeadline\(job\)/)
  assert.match(style, /\.jobs-row \{[^}]*width: 100%[^}]*border-radius: 0/)
  assert.match(style, /\.jobs-row \+ \.jobs-row \{[^}]*border-top: 1px solid/)
  assert.match(style, /white-space: normal/)
  assert.match(style, /focus-visible/)
  assert.doesNotMatch(list, /icon='(?:search|filter|close|retry|refresh)'/)
  assert.match(list, /<NavigationArrow subtle/)
})

test('anonymous job detail links to a separate feedback page preserving material scope, without owning auth or form state', () => {
  const detail = readFileSync('src/pages/jobs/detail/index.tsx', 'utf8')
  assert.match(detail, /\/pages\/jobs\/feedback\/index\?id=\$\{encodeURIComponent\(job.id\)\}&include_partial=\$\{includePartial\}/)
  assert.match(detail, /Taro\.navigateTo/)
  assert.match(detail, /onClick=\{openFeedback\}/)
  assert.match(detail, /onClick=\{copyUrl\}/)
  assert.match(detail, /getJob\(id, includePartial\)/)
  assert.match(readFileSync('src/pages/jobs/feedback/index.tsx', 'utf8'), /反馈仅记录问题，不会自动修改岗位数据/)
  assert.doesNotMatch(detail, /getIdentity|feedbackClient|JobFeedbackForm|Textarea|navigateBack|switchTab/)
})

test('compact list summaries keep inference, unknowns, zero counts and independent hiring validity', async () => {
  const { listFact, listDeadline } = await import('../src/pages/jobs/list-presentation')
  assert.equal(listFact({ value: '助理', tag: 'source' }, '待确认'), '助理')
  assert.equal(listFact({ value: '助理', tag: 'ai_inferred' }, '待确认'), '助理 · AI 推断')
  assert.equal(listFact({ value: null, tag: 'unknown' }, '人数待确认'), '人数待确认')
  assert.equal(listFact({ value: 0, tag: 'source' }, '待确认', value => `计划 ${value} 人`), '计划 0 人')
  const item = job()
  assert.equal(listDeadline(item), '')
  item.facts.application_rule = {value:'until_filled',tag:'source'}
  assert.equal(listDeadline(item), '招满即止 · 招聘待核验')
  item.facts.application_rule.value = 'open_ended'
  assert.equal(listDeadline(item), '长期有效 · 招聘待核验')
  item.facts.application_rule.value = 'not_stated'
  item.facts.deadline_on = { value: '2026-10-31', tag: 'ai_inferred' }
  assert.equal(listDeadline(item), '2026-10-31 · AI 推断 · 待核验')
  item.validity.status = 'expired'
  assert.equal(listDeadline(item), '已过截止日')
})

test('public jobs accept precise position dates, never routing or notice validity', () => {
  const value = job()
  value.facts.deadline_on = {value:'2026-10-31',tag:'source'}
  value.facts.application_dates = {value:{start:null,end:{date:'2026-10-31',year:2026,month:10,day:31,year_status:'explicit',tag:'source',time:'14:00'}},tag:'source'}
  value.expiry_precision = 'minute'
  value.validity.status = 'not_expired'; value.validity.deadline = '2026-10-31'
  assert.equal(parseJob(value).facts.deadline_on.value, '2026-10-31')
  assert.deepEqual(parseJob({...value,extraction_path:'quick'}).facts, value.facts)
  assert.throws(() => parseJob({...value,routing:{category:'fixed_expired'}}))
  assert.throws(() => parseJob({...value,validity:{...value.validity,verification_scope:'saved_notice'}}))
})

test('year uncertainty cannot masquerade as an exact filtering date', () => {
  const value = job()
  value.facts.application_dates = {value:{start:null,end:{date:null,year:null,month:11,day:20,year_status:'unknown',tag:'unknown',time:null}},tag:'source'}
  assert.equal(parseJob(value).validity.status,'unknown')
  assert.throws(() => parseJob({...value,facts:{...value.facts,deadline_on:{value:'2026-11-20',tag:'source'}}}))
  assert.throws(() => parseJob({...value,validity:{...value.validity,disposition:'cancelled'}}))
})

test('acquisition warnings remain separate from exact position deadlines', () => {
  const value = job(); value.material_status='partial'
  value.warnings=[{code:'FETCH_FAILED',message:'Missing annex'}]
  assert.deepEqual(parseJob(value).warnings,value.warnings)
  assert.throws(() => parseJob({...value,validity:{...value.validity,material_timing_impact:'blocking'}}))
  assert.throws(() => parseJob({...value,validity:{...value.validity,application_period:{kind:'fixed_deadline'}}}))
})


test('field uncertainty and repair-needed candidates remain readable without changing facts', () => {
  const value=job()
  value.review_required=true
  value.field_issues=[{code:'UNRESOLVED_CONDITION',message:'经验条件仍需确认',path:'positions[0].conditions'}]
  assert.equal(parseJob(value).facts.title.value,'实验助理')
  assert.equal(parseJobsPage(page(value)).items.length,1)
  assert.ok(jobCautions(value).some(text=>text.includes('部分字段')))
  assert.equal(parseJob({...value,repair_required:true}).facts.title.value, '实验助理')
})


test('inferred date candidates are visible as inference, never exact expiry facts', () => {
  const value=job()
  value.facts.deadline_on={value:'2026-11-20',tag:'ai_inferred'}
  value.facts.application_dates={value:{start:null,end:{date:'2026-11-20',year:2026,month:11,day:20,year_status:'inferred',tag:'ai_inferred',time:null}},tag:'ai_inferred'}
  assert.equal(parseJob(value).validity.status,'unknown')
  assert.throws(()=>parseJob({...value,validity:{...value.validity,status:'not_expired',deadline:'2026-11-20'}}))
})


test('typed conditions survive explicitly unknown relationships without default AND', () => {
  const value=job(); value.facts.condition_logic=null
  value.review_required=true
  value.field_issues=[{code:'UNRESOLVED_CONDITION_LOGIC',message:'条件关系未明确'}]
  assert.equal(parseJob(value).facts.conditions[0].type,'education')
})


test('review, failed enrichment and missing admission are displayed with explicit tags', async () => {
  const {jobTags} = await import('../src/services/jobs')
  const value = job()
  value.admission_audit_id = null; value.last_successful_check = null
  value.review_required = true; value.repair_required = true; value.extraction_status = 'failed'
  value.material_status = 'partial'
  value.display_tags = ['cancelled', 'material_stale', 'refresh_pending']
  assert.deepEqual(parseJob(value), value)
  assert.equal(parseJobsPage(page(value)).items[0].id, value.id)
  const labels = jobTags(value).map(tag => tag.label)
  for (const label of ['信息待核验', '解析未完成', '材料不全', '取消 / 撤销', '材料待更新', '含 AI 推断', '含未知字段', '期限未知']) assert.ok(labels.includes(label), label)
  assert.ok(jobCautions(value).some(text => text.includes('取消或撤销')))
  assert.ok(jobCautions(value).some(text => text.includes('解析未完成')))
  assert.equal((await getJob(value.id, undefined, async () => ({ statusCode: 200, data: value }), '', 'h5')).id, value.id)
  for (const display_tags of [['hiring_confirmed'], ['needs_review', 'needs_review'], 'needs_review']) assert.throws(() => parseJob({...value, display_tags}))
})

test('historical migration has its own provenance, never claims a Flow execution', async () => {
  const {jobTags} = await import('../src/services/jobs')
  const value = job()
  value.extraction_path = 'migration'; value.review_required = true; value.admission_audit_id = null
  value.display_tags = ['migrated', 'needs_review']
  assert.equal(parseJob(value).extraction_path, 'migration')
  assert.ok(jobTags(value).some(tag => tag.code === 'migrated'))
  assert.ok(jobCautions(value).some(text => text.includes('未重新执行解析工作流')))
})

test('list hiring traits preserve staffing identity independently of employment mechanism', async () => {
  const { employmentTraits } = await import('../src/pages/jobs/list-presentation')
  const value=job()
  assert.deepEqual(employmentTraits(value),[])
  value.facts.staffing_status={value:'established',tag:'source'}
  assert.deepEqual(employmentTraits(value),['事业编制'])
  value.facts.staffing_status={value:'non_established',tag:'source'}
  value.facts.employment_type={value:'labor_dispatch',tag:'source'}
  assert.deepEqual(employmentTraits(value),['劳务派遣','编外'])
})


test('expanded facets validate wire metadata, keep unknown separate and reset pagination', async () => {
  const { activeJobFilters, changeJobsFilters, initialJobsQuery, resultCountLabel } = await import('../src/pages/jobs/filters')
  const query = { ...initialJobsQuery(), page: 4, q: '助理', deadline: 'expired' as const, degree: 'bachelor' as const, staffing: 'established' as const, experience: 'not_required' as const, education: 'associate' as const }
  const path = jobsPath(query)
  for (const pair of ['deadline=expired', 'degree=bachelor', 'staffing=established', 'experience=not_required', 'education=associate']) assert.ok(path.includes(pair))
  const filters = { ...page().filters, deadline: query.deadline, degree: query.degree, staffing: query.staffing, experience: query.experience, education: query.education }
  assert.deepEqual(parseJobsPage({ ...page(), filters }, query).filters, filters)
  assert.throws(() => parseJobsPage(page(), query), /筛选条件不一致/)
  for (const key of ['deadline', 'degree', 'staffing', 'experience']) {
    assert.throws(() => jobsPath({ [key]: 'made-up' }), /筛选参数无效/)
    assert.throws(() => parseJobsPage({ ...page(), filters: { ...page().filters, [key]: 'made-up' } }), /数据格式无效/)
  }
  assert.equal(changeJobsFilters(query, { deadline: 'all' }).page, 1)
  assert.equal(changeJobsFilters(query, { deadline: 'all' }).q, '助理')
  assert.equal(activeJobFilters(query).length, 6)
  assert.equal(resultCountLabel(163, true, false), '找到 163 个岗位')
  assert.equal(resultCountLabel(0, true, false), '找到 0 个岗位')
  assert.equal(resultCountLabel(163, true, true), '正在筛选…')
  assert.equal(resultCountLabel(163, true, false, 'failed'), '结果未更新')
  assert.equal(resultCountLabel(null, false, false), '岗位列表')
})

test('expired dates and information review remain independent, not an ambiguous deadline tag', async () => {
  const { jobTags, jobReviewRows } = await import('../src/services/jobs')
  const value = job()
  value.validity.status = 'expired'
  value.review_required = true; value.admission_audit_id = null
  const labels = jobTags(value).map(tag => tag.label)
  assert.ok(labels.includes('已截止') && labels.includes('信息待核验'))
  assert.ok(!labels.includes('期限未知') && !labels.includes('待复核'))
  assert.ok(jobReviewRows(value).some(row => row.label === '解析版本' && row.value === '尚待来源审查'))
  assert.ok(jobCautions(value).some(copy => copy.includes('不会将其重新标为可报名')))
})


test('sheet edits are transactional and reset preserves search, page size and original query', async () => {
  const { applyJobsFilters, resetJobsFilters, initialJobsQuery } = await import('../src/pages/jobs/filters')
  const original = { ...initialJobsQuery(), q: '计算机', page: 4, pageSize: 50, deadline: 'expired' as const }
  const pending = { ...original, degree: 'master' as const, staffing: 'established' as const }
  assert.equal(original.degree, 'all')
  assert.equal(original.page, 4)
  const reset = resetJobsFilters(pending)
  assert.equal(reset.q, '计算机'); assert.equal(reset.pageSize, 50)
  assert.equal(reset.deadline, 'all'); assert.equal(reset.degree, 'all')
  assert.equal(pending.degree, 'master')
  const applied = applyJobsFilters({ ...original, q: '新的已确认搜索' }, pending)
  assert.equal(applied.q, '新的已确认搜索')
  assert.equal(applied.page, 1); assert.equal(applied.pageSize, 50)
  assert.equal(applied.staffing, 'established'); assert.equal(applied.degree, 'master')
})
