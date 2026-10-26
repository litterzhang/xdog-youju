import { ApiError } from './api-error'
import { healthUrl } from './health-contract'

export interface EvidenceBlock { id: string; text: string; locator: string }
export interface JobValidity {
  status: 'expired' | 'not_expired' | 'unknown'; deadline: string | null; reason: string
  evidence_ids: string[]; verification_scope: 'position'; evaluated_at: string
  current_hiring_status: 'not_verified'
}
export interface ApplicationDateFact {
  date: string | null; year: number | null; month: number; day: number
  year_status: 'explicit' | 'inferred' | 'unknown'; tag: 'source' | 'ai_inferred' | 'unknown'
  time: string | null
}
export interface ApplicationDates { start: ApplicationDateFact | null; end: ApplicationDateFact | null }
export type FactTag = 'source' | 'ai_inferred' | 'unknown'
export interface TaggedFact<T> { value: T | null; tag: FactTag }
export const employmentTypes = ['personnel_agency', 'labor_dispatch', 'labor_contract', 'other'] as const
export type EmploymentType = typeof employmentTypes[number]
export interface PositionFacts {
  schema_version: 'position-facts-v3'
  hiring_unit: TaggedFact<string>; employer: TaggedFact<string>; title: TaggedFact<string>; position_code: TaggedFact<string>
  headcount: TaggedFact<number>; headcount_description: TaggedFact<string>; deadline_on: TaggedFact<string>
  application_rule: TaggedFact<string>; employment_type: TaggedFact<EmploymentType>
  application_start_on: TaggedFact<string>; application_dates: TaggedFact<ApplicationDates>
  staffing_status: TaggedFact<'established' | 'non_established' | 'filing_system' | 'quota_managed'>
  work_location: TaggedFact<string>; requirements_text: TaggedFact<string>
  conditions: PositionCondition[]; condition_logic: ConditionLogic | null
}
export const conditionTypes = ['education', 'degree', 'study_form', 'major', 'age', 'work_experience', 'graduation_status', 'graduation_year', 'political_status', 'license', 'language', 'other'] as const
export const conditionOperators = ['equals', 'at_least', 'at_most', 'in', 'contains', 'unspecified'] as const
export type ConditionValue = string | number | boolean | null | ConditionValue[] | { [key: string]: ConditionValue }
export interface ConditionValues {
  education: { level: 'primary' | 'junior_secondary' | 'senior_secondary' | 'vocational' | 'associate' | 'bachelor' | 'postgraduate' | 'master' | 'doctorate' | 'unrestricted' }
  degree: { level: 'bachelor' | 'master' | 'doctorate' | 'unrestricted' }
  study_form: { form: 'full_time' | 'part_time' | 'unrestricted' }
  major: { names: string[]; unrestricted: boolean }
  age: { minimum: number | null; maximum: number | null; reference_date: string | null }
  work_experience: { minimum_years: number | null; kind: string | null }
  graduation_status: { status: string }; graduation_year: { years: number[] }
  political_status: { statuses: string[] }; license: { names: string[] }
  language: { name: string; requirement: string }; other: { text: string }
}
export type PositionCondition = {
  [K in keyof ConditionValues]: { id: string; type: K; operator: typeof conditionOperators[number];
    scope: 'notice' | 'position'; value: ConditionValues[K] | null; tag: FactTag }
}[keyof ConditionValues]
export type ConditionLogic = { condition_id: string } | { operator: 'all' | 'any'; items: ConditionLogic[] }
export interface Job {
  id: string; source_id: string; source_name: string; official_url: string
  bundle_id: string; recipe_id: string; audit_id: string; admission_audit_id: string | null
  audit_evaluated_on: string; audit_evaluated_at: string; expiry_evaluated_on: string
  expiry_precision: 'day' | 'minute'; last_successful_check: string | null
  display_tags?: JobDisplayTag[]
  extraction_path: 'quick' | 'detailed' | 'migration'
  facts: PositionFacts
  evidence: Record<string, EvidenceBlock[]>; validity: JobValidity
  material_status: 'complete' | 'partial'; extraction_status: 'parsed' | 'needs_review' | 'failed' | 'archived' | 'empty_capture'
  review_status: string; review_required: boolean; repair_required: boolean
  field_issues: { code: string; message: string; path?: string }[]
  coverage_issues: { code: string; message: string; path?: string }[]
  warnings: { code: string; message: string; url?: string }[]
}
export const educationFilters = ['all', 'associate', 'bachelor', 'postgraduate', 'master', 'doctorate', 'unrestricted', 'unknown'] as const
export const employmentFilters = ['all', 'public_institution_staff', ...employmentTypes, 'unknown'] as const
export type EducationFilter = typeof educationFilters[number]
export type EmploymentFilter = typeof employmentFilters[number]
export const deadlineFilters = ['all', 'not_expired', 'expired', 'unknown', 'open_ended', 'until_filled'] as const
export const degreeFilters = ['all', 'bachelor', 'master', 'doctorate', 'unrestricted', 'unknown'] as const
export const staffingFilters = ['all', 'established', 'non_established', 'filing_system', 'quota_managed', 'unknown'] as const
export const experienceFilters = ['all', 'not_required', 'experienced', 'unknown'] as const
export type DeadlineFilter = typeof deadlineFilters[number]
export type DegreeFilter = typeof degreeFilters[number]
export type StaffingFilter = typeof staffingFilters[number]
export type ExperienceFilter = typeof experienceFilters[number]
export const jobFacetValues = { education: educationFilters, employment: employmentFilters, deadline: deadlineFilters, degree: degreeFilters, staffing: staffingFilters, experience: experienceFilters }
export interface JobsFilters { education: EducationFilter; employment: EmploymentFilter; deadline?: DeadlineFilter; degree?: DegreeFilter; staffing?: StaffingFilter; experience?: ExperienceFilter }
export interface JobsPage {
  items: Job[]; page: number; page_size: number; total: number; has_next: boolean
  include_partial: boolean; evaluated_on: string; timezone: 'Asia/Shanghai'
  filters: JobsFilters
}
export interface JobsQuery { page?: number; pageSize?: number; q?: string; includePartial?: boolean; education?: EducationFilter; employment?: EmploymentFilter; deadline?: DeadlineFilter; degree?: DegreeFilter; staffing?: StaffingFilter; experience?: ExperienceFilter }
export interface JobsHttpResponse { statusCode: number; data: unknown }
export type JobsTransport = (url: string) => Promise<JobsHttpResponse>

