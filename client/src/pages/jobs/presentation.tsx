import { Text, View } from '@tarojs/components'
import type { Job, PositionCondition, TaggedFact } from '../../services/jobs'
import { periodLabel, jobTags } from '../../services/jobs'
import { applicationRuleLabels, hasFactValue, hasBusinessFacts, conditionLabels, conditionDisplayValue, employmentLabels, staffingLabels, tagLabels } from './position-display'

import { conditionGroupTree } from './condition-groups'
import type { ConditionGroupNode } from './condition-groups'

export { fieldLabels } from './position-display'
export function FactRow({ label, fact, format = String }: { label: string; fact: TaggedFact<string | number>; format?: (value: string | number) => string }) {
  if (!hasFactValue(fact)) return null
  return <View className='job-fact-row'>
    <Text className='job-fact-label'>{label}</Text>
    <View className='job-fact-content'>
      <Text className='job-fact-value' selectable>{format(fact.value)}</Text>
      <Text className={`job-origin-tag job-origin--${fact.tag}`}>{tagLabels[fact.tag]}</Text>
    </View>
  </View>
}
export function JobFacts({ job }: { job: Job }) {
  const unit = hasFactValue(job.facts.hiring_unit) ? job.facts.hiring_unit : job.facts.employer
  const hasPeriod = job.validity.status !== 'unknown' || ['open_ended', 'until_filled', 'not_stated'].includes(job.facts.application_rule.value || '')
  return <>
    <View className='job-detail-title-line'><Text className='job-detail-title'>{job.facts.title.value ?? '岗位名称未识别'}</Text><Text className='job-origin-tag'>{tagLabels[job.facts.title.tag]}</Text></View>
    {hasFactValue(unit) && <Text className='job-detail-unit'>{unit.value}</Text>}
    <View className='jobs-status-tags'>{jobTags(job).map(tag => <Text className={`jobs-material-tag jobs-tag--${tag.code}`} key={tag.code}>{tag.label}</Text>)}</View>
    {hasPeriod && <Text className='job-detail-deadline'>{periodLabel(job)}</Text>}
  </>
}
export function JobBusinessDetails({ job }: { job: Job }) {
  if (!hasBusinessFacts(job)) return null
  return <View className='job-facts-grid'>
    <FactRow label='招聘单位' fact={job.facts.hiring_unit} />
    <FactRow label='合同单位' fact={job.facts.employer} />
    <FactRow label='岗位代码' fact={job.facts.position_code} />
    <FactRow label='计划人数' fact={job.facts.headcount} format={value => `${value} 人`} />
    <FactRow label='人数说明' fact={job.facts.headcount_description} />
    <FactRow label='工作地点' fact={job.facts.work_location} />
    <FactRow label='用工形式' fact={job.facts.employment_type} format={value => employmentLabels[value as keyof typeof employmentLabels] ?? String(value)} />
    <FactRow label='编制身份' fact={job.facts.staffing_status} format={value => staffingLabels[value] ?? String(value)} />
    <FactRow label='报名开始' fact={job.facts.application_start_on} />
    <FactRow label='截止日期' fact={job.facts.deadline_on} />
    <FactRow label='报名规则' fact={job.facts.application_rule} format={value => applicationRuleLabels[value] ?? String(value)} />
  </View>
}
function conditionRow(condition: PositionCondition, key: string) {
  return <View className='job-condition-row' key={key}>
    <Text className='job-fact-label'>{conditionLabels[condition.type]}</Text>
    <View className='job-fact-content'>
      <Text className='job-fact-value' selectable>{conditionDisplayValue(condition)}</Text>
      <View className='job-condition-tags'>
        <Text className={`job-origin-tag job-origin--${condition.tag}`}>{tagLabels[condition.tag]}</Text>
        {condition.scope === 'notice' && <Text className='job-origin-tag'>公告通用</Text>}
      </View>
    </View>
  </View>
}
function conditionGroup(node: ConditionGroupNode, key: string, depth = 0) {
  if (node.kind === 'condition') return conditionRow(node.condition, key)
  return <View className={`job-condition-group${depth === 0 ? ' job-condition-group--root' : ''}${depth > 1 ? ' job-condition-group--compact' : ''}`} key={key}
    role='group' aria-label={node.operator === 'all' ? '组内条件需同时满足' : '组内条件满足任一项'}>
    <View className='job-condition-group-header'>
      <Text className='job-condition-group-badge'>{node.operator === 'all' ? 'AND' : 'OR'}</Text>
    </View>
    {node.items.map((item, index) => conditionGroup(item, `${key}-${index}`, depth + 1))}
  </View>
}
export function JobConditions({ job }: { job: Job }) {
  const tree = conditionGroupTree(job.facts.conditions, job.facts.condition_logic)
  return <View className='job-condition-list'>
    {!job.facts.conditions.length && <Text className='job-condition-empty'>条件尚未识别，不代表没有要求。</Text>}
    {tree ? conditionGroup(tree, 'root') : <>
      {job.facts.conditions.length > 0 && <View className='job-condition-summary'><Text className='job-origin-tag'>条件组合待核验</Text></View>}
      {job.facts.conditions.map(condition => conditionRow(condition, condition.id))}
    </>}
  </View>
}
