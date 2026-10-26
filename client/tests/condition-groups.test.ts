import { readFileSync } from 'node:fs'
import assert from 'node:assert/strict'
import test from 'node:test'
import { conditionGroupTree } from '../src/pages/jobs/condition-groups'
import type { ConditionGroupNode } from '../src/pages/jobs/condition-groups'
import type { PositionCondition } from '../src/services/jobs'

const conditions: PositionCondition[] = [
  {id:'education',type:'education',operator:'at_least',scope:'notice',value:{level:'bachelor'},tag:'source'},
  {id:'degree',type:'degree',operator:'equals',scope:'position',value:{level:'master'},tag:'source'},
  {id:'major',type:'major',operator:'in',scope:'position',value:{names:['计算机'],unrestricted:false},tag:'source'},
]
function ids(node: ConditionGroupNode): string[] {
  return node.kind === 'condition' ? [node.condition.id] : node.items.flatMap(ids)
}
test('mixed AND/OR preserves nested groups and named requirements without eligibility inference', () => {
  const tree = conditionGroupTree(conditions, {operator:'all',items:[{condition_id:'education'}, {operator:'any',items:[{condition_id:'degree'},{condition_id:'major'}]}]})!
  assert.equal(tree.kind,'group')
  if (tree.kind !== 'group') return
  assert.equal(tree.operator,'all'); assert.equal(tree.items[1].kind,'group')
  if (tree.items[1].kind === 'group') assert.equal(tree.items[1].operator,'any')
  assert.deepEqual(ids(tree),['education','degree','major'])
  assert.equal(tree.items[0].kind === 'condition' && tree.items[0].condition.type,'education')
})
test('only same-operator groups flatten; unknown or incomplete relations never become AND', () => {
  const tree = conditionGroupTree(conditions, {operator:'all',items:[{condition_id:'education'},{operator:'all',items:[{condition_id:'degree'},{condition_id:'major'}]}]})!
  assert.equal(tree.kind === 'group' && tree.items.length,3)
  assert.equal(conditionGroupTree(conditions,null),null)
  assert.equal(conditionGroupTree(conditions,{condition_id:'missing'}),null)
  assert.equal(conditionGroupTree(conditions,{condition_id:'education'}),null)
})


test('detail places business information before named conditions and renders operator badges only', () => {
  const page = readFileSync('src/pages/jobs/detail/index.tsx','utf8')
  const component = readFileSync('src/pages/jobs/presentation.tsx','utf8')
  assert.ok(page.indexOf('<JobBusinessDetails') < page.indexOf('<JobConditions'))
  assert.match(component, /job-condition-group-badge/)
  assert.match(component, /depth === 0 \? ' job-condition-group--root'/)
  const style = readFileSync('src/pages/jobs/jobs.scss','utf8')
  assert.match(style, /\.job-condition-group--root \{[^}]*border: 0/)
  assert.match(style, /\.job-condition-group-header \{[^}]*position: absolute/)
  assert.match(style, /\.job-condition-group-badge \{[^}]*border-radius: 10px/)
  assert.match(component, /'AND' : 'OR'/)
  assert.doesNotMatch(component, /conditionLogicText|组合方式|条件 \{index/)
})
