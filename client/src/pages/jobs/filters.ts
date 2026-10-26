import type { EducationFilter, EmploymentFilter, DeadlineFilter, DegreeFilter, StaffingFilter, ExperienceFilter, JobsQuery } from '../../services/jobs'

type Choice<T> = { value: T; label: string }
export const educationChoices: Choice<EducationFilter>[] = [
  { value: 'all', label: '全部学历' }, { value: 'associate', label: '专科' }, { value: 'bachelor', label: '本科' },
  { value: 'postgraduate', label: '研究生' }, { value: 'master', label: '硕士研究生' }, { value: 'doctorate', label: '博士研究生' },
  { value: 'unrestricted', label: '学历不限' }, { value: 'unknown', label: '学历未识别' },
]
export const employmentChoices: Choice<EmploymentFilter>[] = [
  { value: 'all', label: '全部用工' }, { value: 'personnel_agency', label: '人事代理' },
  { value: 'labor_dispatch', label: '劳务派遣' }, { value: 'labor_contract', label: '劳动合同制' },
  { value: 'other', label: '其他用工' }, { value: 'unknown', label: '用工未识别' },
]
export const deadlineChoices: Choice<DeadlineFilter>[] = [
  { value: 'all', label: '全部期限' }, { value: 'not_expired', label: '未到截止日' },
  { value: 'expired', label: '已截止' }, { value: 'open_ended', label: '长期有效声明' },
  { value: 'until_filled', label: '招满即止' }, { value: 'unknown', label: '截止时间未知' },
]
export const degreeChoices: Choice<DegreeFilter>[] = [
  { value: 'all', label: '全部学位' }, { value: 'bachelor', label: '学士' }, { value: 'master', label: '硕士' },
  { value: 'doctorate', label: '博士' }, { value: 'unrestricted', label: '学位不限' }, { value: 'unknown', label: '学位未识别' },
]
export const staffingChoices: Choice<StaffingFilter>[] = [
  { value: 'all', label: '全部编制' }, { value: 'established', label: '事业编制' }, { value: 'non_established', label: '编外' },
  { value: 'filing_system', label: '备案制' }, { value: 'quota_managed', label: '员额制' }, { value: 'unknown', label: '编制未识别' },
]
export const experienceChoices: Choice<ExperienceFilter>[] = [
  { value: 'all', label: '全部经验' }, { value: 'not_required', label: '明确无需经验' },
  { value: 'experienced', label: '要求工作经验' }, { value: 'unknown', label: '经验未识别' },
]
export const materialChoices = ['仅完整材料', '全部材料']
export const facetChoices = { deadline: deadlineChoices, education: educationChoices, employment: employmentChoices, degree: degreeChoices, staffing: staffingChoices, experience: experienceChoices }
export type FacetKey = keyof typeof facetChoices
export const facetLabels: Record<FacetKey, string> = { deadline: '报名期限', education: '学历要求', employment: '用工形式', degree: '学位要求', staffing: '编制身份', experience: '工作经验' }
export function initialJobsQuery(): JobsQuery {
  return { page: 1, pageSize: 20, q: '', includePartial: true, education: 'all', employment: 'all', deadline: 'all', degree: 'all', staffing: 'all', experience: 'all' }
}
export function changeJobsFilters(query: JobsQuery, filters: Partial<Pick<JobsQuery, FacetKey | 'includePartial'>>): JobsQuery {
  return { ...query, ...filters, page: 1 }
}
export function activeJobFilters(query: JobsQuery): { key: FacetKey | 'q' | 'includePartial'; label: string }[] {
  const result: { key: FacetKey | 'q' | 'includePartial'; label: string }[] = []
  if (query.q) result.push({ key: 'q', label: query.q })
  for (const key of Object.keys(facetChoices) as FacetKey[]) {
    if (query[key] && query[key] !== 'all') result.push({ key, label: facetChoices[key].find(c => c.value === query[key])!.label })
  }
  if (query.includePartial === false) result.push({ key: 'includePartial', label: '仅完整材料' })
  return result
}
export function resultCountLabel(total: number | null, filtered: boolean, loading: boolean, error = ''): string {
  if (loading) return filtered ? '正在筛选…' : '正在读取…'
  if (error) return '结果未更新'
  if (total === null) return '岗位列表'
  return `${filtered ? '找到' : '全部'} ${total.toLocaleString('zh-CN')} 个岗位`
}

/** Reset only facets; searching and filtering are independent user actions. */
export function resetJobsFilters(query: JobsQuery): JobsQuery {
  return { ...initialJobsQuery(), q: query.q ?? '', pageSize: query.pageSize ?? 20 }
}
/** One commit, preserving the current search even if it changed while the sheet was open. */
export function applyJobsFilters(query: JobsQuery, pending: JobsQuery): JobsQuery {
  return { ...query, ...pending, q: query.q, pageSize: query.pageSize, page: 1 }
}
