import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import ts from 'typescript'
import type { Job, TaggedFact } from '../src/services/jobs'
import * as services from '../src/services/jobs'
import * as display from '../src/pages/jobs/position-display'
import * as groups from '../src/pages/jobs/condition-groups'
import { employmentTraits, listDeadline } from '../src/pages/jobs/list-presentation'

type Node = { type: unknown; props: { children?: unknown } }
const jsx = (type: unknown, props: Record<string, unknown>) => typeof type === 'function'
  ? type(props) : { type, props }
const imports: Record<string, unknown> = {
  'react/jsx-runtime': { jsx, jsxs: jsx, Fragment: 'Fragment' },
  '@tarojs/components': { Text: 'Text', View: 'View' },
  '../../services/jobs': services,
  './position-display': display,
  './condition-groups': groups,
}
const module = { exports: {} as {
  FactRow: (props: { label: string; fact: TaggedFact<string | number>; format?: (v: string | number) => string }) => unknown
  JobBusinessDetails: (props: { job: Job }) => unknown
} }
const source = readFileSync('src/pages/jobs/presentation.tsx', 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
} }).outputText
runInNewContext(compiled, { exports: module.exports, module, require: (name: string) => {
  assert.ok(name in imports, `Unexpected presentation dependency ${name}`)
  return imports[name]
} })
function text(node: unknown): string {
  if (Array.isArray(node)) return node.map(text).join(' ')
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  return node && typeof node === 'object' && 'props' in node ? text((node as Node).props.children) : ''
}
function emptyJob(): Job {
  const names = ['title', 'hiring_unit', 'employer', 'position_code', 'headcount',
    'headcount_description', 'work_location', 'employment_type', 'staffing_status',
    'application_start_on', 'deadline_on', 'application_rule']
  return { facts: Object.fromEntries(names.map(name => [name, { value: null, tag: 'unknown' }])),
    validity: { status: 'unknown', deadline: null } } as unknown as Job
}

test('real fact row omits unknown/blank values while keeping zero and AI provenance', () => {
  let formatted = false
  assert.equal(module.exports.FactRow({ label: '工作地点', fact: { value: null, tag: 'unknown' },
    format: () => { formatted = true; return 'should not format unknown' } }), null)
  assert.equal(formatted, false)
  assert.equal(module.exports.FactRow({ label: '工作地点', fact: { value: ' ', tag: 'source' } }), null)
  const zero = text(module.exports.FactRow({ label: '计划人数', fact: { value: 0, tag: 'source' }, format: v => `${v} 人` }))
  assert.match(zero, /计划人数.*0 人.*原文明确/)
  const inferred = text(module.exports.FactRow({ label: '工作地点', fact: { value: '苏州', tag: 'ai_inferred' } }))
  assert.match(inferred, /工作地点.*苏州.*AI 推断/)
})

test('real business section contains only recognized fields and disappears when empty', () => {
  const job = emptyJob()
  assert.equal(display.hasBusinessFacts(job), false)
  assert.equal(module.exports.JobBusinessDetails({ job }), null)
  job.facts.headcount = { value: 0, tag: 'source' }
  job.facts.work_location = { value: '合肥', tag: 'ai_inferred' }
  const before = JSON.stringify(job)
  assert.equal(display.hasBusinessFacts(job), true)
  const content = text(module.exports.JobBusinessDetails({ job }))
  assert.match(content, /计划人数.*0 人/)
  assert.match(content, /工作地点.*合肥.*AI 推断/)
  assert.doesNotMatch(content, /招聘单位|合同单位|岗位代码|用工形式|编制身份|截止日期|未识别|未知/)
  assert.equal(JSON.stringify(job), before) // No changes to DTOs or stored facts.
  const detail = readFileSync('src/pages/jobs/detail/index.tsx', 'utf8')
  assert.match(detail, /hasBusinessFacts\(job\) && <Card/)
})

test('list omits empty traits/deadlines but keeps explicit rules and independent risks', () => {
  const job = emptyJob()
  assert.deepEqual(employmentTraits(job), [])
  assert.equal(listDeadline(job), '')
  job.facts.application_rule = { value: 'until_filled', tag: 'source' }
  assert.match(listDeadline(job), /招满即止.*待核验/)
  job.validity.status = 'expired'
  assert.equal(listDeadline(job), '已过截止日')
  const list = readFileSync('src/pages/jobs/index.tsx', 'utf8')
  assert.doesNotMatch(list, /招聘单位待确认|人数待确认|地点待确认/)
  assert.match(list, /traits.length > 0/)
  assert.match(list, /jobTags\(job\)/)
  assert.match(source, /条件尚未识别，不代表没有要求/)
  assert.match(source, /conditionGroupTree\(job.facts.conditions, job.facts.condition_logic\)/)
})
