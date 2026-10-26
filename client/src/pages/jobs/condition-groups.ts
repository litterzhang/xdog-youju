import type { ConditionLogic, PositionCondition } from '../../services/jobs'

export type ConditionGroupNode =
  | { kind: 'condition'; condition: PositionCondition }
  | { kind: 'group'; operator: 'all' | 'any'; items: ConditionGroupNode[] }

/** Presentation of declared logic only: never infer AND from a list of requirements. */
export function conditionGroupTree(conditions: PositionCondition[], logic: ConditionLogic | null): ConditionGroupNode | null {
  if (!logic) return null
  const index = new Map(conditions.map(condition => [condition.id, condition]))
  const used = new Set<string>()
  let count = 0
  if (index.size !== conditions.length) return null
  function build(node: ConditionLogic, depth: number): ConditionGroupNode | null {
    if (++count > 500 || depth > 12) return null
    if ('condition_id' in node) {
      const condition = index.get(node.condition_id)
      if (!condition) return null
      used.add(condition.id)
      return { kind: 'condition', condition }
    }
    const items: ConditionGroupNode[] = []
    for (const child of node.items) {
      const item = build(child, depth + 1)
      if (!item) return null
      // Flatten only associative operations. Mixed AND/OR groups remain intact.
      if (item.kind === 'group' && item.operator === node.operator) items.push(...item.items)
      else items.push(item)
    }
    return items.length ? { kind: 'group', operator: node.operator, items } : null
  }
  const tree = build(logic, 0)
  return used.size === conditions.length ? tree : null
}