function invalid(): never { throw new Error('岗位数据格式无效，请稍后重试。') }
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid()
  return value as Record<string, unknown>
}
function text(value: unknown, limit = 100000): string {
  if (typeof value !== 'string' || value.length > limit) return invalid()
  return value
}
function strings(value: unknown): string[] {
  if (!Array.isArray(value) || value.some(v => typeof v !== 'string')) return invalid()
  return value
}
export function isDate(value: unknown): value is string {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const stamp = Date.parse(`${value}T00:00:00Z`)
  return Number.isFinite(stamp) && new Date(stamp).toISOString().slice(0, 10) === value
}
export function officialUrl(value: unknown): string {
  const url = text(value, 4096)
  if (!/^https?:\/\/[^/\s?#@\\]+(?:[/?#][^\s\\]*)?$/.test(url) || /[\u0000-\u001f\u007f]/.test(url)) return invalid()
  return url
}
function stamp(value: unknown): string {
  const valueText = text(value, 64)
  if (!/(Z|[+-]\d{2}:\d{2})$/.test(valueText) || !Number.isFinite(Date.parse(valueText))) return invalid()
  return valueText
}

function tag(value: unknown): void {
  if (!['source', 'ai_inferred', 'unknown'].includes(text(value))) invalid()
}
function tagged(value: unknown): Record<string, unknown> {
  const fact = object(value); tag(fact.tag)
  if (Object.keys(fact).length !== 2 || !('value' in fact) || !('tag' in fact) ||
      (fact.tag === 'unknown') !== (fact.value === null)) invalid()
  return fact
}
function validateConditionValue(kind: PositionCondition['type'], raw: unknown): void {
  if (raw === null) return
  const value = object(raw)
  const keys: Record<PositionCondition['type'], string[]> = {
    education: ['level'], degree: ['level'], study_form: ['form'], major: ['names', 'unrestricted'],
    age: ['minimum', 'maximum', 'reference_date'], work_experience: ['minimum_years', 'kind'],
    graduation_status: ['status'], graduation_year: ['years'], political_status: ['statuses'],
    license: ['names'], language: ['name', 'requirement'], other: ['text'],
  }
  if (Object.keys(value).length !== keys[kind].length || keys[kind].some(key => !(key in value))) invalid()
  const levels = ['primary', 'junior_secondary', 'senior_secondary', 'vocational', 'associate', 'bachelor', 'postgraduate', 'master', 'doctorate', 'unrestricted']
  const bound = (item: unknown, integer = true) => item === null || (typeof item === 'number' && Number.isFinite(item) && item >= 0 && (!integer || Number.isInteger(item)))
  switch (kind) {
    case 'education': if (!levels.includes(text(value.level))) invalid(); break
    case 'degree': if (!['bachelor', 'master', 'doctorate', 'unrestricted'].includes(text(value.level))) invalid(); break
    case 'study_form': if (!['full_time', 'part_time', 'unrestricted'].includes(text(value.form))) invalid(); break
    case 'major':
      strings(value.names)
      if (typeof value.unrestricted !== 'boolean' || (!(value.names as string[]).length && !value.unrestricted)) invalid()
      break
    case 'age':
      if (!bound(value.minimum) || !bound(value.maximum) || (value.minimum === null && value.maximum === null) ||
          (value.minimum !== null && value.maximum !== null && (value.minimum as number) > (value.maximum as number)) ||
          (value.reference_date !== null && !isDate(value.reference_date))) invalid()
      break
    case 'work_experience':
      if (!bound(value.minimum_years, false) || (value.kind !== null && !text(value.kind).trim()) ||
          (value.minimum_years === null && value.kind === null)) invalid()
      break
    case 'graduation_year':
      if (!Array.isArray(value.years) || value.years.some(item => !Number.isInteger(item) || item < 1900 || item > 2200)) invalid()
      break
    case 'political_status': strings(value.statuses); break
    case 'license': strings(value.names); break
    default: for (const item of Object.values(value)) if (!text(item).trim()) invalid()
  }
}
function parsePosition(row: Record<string, unknown>): void {
  const facts = object(row.facts)
  if (facts.schema_version !== 'position-facts-v3') invalid()
  for (const key of ['hiring_unit', 'employer', 'title', 'position_code', 'headcount_description', 'application_rule', 'work_location', 'requirements_text']) {
    const fact = tagged(facts[key])
    if (fact.value !== null && !text(fact.value, key === 'title' ? 256 : 32000).trim()) invalid()
  }
  if (object(facts.title).value === null) invalid()
  if (object(facts.position_code).value !== null) text(object(facts.position_code).value, 128)
  const rule = object(facts.application_rule)
  if (rule.value !== null && !['fixed_deadline', 'open_ended', 'until_filled', 'not_stated'].includes(text(rule.value))) invalid()
  const count = tagged(facts.headcount)
  if (count.value !== null && (!Number.isSafeInteger(count.value) || (count.value as number) < 0)) invalid()
  const deadline = tagged(facts.deadline_on)
  const start = tagged(facts.application_start_on)
  for (const field of [deadline, start]) if (field.value !== null && !isDate(field.value)) invalid()
  const staffing = tagged(facts.staffing_status)
  if (staffing.value !== null && !['established', 'non_established', 'filing_system', 'quota_managed'].includes(text(staffing.value))) invalid()
  const dates = tagged(facts.application_dates)
  if (dates.value !== null) {
    const value = object(dates.value)
    if (Object.keys(value).length !== 2 || !('start' in value) || !('end' in value)) invalid()
    for (const key of ['start', 'end']) {
      if (value[key] === null) continue
      const part = object(value[key])
      if (Object.keys(part).length !== 7 || !['explicit', 'inferred', 'unknown'].includes(text(part.year_status)) ||
          !Number.isInteger(part.month) || !Number.isInteger(part.day) || (part.month as number) < 1 || (part.month as number) > 12 ||
          (part.day as number) < 1 || (part.day as number) > 31) invalid()
      tag(part.tag)
      const year = part.year === null ? 2000 : part.year
      if (!Number.isInteger(year) || (year as number) < 1 || (year as number) > 9999) invalid()
      const full = `${String(year).padStart(4, '0')}-${String(part.month).padStart(2, '0')}-${String(part.day).padStart(2, '0')}`
      if (!isDate(full)) invalid()
      if (part.year_status === 'unknown' ? (part.date !== null || part.year !== null || part.tag !== 'unknown') :
          (part.date !== full || part.year === null || part.tag !== (part.year_status === 'explicit' ? 'source' : 'ai_inferred'))) invalid()
      if (part.time !== null && part.time !== '24:00' && !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(text(part.time))) invalid()
    }
    const end = value.end === null ? null : object(value.end)
    if (deadline.value !== null && (!end || !['explicit', 'inferred'].includes(text(end.year_status)) || end.date !== deadline.value || deadline.tag !== end.tag)) invalid()
  } else if (deadline.value !== null || start.value !== null) invalid()
  const employment = tagged(facts.employment_type)
  if (employment.value !== null && !employmentTypes.includes(employment.value as EmploymentType)) invalid()
  if (!Array.isArray(facts.conditions) || facts.conditions.length > 200) invalid()
  const ids = new Set<string>()
  let valueNodes = 0; let valueChars = 0
  const validateValue = (value: unknown, depth = 0): void => {
    if (++valueNodes > 10000 || depth > 12) invalid()
    if (typeof value === 'string') { valueChars += text(value).length; if (valueChars > 100000) invalid(); return }
    if (value === null || typeof value === 'boolean' || (typeof value === 'number' && Number.isFinite(value))) return
    if (Array.isArray(value)) { value.forEach(item => validateValue(item, depth + 1)); return }
    for (const [key, item] of Object.entries(object(value))) { valueChars += key.length; if (valueChars > 100000) invalid(); validateValue(item, depth + 1) }
  }
  for (const item of facts.conditions as unknown[]) {
    const condition = object(item); const id = text(condition.id, 128)
    if (!id.trim() || ids.has(id) || !conditionTypes.includes(condition.type as PositionCondition['type']) ||
        !conditionOperators.includes(condition.operator as PositionCondition['operator']) ||
        !['notice', 'position'].includes(text(condition.scope))) invalid()
    ids.add(id); tag(condition.tag)
    if ((condition.tag === 'unknown') !== (condition.value === null)) invalid()
    validateValue(condition.value)
    validateConditionValue(condition.type as PositionCondition['type'], condition.value)
  }
  let nodes = 0
  const visited = new Set<string>()
  const logic = (value: unknown, depth = 0): void => {
    if (++nodes > 500 || depth > 12) invalid()
    const node = object(value)
    if ('condition_id' in node) {
      if (Object.keys(node).length !== 1 || !ids.has(text(node.condition_id, 128))) invalid()
      visited.add(node.condition_id as string)
    } else {
      if (Object.keys(node).length !== 2 || !['all', 'any'].includes(text(node.operator)) ||
          !Array.isArray(node.items) || !node.items.length) invalid()
      for (const item of node.items as unknown[]) logic(item, depth + 1)
    }
  }
  if (ids.size === 0 && facts.condition_logic !== null) invalid()
  if (facts.condition_logic !== null) logic(facts.condition_logic)
  if (facts.condition_logic !== null && visited.size !== ids.size) invalid()
}

export function parseJob(value: unknown): Job {
  const row = object(value)
  for (const key of ['id', 'source_id', 'bundle_id', 'recipe_id', 'audit_id']) {
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(text(row[key], 64))) return invalid()
  }
  if (row.admission_audit_id !== null && !/^[A-Za-z0-9_-]{1,64}$/.test(text(row.admission_audit_id, 64))) invalid()
  if (row.display_tags !== undefined && (!Array.isArray(row.display_tags) || row.display_tags.length > jobDisplayTags.length ||
      new Set(row.display_tags).size !== row.display_tags.length || row.display_tags.some(value => !jobDisplayTags.includes(value)))) invalid()
  if (!text(row.source_name, 256).trim()) return invalid()
  parsePosition(row)
  officialUrl(row.official_url)
  for (const key of ['audit_evaluated_on', 'expiry_evaluated_on']) if (!isDate(row[key])) return invalid()
  stamp(row.audit_evaluated_at); if (row.last_successful_check !== null) stamp(row.last_successful_check)
  if (!['complete', 'partial'].includes(text(row.material_status)) || !['parsed', 'needs_review', 'failed', 'archived', 'empty_capture'].includes(text(row.extraction_status)) ||
      !['day', 'minute'].includes(text(row.expiry_precision)) || typeof row.review_required !== 'boolean' || typeof row.repair_required !== 'boolean') return invalid()
  if (!['quick', 'detailed', 'migration'].includes(text(row.extraction_path)) || 'routing' in row || 'routing_category' in row || 'quality_tier' in row) return invalid()
  text(row.review_status)
  for (const key of ['field_issues', 'coverage_issues']) {
    const issues = row[key]
    if (!Array.isArray(issues) || issues.length > 2000) invalid()
    for (const item of issues as unknown[]) {
      const issue = object(item); text(issue.code, 128); text(issue.message)
      if (issue.path !== undefined) text(issue.path, 512)
    }
  }
  const validity = object(row.validity)
  if (!['expired', 'not_expired', 'unknown'].includes(text(validity.status)) ||
      validity.current_hiring_status !== 'not_verified' || validity.verification_scope !== 'position' ||
      (validity.deadline !== null && !isDate(validity.deadline))) return invalid()
  if (Object.keys(validity).some(key => !['status', 'deadline', 'reason', 'evidence_ids', 'evaluated_at', 'current_hiring_status', 'verification_scope'].includes(key))) invalid()
  text(validity.reason); strings(validity.evidence_ids); stamp(validity.evaluated_at)
  if (validity.status === 'unknown' ? validity.deadline !== null :
      (validity.deadline === null || object(object(row.facts).deadline_on).tag !== 'source')) invalid()
  if (validity.deadline !== null && validity.deadline !== object(object(row.facts).deadline_on).value) invalid()
  for (const blocks of Object.values(object(row.evidence))) {
    if (!Array.isArray(blocks)) return invalid()
    for (const value of blocks) { const block = object(value); text(block.id); text(block.text); text(block.locator) }
  }
  if (!Array.isArray(row.warnings)) return invalid()
  for (const value of row.warnings) {
    const warning = object(value); text(warning.code); text(warning.message)
    if (warning.url !== undefined) officialUrl(warning.url)
  }
  return row as unknown as Job
}

export function parseJobsPage(value: unknown, selected?: JobsQuery): JobsPage {
  const row = object(value)
  if (!Array.isArray(row.items) || !Number.isInteger(row.page) || (row.page as number) < 1 || (row.page as number) > 1000 ||
      !Number.isInteger(row.page_size) || (row.page_size as number) < 1 || (row.page_size as number) > 50 ||
      !Number.isSafeInteger(row.total) || (row.total as number) < 0 || typeof row.has_next !== 'boolean' ||
      typeof row.include_partial !== 'boolean' || row.timezone !== 'Asia/Shanghai' || !isDate(row.evaluated_on) ||
      row.items.length > (row.page_size as number) || row.items.length > (row.total as number)) return invalid()
  const filters = object(row.filters)
  if (!educationFilters.includes(filters.education as EducationFilter) ||
      !employmentFilters.includes(filters.employment as EmploymentFilter)) return invalid()
  if (selected && (filters.education !== (selected.education ?? 'all') ||
      filters.employment !== (selected.employment ?? 'all'))) {
    throw new Error('岗位服务返回的筛选条件不一致，请重试或重置筛选。')
  }
  for (const key of ['deadline', 'degree', 'staffing', 'experience'] as const) {
    const actual = filters[key] ?? 'all'
    if (!(jobFacetValues[key] as readonly unknown[]).includes(actual)) return invalid()
    if (selected && actual !== (selected[key] ?? 'all')) throw new Error('岗位服务返回的筛选条件不一致，请重试或重置筛选。')
  }
  if (selected && row.include_partial !== (selected.includePartial !== false)) return invalid()
  const items = row.items.map(parseJob)
  if (items.some(job => (!row.include_partial && job.material_status === 'partial') ||
      job.expiry_evaluated_on !== row.evaluated_on)) return invalid()
  const next = (row.page as number) < 1000 && (row.page as number) * (row.page_size as number) < (row.total as number)
  if (row.has_next !== next) return invalid()
  return { ...row, items } as unknown as JobsPage
}

export function jobsPath(query: JobsQuery = {}): string {
  const page = query.page ?? 1; const size = query.pageSize ?? 20; const raw = query.q ?? ''
  if (!Number.isInteger(page) || page < 1 || page > 1000 || !Number.isInteger(size) || size < 1 || size > 50 ||
      typeof raw !== 'string' || raw.length > 100 || /[\u0000-\u001f\u007f]/.test(raw) ||
      (query.includePartial !== undefined && typeof query.includePartial !== 'boolean') ||
      (query.education !== undefined && !educationFilters.includes(query.education)) ||
      (query.employment !== undefined && !employmentFilters.includes(query.employment))) throw new Error('搜索或分页参数无效。')
  let filters = ''
  for (const key of Object.keys(jobFacetValues) as (keyof typeof jobFacetValues)[]) {
    const selected = query[key]
    if (selected === undefined) continue
    if (!(jobFacetValues[key] as readonly unknown[]).includes(selected)) throw new Error('搜索或筛选参数无效。')
    filters += `&${key}=${encodeURIComponent(selected)}`
  }
  return `/jobs?page=${page}&page_size=${size}&q=${encodeURIComponent(raw.trim())}&include_partial=${query.includePartial !== false}${filters}`
}
/** A real page stack gives WeChat its native back control; only explicit material scope is passed. */
export function jobDetailRoute(id: string, includePartial = true): string {
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(id) || typeof includePartial !== 'boolean') throw new Error('岗位详情参数无效。')
  return `/pages/jobs/detail/index?id=${encodeURIComponent(id)}&include_partial=${includePartial}`
}
export function jobsUrl(base: string, platform: string, path: string): string {
  if (!/^\/jobs(?:\?|\/[A-Za-z0-9_-]{1,64}(?:\?|$)|$)/.test(path)) throw new Error('岗位请求路径无效。')
  if (base.trim()) officialUrl(base.trim())
  return healthUrl(base, platform).replace(/\/health$/, path)
}
const defaultTransport: JobsTransport = async url => {
  const { sendJobs } = await import('./jobs-transport')
  return sendJobs(url)
}
async function read(path: string, transport: JobsTransport, base: string, platform: string): Promise<unknown> {
  const url = jobsUrl(base, platform, path)
  let response: JobsHttpResponse
  try { response = await transport(url) } catch { throw new Error('暂时连接不上岗位服务，请检查网络后重试。') }
  if (response.statusCode !== 200) {
    throw new ApiError(response.statusCode, response.statusCode === 404 ? '岗位已不再公开，请返回列表刷新。' : '岗位服务暂不可用，请稍后重试。')
  }
  return response.data
}
export async function getJobs(query: JobsQuery = {}, transport = defaultTransport,
  base = process.env.TARO_APP_API_BASE_URL || '', platform = process.env.TARO_ENV || 'h5'): Promise<JobsPage> {
  return parseJobsPage(await read(jobsPath(query), transport, base, platform), query)
}
export async function getJob(id: string, includePartial = true, transport = defaultTransport,
  base = process.env.TARO_APP_API_BASE_URL || '', platform = process.env.TARO_ENV || 'h5'): Promise<Job> {
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(id)) throw new Error('岗位编号无效。')
  const job = parseJob(await read(`/jobs/${id}?include_partial=${includePartial}`, transport, base, platform))
  if (job.id !== id || (!includePartial && job.material_status === 'partial')) return invalid()
  return job
}
export function periodLabel(job: Job): string {
  const validity = job.validity
  if (validity.status === 'expired') return '已过固定截止期限'
  if (validity.status === 'not_expired' && validity.deadline) {
    return validity.deadline === job.expiry_evaluated_on ? `截止当日 ${validity.deadline}，请核对具体时间` : `固定截止 ${validity.deadline}`
  }
  const kind = job.facts.application_rule.value
  if (kind === 'open_ended') return '原文声明长期有效，是否仍招聘待核验'
  if (kind === 'until_filled') return '原文声明招满即止，是否招满待核验'
  if (kind === 'not_stated') return '原文未说明报名期限'
  return '报名期限待确认'
}
export function jobCautions(job: Job): string[] {
  const items = ['当前是否仍在招聘尚未核验；不保证报名资格或录取。']
  if (job.repair_required || job.extraction_status === 'failed') items.push('解析未完成，当前展示已保存的岗位信息；待后续补全。')
  if (job.extraction_path === 'migration') items.push('这是历史岗位的结构迁移结果，未重新执行解析工作流；旧截止日期待重新提取。')
  if (job.review_required) items.push('信息待核验指解析结果或招聘条件尚有疑点，不等于截止日期未知，也不代表已人工确认。')
  if (job.review_required && job.admission_audit_id === null) items.push('当前解析版本尚未通过来源审查，岗位先展示已保存的信息。')
  if (job.validity.status === 'expired') items.push('该岗位已过原文明确的截止时间；其他字段的核验状态不会将其重新标为可报名。')
  if (job.display_tags?.includes('cancelled')) items.push('存在取消或撤销标记，请先核对官方最新通知，勿据此报名。')
  if (job.display_tags?.includes('material_stale')) items.push('当前展示上次保存的材料，最新版本或附件尚待重新解析。')
  if (job.material_status === 'partial') items.push('材料不完整，未取得的附件或更正可能影响报名条件。')
  if (job.field_issues.length) items.push('部分字段仍有解析疑点，其他已识别信息保留，请核对相应条件。')
  if (job.validity.status === 'unknown') items.push('不能依据当前材料确定是否已截止，请核对官方原文。')
  if (job.facts.staffing_status.value === null || job.facts.staffing_status.tag === 'unknown') items.push('岗位性质待确认，不能默认有事业编制。')
  if (job.facts.employment_type.tag === 'ai_inferred') items.push('用工性质为 AI 推断，请核对官方说明，不保证事业编制或聘用身份。')
  if (job.facts.deadline_on.tag === 'ai_inferred') items.push('推断截止日期不作为有效期判断依据，请核对报名期限。')
  return items
}
export function evidenceRows(job: Job): { field: string; block: EvidenceBlock }[] {
  return Object.entries(job.evidence).flatMap(([field, blocks]) => blocks.map(block => ({ field, block })))
}

