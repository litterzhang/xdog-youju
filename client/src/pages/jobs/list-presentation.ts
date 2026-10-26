import { employmentLabels, staffingLabels, hasFactValue } from './position-display'
import type { Job, TaggedFact } from '../../services/jobs'

/** Compact list values; full provenance remains in the detail page. */
export function listFact<T extends string | number>(fact: TaggedFact<T>, unknown: string, format: (value: T) => string = String): string {
  if (!hasFactValue(fact)) return unknown
  return `${format(fact.value)}${fact.tag === 'ai_inferred' ? ' · AI 推断' : ''}`
}

export function listDeadline(job: Job): string {
  const validity = job.validity
  if (validity.status === 'expired') return '已过截止日'
  if (validity.status === 'not_expired' && validity.deadline) return `截止 ${validity.deadline}`
  const kind = job.facts.application_rule.value
  if (kind === 'open_ended') return '长期有效 · 招聘待核验'
  if (kind === 'until_filled') return '招满即止 · 招聘待核验'
  if (job.facts.deadline_on.value) return `${listFact(job.facts.deadline_on, '截止待确认')} · 待核验`
  return ''
}

/** Staffing and employment are independent; a known staffing identity must not look empty. */
export function employmentTraits(job: Job): string[] {
  const items: string[] = []
  if (hasFactValue(job.facts.employment_type)) items.push(listFact(job.facts.employment_type, '', value => employmentLabels[value]))
  if (hasFactValue(job.facts.staffing_status)) items.push(listFact(job.facts.staffing_status, '', value => staffingLabels[value]))
  return items
}
