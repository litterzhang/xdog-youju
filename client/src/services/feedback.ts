/** Issue bookkeeping only. This client never calls Recipe, repair, crawl or model APIs. */
export const feedbackFields = ['title', 'headcount', 'hiring_unit', 'employer', 'deadline_on', 'employment_type', 'work_location', 'conditions', 'requirements_text', 'validity', 'other'] as const
export type FeedbackField = typeof feedbackFields[number]
export const feedbackFieldLabels: Record<FeedbackField, string> = {
  title: '岗位名称', headcount: '计划人数', hiring_unit: '招聘单位', employer: '用人单位',
  deadline_on: '截止日期', employment_type: '用工形式', work_location: '工作地点', conditions: '资格条件及逻辑',
  requirements_text: '其他要求原文', validity: '期限判断', other: '其他问题',
}
export const feedbackStatuses = ['open', 'reviewed', 'resolved', 'dismissed'] as const
export type FeedbackStatus = typeof feedbackStatuses[number]
export type ReviewStatus = Exclude<FeedbackStatus, 'open'>
export type FeedbackFilter = FeedbackStatus | 'all'
export const feedbackStatusLabels: Record<FeedbackStatus, string> = {
  open: '待复核', reviewed: '已复核（记录状态）', resolved: '已处理（记录状态）', dismissed: '无需修改（记录状态）',
}
export const feedbackDisclaimer = '反馈仅记录问题，不会自动修改数据、修复 Recipe、重新抓取或调用模型。状态标记不代表已核验数据更正，请继续核对官方原文。'
export const feedbackPrivacyWarning = '请勿填写身份证号、电话、简历、账号凭据或其他私人及保密信息。'
export interface ParsingFeedback {
  id: string; position_id: string; source_id: string; field: FeedbackField; message: string
  expected_text: string | null; status: FeedbackStatus; resolution_note: string | null
  created_at: string; updated_at: string
  context: {
    title: string; official_url: string; bundle_id: string; recipe_id: string; audit_id: string
    audit_evaluated_on: string; field_value: unknown; evidence: unknown; requirements_text?: string | null
  }
}
export interface FeedbackPage { items: ParsingFeedback[]; page: number; total: number }
export interface FeedbackInput { field: FeedbackField; message: string; expected_text?: string }
export const feedbackPageSize = 50
const legalId = /^[A-Za-z0-9_-]{1,64}$(?![\s\S])/
function id(value: string): string {
  if (typeof value !== 'string' || !legalId.test(value)) throw new Error('反馈标识无效，请从岗位详情或来源管理重新进入。')
  return value
}
function invalid(): never { throw new Error('反馈数据格式无效，请刷新后重试。') }
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid()
  return value as Record<string, unknown>
}
function text(value: unknown, limit: number, nonempty = false): string {
  if (typeof value !== 'string' || value.length > limit * 2 || Array.from(value).length > limit || (nonempty && !value.trim())) return invalid()
  return value
}
function timestamp(value: unknown): void {
  if (!/^\d{4}-\d{2}-\d{2}T/.test(text(value, 64)) || !Number.isFinite(Date.parse(value as string))) invalid()
}
function date(value: unknown): void {
  const day = text(value, 10)
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || !Number.isFinite(Date.parse(`${day}T00:00:00Z`)) ||
      new Date(`${day}T00:00:00Z`).toISOString().slice(0, 10) !== day) invalid()
}
/** Bound opaque JSON before rendering; don't execute or interpret material as instructions. */
function boundedJson(value: unknown): void {
  let nodes = 0; let chars = 0
  const visit = (item: unknown, depth: number) => {
    if (++nodes > 5000 || depth > 12) invalid()
    if (typeof item === 'string') { chars += item.length; if (chars > 100000) invalid(); return }
    if (item === null || typeof item === 'boolean' || (typeof item === 'number' && Number.isFinite(item))) return
    if (Array.isArray(item)) { if (item.length > 5000) invalid(); item.forEach(child => visit(child, depth + 1)); return }
    const row = object(item)
    const keys = Object.keys(row)
    if (keys.length > 5000) invalid()
    for (const key of keys) { chars += key.length; if (chars > 100000) invalid(); visit(row[key], depth + 1) }
  }
  visit(value, 0)
}
export function parseFeedback(value: unknown): ParsingFeedback {
  const row = object(value)
  for (const key of ['id', 'position_id', 'source_id']) if (!legalId.test(text(row[key], 64))) invalid()
  if (!feedbackFields.includes(row.field as FeedbackField) || !feedbackStatuses.includes(row.status as FeedbackStatus)) invalid()
  text(row.message, 1000, true)
  if (row.expected_text !== null) text(row.expected_text, 500)
  if (row.resolution_note !== null) text(row.resolution_note, 1000, true)
  if (row.status !== 'open' && row.resolution_note === null) invalid()
  timestamp(row.created_at); timestamp(row.updated_at)
  const context = object(row.context)
  text(context.title, 256, true)
  const url = text(context.official_url, 4096, true)
  if (!/^https?:\/\/[^/\s?#@\\]+(?:[/?#][^\s\\]*)?$/.test(url) || /[\u0000-\u001f\u007f]/.test(url)) invalid()
  for (const key of ['bundle_id', 'recipe_id', 'audit_id']) if (!legalId.test(text(context[key], 64))) invalid()
  date(context.audit_evaluated_on)
  boundedJson(context.field_value); boundedJson(context.evidence)
  if (context.requirements_text !== undefined && context.requirements_text !== null) text(context.requirements_text, 100000)
  return row as unknown as ParsingFeedback
}
export function parseFeedbackPage(value: unknown): FeedbackPage {
  const row = object(value)
  if (!Array.isArray(row.items) || row.items.length > feedbackPageSize || !Number.isInteger(row.page) ||
      (row.page as number) < 1 || (row.page as number) > 1000 || !Number.isSafeInteger(row.total) ||
      (row.total as number) < 0 || row.items.length > (row.total as number)) invalid()
  return { items: row.items.map(parseFeedback), page: row.page as number, total: row.total as number }
}
export function feedbackBody(input: FeedbackInput): Record<string, unknown> {
  if (!feedbackFields.includes(input.field)) throw new Error('请选择支持反馈的字段。')
  if (typeof input.message !== 'string' || !input.message.trim() || Array.from(input.message).length > 1000) throw new Error('问题描述需为 1–1000 字。')
  if (input.expected_text !== undefined && (typeof input.expected_text !== 'string' || Array.from(input.expected_text).length > 500)) throw new Error('预期原文最多 500 字。')
  // Keep the literal text; no inferred correction and no client-supplied ownership/context IDs.
  return { field: input.field, message: input.message, ...(input.expected_text === undefined ? {} : { expected_text: input.expected_text }) }
}
export function reviewBody(status: ReviewStatus, note: string): Record<string, unknown> {
  if (!['reviewed', 'resolved', 'dismissed'].includes(status)) throw new Error('请选择允许的记录状态。')
  if (typeof note !== 'string' || !note.trim() || Array.from(note).length > 1000) throw new Error('处理说明必填，需为 1–1000 字。')
  return { status, resolution_note: note.trim() }
}
export function reviewConfirmation(status: ReviewStatus) {
  reviewBody(status, '核验')
  return { title: '确认记录状态', content: `将问题记录标记为「${feedbackStatusLabels[status]}」并保存处理说明？${feedbackDisclaimer}` }
}
export function feedbackRoute(sourceId: string): string { return `/pages/admin/feedback/index?id=${id(sourceId)}` }
export function feedbackHasNext(page: FeedbackPage): boolean { return page.page < 1000 && page.page * feedbackPageSize < page.total }
export function feedbackSnapshotText(value: unknown): string {
  if (value === null || value === undefined) return '未识别 / 未记录，不推断'
  try {
    boundedJson(value)
    const result = typeof value === 'string' ? value : JSON.stringify(value, null, 2)
    return result.length > 6000 ? `${result.slice(0, 6000)}\n（展示已截断，请核对原始材料）` : result
  } catch { return '快照格式无效或超出展示限制，请人工核查。' }
}
type FeedbackRequest = <T>(path: string, data?: Record<string, unknown>) => Promise<T>
/** Inject the existing authenticated request (including same-identity automatic renewal). */
export function createFeedbackClient(request: FeedbackRequest) {
  return {
    async submit(positionId: string, input: FeedbackInput): Promise<ParsingFeedback> {
      const row = parseFeedback(await request(`/jobs/${id(positionId)}/feedback`, feedbackBody(input)))
      if (row.position_id !== positionId) invalid()
      return row
    },
    async own(positionId: string): Promise<FeedbackPage> {
      const page = parseFeedbackPage(await request(`/jobs/${id(positionId)}/feedback`))
      if (page.page !== 1 || page.items.some(row => row.position_id !== positionId)) invalid()
      return page
    },
    async list(sourceId: string, page = 1, status: FeedbackFilter = 'all'): Promise<FeedbackPage> {
      if (!Number.isInteger(page) || page < 1 || page > 1000 || !['all', ...feedbackStatuses].includes(status)) throw new Error('反馈分页或状态参数无效。')
      const result = parseFeedbackPage(await request(`/admin/sources/${id(sourceId)}/feedback?page=${page}&status=${status}`))
      if (result.page !== page || result.items.some(row => row.source_id !== sourceId || (status !== 'all' && row.status !== status))) invalid()
      return result
    },
    async review(sourceId: string, feedbackId: string, status: ReviewStatus, note: string): Promise<ParsingFeedback> {
      const body = reviewBody(status, note)
      const row = parseFeedback(await request(`/admin/sources/${id(sourceId)}/feedback/${id(feedbackId)}/review`, body))
      if (row.source_id !== sourceId || row.id !== feedbackId || row.status !== status || row.resolution_note !== body.resolution_note) invalid()
      return row
    },
  }
}
