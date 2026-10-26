import type { EmploymentType, TaggedFact } from './jobs'
export interface Identity { id: string; permissions: string[]; username?: string; nickname?: string; avatar?: string }
export function parseIdentity(value: unknown): Identity {
  if (!value || typeof value !== 'object') throw new Error('登录身份响应无效。')
  const item = value as Record<string, unknown>
  if (typeof item.id !== 'string' || !item.id.trim() || item.id.length > 256 ||
      !Array.isArray(item.permissions) || item.permissions.length > 50 ||
      item.permissions.some(p => typeof p !== 'string')) throw new Error('登录身份响应无效。')
  const identity: Identity = { id: item.id, permissions: item.permissions as string[] }
  for (const key of ['username', 'nickname'] as const) {
    const name = item[key]
    if (typeof name === 'string' && name.trim() && name.trim().length <= 128 &&
        !/[\u0000-\u001f\u007f]/.test(name)) identity[key] = name.trim()
  }
  if (typeof item.avatar === 'string' && item.avatar.trim().length <= 2048 &&
      /^https:\/\/[^/\s?#@\\]+(?:[/?#][^\s\\]*)?$/.test(item.avatar.trim()) &&
      !/[\u0000-\u001f\u007f]/.test(item.avatar)) identity.avatar = item.avatar.trim()
  return identity
}
export function accountDisplayName(identity: Identity): string {
  return identity.nickname || identity.username || '已登录'
}
export function canManageSources(identity: Identity | null): boolean {
  return !!identity?.permissions.includes('youju:source:manage')
}
export interface Region { code: string; name: string }
export interface Organization { id: string; name: string; root_domain: string | null; organization_type: string; location_region_code: string | null }
export interface RegistryPage<T> { items: T[]; page: number; total: number }
export interface RecoveryOptions {
  limits: { max_depth: number; max_resources: number }
  suggested_limits: Partial<{ max_depth: number; max_resources: number }>
  blocking_limits: string[]; depth_limited_resources: number
  can_resume: boolean; can_repair: boolean; requires_operator: boolean
}
export interface SourceError { code: string }
/** Administrative cost hints only; never notice eligibility or exact dates. */
export type RoutingCategory = 'fixed_active' | 'fixed_expired' | 'undated_low_priority' | 'undated_high_priority' | 'unknown'
export type ExtractionPath = 'quick' | 'detailed'
export interface AuditSummary {
  total_announcements?: number; processed?: number; remaining?: number
  statuses?: Record<string, number>
  routing_categories?: Partial<Record<RoutingCategory, number>>; extraction_paths?: Partial<Record<ExtractionPath, number>>
  position_count?: number; partial_position_count?: number
  errors_by_code?: Record<string, number>; warnings_by_code?: Record<string, number>
  evaluated_on?: string; passed?: boolean
  archived_announcements?: number
  field_issues_by_code?: Record<string, number>; coverage_issues_by_code?: Record<string, number>
  extraction_statuses?: Record<string, number>; material_statuses?: Record<string, number>
}
export interface IncrementalDelta {
  state: 'ready' | 'pending' | 'unavailable'; reason: string | null
  notice_count: number | null; position_count: number | null
  new_notices: number | null; changed_notices: number | null
}
export interface IncrementalRun {
  id: string; status: 'running' | 'complete' | 'blocked'; started_at: string | null; finished_at: string | null
  summary: { delta: IncrementalDelta; error?: SourceError; repair_request_ids?: string[] }
  error?: SourceError
}
export interface IncrementalPosition {
  id: string
  facts: { title: TaggedFact<string>; hiring_unit: TaggedFact<string>; headcount: TaggedFact<number>;
    deadline_on: TaggedFact<string>; employment_type: TaggedFact<EmploymentType> }
}
export interface IncrementalNotice {
  bundle_id: string; resource_id: string; title: string; url: string; change: 'new' | 'changed'
  status: string; material_status: string; position_count: number
  extraction_path: ExtractionPath | null; routing_category: RoutingCategory | null
  field_issue_codes: string[]; coverage_issue_codes: string[]; review_required: boolean
  positions: IncrementalPosition[]; positions_truncated: boolean; positions_unavailable: boolean
  error_codes: string[]
}
export interface IncrementalRunDetail extends IncrementalRun {
  notices: { items: IncrementalNotice[]; page: number; page_size: number; total: number; has_next: boolean }
}
export interface RecipeRepair {
  id: string; origin: string
  status: 'queued' | 'preparing' | 'submitted' | 'auditing' | 'accepting' | 'rejecting' | 'succeeded' | 'failed' | 'superseded'
  base_recipe_id: string; candidate_recipe_id: string | null
  task_id: string | null; error_code: string | null; created_at: string; updated_at: string
}
export function repairStatusLabel(status: string): string {
  return ({ queued: '等待修复', preparing: '准备固定输入', submitted: 'Agent 生成中', auditing: '全量回归审查', accepting: '同步采用结果', rejecting: '同步拒绝结果', succeeded: '已采用新版本', failed: '修复未通过', superseded: '输入或版本已变化' } as Record<string, string>)[status] || '修复状态未知'
}
export interface Incremental {
  enabled: boolean; state: 'disabled' | 'idle' | 'running' | 'blocked'
  repairs?: RecipeRepair[]
  latest_run?: IncrementalRun | null; error?: SourceError
}
export interface IncrementalDetails extends Incremental { runs: IncrementalRun[] }
export interface Onboarding {
  active_revision_id?: string | null; candidate_revision_id?: string | null; generation?: number
  recovery?: RecoveryOptions
  stage: string; state: string; acquisition: Record<string, number>
  summary: {
    inventory_incomplete?: boolean
    error?: { code: string }
    authoring_job?: { id?: string; status?: string }
    classification?: Record<string, unknown>
    audit?: AuditSummary
  }
  updated_at?: string; activated_at?: string | null; completed_at?: string | null
  baseline_recipe_id?: string | null; baseline_audit_id?: string | null; provenance?: string | null
  maintenance?: { stage: string; state: string; error?: { code: string | null } } | null
}
export interface SourceParsingSummary {
  source_id: string; scope: 'current_saved_notices'; state: 'ready' | 'pending'
  notice_count: number; position_count: number
  statuses: Record<'success' | 'partial' | 'review' | 'failed' | 'pending', number> & { empty?: number; archived?: number; cancelled?: number; associated?: number }
  extraction_paths: Partial<Record<ExtractionPath, number>>; routing_categories: Partial<Record<RoutingCategory, number>>
  field_issues_by_code: Record<string, number>; coverage_issues_by_code: Record<string, number>; review_required: number
  unclassified_pages: number; recipe_id: string | null
  evaluated_from: string | null; evaluated_through: string | null; summarized_at: string
}
export interface Source {
  id: string; name: string; organization_id: string; organization_name: string | null
  base_url: string; description: string; status: string; region_codes: string[]
  authority_evidence: string | null; verified_at: string | null
  seeds: { name: string; url: string }[]; onboarding: Onboarding; incremental?: Incremental
}
const stages: Record<string, string> = {
  not_started: '未启动', inventory: '采集全站链接与材料', classification: '判断页面类型',
  authoring: '生成解析脚本与工作流', audit: '全量公告审计', completed: '接入完成', active: '接入完成',
}
const states: Record<string, string> = { not_started: '未启动', ready: '等待任务推进', running: '任务处理中', blocked: '已阻塞，需处理', completed: '已完成' }
export function stageLabel(value: string): string { return stages[value] || '未知阶段' }
export function stateLabel(value: string): string { return states[value] || '未知状态' }
export function lines(value: string): string[] { return value.split(/\r?\n/).map(v => v.trim()).filter(Boolean) }
export function routingCategoryLabel(value: RoutingCategory | null): string {
  return value ? ({
    fixed_active: '截止线索未过（仅成本路由）', fixed_expired: '截止线索已过（仅成本路由）',
    undated_low_priority: '无日期低优先级（仅成本路由）', undated_high_priority: '无日期高优先级（仅成本路由）',
    unknown: '成本路由未知',
  })[value] || '成本路由待核验' : '成本路由待核验'
}
export function counts(value: Record<string, number> | undefined): string {
  if (!value || !Object.keys(value).length) return '暂无统计'
  const labels: Record<string, string> = {
    ready: '待采集', running: '采集中', done: '已采集', review: '待复核',
    failed: '失败', success: '完整解析', partial: '部分材料解析',
    quick: '低成本路径（非期限结论）', detailed: '详细路径', unknown: '未知',
    fixed_active: '截止线索未过（仅成本路由）', fixed_expired: '截止线索已过（仅成本路由）',
    undated_low_priority: '无日期低优先级（仅成本路由）', undated_high_priority: '无日期高优先级（仅成本路由）',
    empty: '当前材料无岗位行', empty_capture: '空表材料',
    archived: '低成本归档（无岗位事实）', parsed: '已提取岗位事实', needs_review: '待复核',
    complete: '材料完整', incomplete: '材料不完整',
  }
  return Object.entries(value).map(([key, count]) => `${labels[key] || key} ${count}`).join(' · ')
}

export function registryStatusLabel(value: string): string {
  return ({ candidate: '来源待审核', active: '官方来源已批准', paused: '来源已暂停', retired: '来源已停用' } as Record<string, string>)[value] || '来源状态未知'
}
