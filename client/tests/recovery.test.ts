import assert from 'node:assert/strict'
import test from 'node:test'
import { blockedExplanation, resumeConfirmation, resumeLimits } from '../src/services/onboarding-recovery'
import type { RecoveryOptions } from '../src/services/contracts'

const blocked: RecoveryOptions = {
  limits: { max_depth: 20, max_resources: 4000 }, suggested_limits: { max_depth: 100 },
  blocking_limits: ['depth_limit'], depth_limited_resources: 22,
  can_resume: true, can_repair: false, requires_operator: false,
}
test('depth recovery explains the blocker and confirms saved-resource reuse with bounded limits', () => {
  assert.deepEqual(resumeLimits(blocked), { max_depth: 100 })
  assert.match(blockedExplanation(undefined, blocked), /深度 20.*22 个资源/)
  assert.match(resumeConfirmation(blocked), /深度上限提高到 100.*复用已保存资料.*模型费用/)
  assert.throws(() => resumeLimits({ ...blocked, suggested_limits: { max_depth: 101 } }))
  assert.throws(() => resumeLimits({ ...blocked, suggested_limits: { max_depth: 19 } }))
  assert.throws(() => resumeLimits({ ...blocked, suggested_limits: { max_resources: 10001 } }))
})
test('analysis blockers describe agent repair or explicit review rather than claiming activation', () => {
  assert.match(blockedExplanation('SOURCE_EXECUTION_FAILURE'), /审计反馈修复.*重新审计/)
  assert.match(blockedExplanation('AUDIT_NOT_READY'), /满足审计要求/)
  assert.match(blockedExplanation('unexpected'), /不会跳过来源核验/)
})
