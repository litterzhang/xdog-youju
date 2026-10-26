import type { Job, ConditionLogic, ConditionValue, EmploymentType, FactTag, PositionCondition, TaggedFact } from '../../services/jobs'

/** Presentation only: unknown facts remain unchanged in storage and feedback. */
export function hasFactValue<T>(fact: TaggedFact<T>): fact is TaggedFact<T> & { value: T } {
  return fact.tag !== 'unknown' && fact.value !== null &&
    (typeof fact.value !== 'string' || fact.value.trim().length > 0)
}
const businessFields = [
  'hiring_unit', 'employer', 'position_code', 'headcount', 'headcount_description',
  'work_location', 'employment_type', 'staffing_status', 'application_start_on',
  'deadline_on', 'application_rule',
] as const
export function hasBusinessFacts(job: Job): boolean {
  return businessFields.some(key => hasFactValue<string | number>(job.facts[key]))
}

export const tagLabels: Record<FactTag, string> = { source: '原文明确', ai_inferred: 'AI 推断', unknown: '未知' }
export const employmentLabels: Record<EmploymentType, string> = {
  personnel_agency: '人事代理', labor_dispatch: '劳务派遣',
  labor_contract: '劳动合同制', other: '其他条件',
}
export const applicationRuleLabels: Record<string, string> = {
  fixed_deadline: '固定截止日期', open_ended: '长期有效', until_filled: '招满为止', not_stated: '未声明报名期限',
}
export const fieldLabels: Record<string, string> = {
  position_code: '岗位代码', title: '岗位', headcount: '计划人数（非当前空缺）', headcount_description: '人数说明', hiring_unit: '招聘单位',
  staffing_status: '编制身份', application_start_on: '报名开始', employer: '合同单位', deadline_on: '截止日期', application_rule: '报名规则', employment_type: '用工形式',
  work_location: '工作地点', requirements_text: '其他要求原文', conditions: '资格条件', validity: '期限判断',
}
export const conditionLabels: Record<PositionCondition['type'], string> = {
  education: '学历', degree: '学位', study_form: '学习形式', major: '专业', age: '年龄', work_experience: '工作经验',
  graduation_status: '毕业身份', graduation_year: '毕业年份', political_status: '政治面貌', license: '资格证书',
  language: '语言', other: '其他条件',
}
const operatorLabels: Record<PositionCondition['operator'], string> = {
  equals: '等于', at_least: '至少', at_most: '至多', in: '属于以下范围', contains: '包含', unspecified: '关系未明确',
}
export function factText<T extends string | number>(fact: TaggedFact<T>, format: (value: T) => string = String): string {
  return `${fact.value === null ? '未识别，请核对原文' : format(fact.value)} [${tagLabels[fact.tag]}]`
}
const normalizedLabels: Record<string, string> = {
  primary: '小学', junior_secondary: '初中', senior_secondary: '高中', vocational: '中专',
  associate: '专科', bachelor: '本科', postgraduate: '研究生', master: '硕士', doctorate: '博士',
  unrestricted: '不限', full_time: '全日制', part_time: '非全日制',
}
export const staffingLabels: Record<string, string> = {
  established: '事业编制', non_established: '编外', filing_system: '备案制', quota_managed: '员额制',
}
export function conditionValueText(value: ConditionValue, kind?: PositionCondition['type']): string {
  if (value === null) return '未识别，请核对原文'
  if (typeof value !== 'object' || Array.isArray(value)) return typeof value === 'string' ? value : JSON.stringify(value)
  if ('level' in value || 'form' in value) {
    const code = String(value.level ?? value.form)
    if (kind === 'degree') return ({bachelor:'学士',master:'硕士',doctorate:'博士',unrestricted:'不限'} as Record<string,string>)[code] || code
    return normalizedLabels[code] || code
  }
  if ('minimum_years' in value) return value.minimum_years === 0 ? '明确无需工作经验' : `${value.kind ? `${value.kind} · ` : ''}${value.minimum_years === null ? '年限未明确' : `至少 ${value.minimum_years} 年`}`
  if ('names' in value) return value.unrestricted === true ? '不限' : (value.names as string[]).join('、')
  if ('minimum' in value) return `${value.minimum === null ? '' : `${value.minimum} 岁起`}${value.maximum === null ? '' : ` 至 ${value.maximum} 岁`}${value.reference_date ? `，计算基准 ${value.reference_date}` : ''}`
  if ('years' in value) return (value.years as number[]).join('、')
  if ('statuses' in value) return (value.statuses as string[]).join('、')
  if ('text' in value) return String(value.text)
  return Object.values(value).filter(item => item !== null).join(' · ')
}
export function conditionText(condition: PositionCondition): string {
  return `${conditionLabels[condition.type]} · ${condition.scope === 'notice' ? '公告通用' : '岗位专属'} · ${operatorLabels[condition.operator]}：${conditionValueText(condition.value, condition.type)} [${tagLabels[condition.tag]}]`
}
/** References only: leaves are never interpreted as conditions or eligibility decisions. */
export function conditionLogicText(logic: ConditionLogic | null, conditions: PositionCondition[]): string {
  if (logic === null) return '组合关系未明确，不能默认全部满足或任一满足，请核对原文。'
  const render = (node: ConditionLogic): string => {
    if ('condition_id' in node) return conditionLabels[conditions.find(item => item.id === node.condition_id)!.type]
    return `（${node.items.map(render).join(node.operator === 'all' ? ' 且 ' : ' 或 ')}）`
  }
  return render(logic)
}


export function conditionDisplayValue(condition: PositionCondition): string {
  const value = conditionValueText(condition.value, condition.type)
  if (condition.value && ['education', 'degree'].includes(condition.type)) {
    if (condition.operator === 'at_least') return `${value}及以上`
    if (condition.operator === 'at_most') return `${value}及以下`
  }
  return value
}