/** Display labels are independent of recipe admission and field provenance. */
export const jobDisplayLabels = {
  migrated: '历史迁移', expired: '已截止', deadline_not_passed: '未到截止日', deadline_unknown: '期限未知',
  cancelled: '取消 / 撤销', parse_failed: '解析未完成', partial_parse: '部分解析',
  needs_review: '信息待核验', material_incomplete: '材料不全', material_stale: '材料待更新',
  refresh_pending: '采集待更新', ai_inferred: '含 AI 推断', fields_unknown: '含未知字段',
} as const
export type JobDisplayTag = keyof typeof jobDisplayLabels
export const jobDisplayTags = Object.keys(jobDisplayLabels) as JobDisplayTag[]
export function jobTags(job: Job): { code: JobDisplayTag; label: string }[] {
  const codes = new Set<JobDisplayTag>(job.display_tags || [])
  if (job.extraction_path === 'migration') codes.add('migrated')
  codes.add(job.validity.status === 'expired' ? 'expired' : job.validity.status === 'not_expired' ? 'deadline_not_passed' : 'deadline_unknown')
  if (job.repair_required || job.extraction_status === 'failed') codes.add('parse_failed')
  else if (job.extraction_status !== 'parsed' || job.coverage_issues.length) codes.add('partial_parse')
  if (job.review_required) codes.add('needs_review')
  if (job.material_status === 'partial') codes.add('material_incomplete')
  const facts = [...Object.values(job.facts).filter(value => value && typeof value === 'object' && 'tag' in value), ...job.facts.conditions] as { tag: FactTag }[]
  if (facts.some(value => value.tag === 'ai_inferred')) codes.add('ai_inferred')
  if (facts.some(value => value.tag === 'unknown')) codes.add('fields_unknown')
  return jobDisplayTags.filter(code => codes.has(code)).map(code => ({ code, label: jobDisplayLabels[code] }))
}


/** Separate dimensions, rather than a paragraph mixing dates and parsing review. */
export function jobReviewRows(job: Job): { label: string; value: string }[] {
  const rows = [{ label: '信息状态', value: job.review_required ? '信息待核验' : '未发现解析疑点' }]
  if (job.review_required && job.admission_audit_id === null) rows.push({ label: '解析版本', value: '尚待来源审查' })
  if (job.field_issues.length) rows.push({ label: '字段疑点', value: `${job.field_issues.length} 项，已识别内容保留` })
  if (job.repair_required || job.extraction_status === 'failed') rows.push({ label: '解析状态', value: '未完成，等待补全' })
  rows.push({ label: '材料范围', value: job.material_status === 'partial' ? '部分材料可用，附件尚不完整' : '已保存材料完整' })
  if (job.display_tags?.includes('cancelled')) rows.push({ label: '后续通知', value: '存在取消或撤销标记' })
  if (job.display_tags?.includes('material_stale')) rows.push({ label: '材料更新', value: '当前展示上次保存的版本' })
  rows.push({ label: '招聘状态', value: '当前空缺尚未核验' })
  return rows
}
