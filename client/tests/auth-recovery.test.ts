import assert from 'node:assert/strict'
import test from 'node:test'
import { AuthRecovery } from '../src/services/auth-recovery'
import type { SessionSnapshot, VerifiedSession } from '../src/services/auth-recovery'
import { ApiError } from '../src/services/api-error'

const original: SessionSnapshot = { token: 'expired', userId: 'one', generation: 0 }
const renewed: VerifiedSession = { token: 'renewed', userId: 'one' }
function harness(options: { login?: () => Promise<VerifiedSession>; send?: (token: string) => Promise<unknown>; canRecover?: boolean } = {}) {
  let current: SessionSnapshot | null = { ...original }
  let generation = 0; let logins = 0
  const requests: { token: string; path: string; data?: Record<string, unknown> }[] = []
  const recovery = new AuthRecovery({
    current: () => current,
    save: value => (current = { ...value, generation: ++generation }),
    clear: () => { generation++; current = null },
    login: async () => { logins++; return options.login ? options.login() : renewed },
    canRecover: () => options.canRecover ?? true,
    send: async <T>(path: string, data: Record<string, unknown> | undefined, token: string) => {
      requests.push({ token, path, data })
      if (options.send) return await options.send(token) as T
      if (token === 'expired') throw new ApiError(401, 'Expired')
      return { ok: true } as T
    },
  })
  return { recovery, requests, current: () => current, logins: () => logins,
    switch: (value: VerifiedSession | null) => { generation++; current = value ? { ...value, generation } : null } }
}
test('parallel expired requests share one login and replay a rejected write only once', async () => {
  const h = harness(); const payload = { name: 'synthetic source' }
  await Promise.all([h.recovery.request(original, '/admin/sources', payload), h.recovery.request(original, '/auth/me')])
  await h.recovery.request(original, '/auth/me')
  assert.equal(h.logins(), 1)
  assert.equal(h.current()?.userId, 'one')
  assert.deepEqual(h.requests.filter(r => r.path === '/admin/sources').map(r => r.data), [payload, payload])
})
test('network, 503 and 403 failures do not log out or renew without an authentication rejection', async () => {
  for (const failure of [new Error('Offline'), new ApiError(503, 'Unavailable'), new ApiError(403, 'Forbidden')]) {
    const h = harness({ send: async () => { throw failure } })
    await assert.rejects(h.recovery.request(original, '/admin/sources'))
    assert.deepEqual(h.current(), original); assert.equal(h.logins(), 0)
  }
  let attempts = 0
  const temporary = harness({ login: async () => { if (++attempts === 1) throw new ApiError(503, 'Unavailable'); return renewed } })
  await assert.rejects(temporary.recovery.request(original, '/auth/me'), { status: 503 })
  assert.deepEqual(temporary.current(), original)
  await temporary.recovery.request(original, '/auth/me'); assert.equal(temporary.logins(), 2)
})
test('changed identity, logout and logout/login with the same token cannot replay private writes', async () => {
  const mismatch = harness({ login: async () => ({ ...renewed, userId: 'another' }) })
  await assert.rejects(mismatch.recovery.request(original, '/admin/sources', { name: 'one' }), { status: 401 })
  assert.equal(mismatch.current(), null); assert.equal(mismatch.requests.length, 1)
  for (const sameToken of [false, true]) {
    let finish!: (value: VerifiedSession) => void
    const h = harness({ login: () => new Promise(resolve => { finish = resolve }) })
    const pending = h.recovery.request(original, '/admin/sources', { name: 'one' })
    await Promise.resolve(); h.switch(sameToken ? { token: 'expired', userId: 'one' } : null); finish(renewed)
    await assert.rejects(pending, /登录状态已变化/)
    assert.equal(h.requests.length, 1)
    assert.equal(h.current()?.token, sameToken ? 'expired' : undefined)
  }
})
test('H5 and identity-less legacy tokens require explicit login; repeated 401s stop after one replay', async () => {
  const web = harness({ canRecover: false })
  await assert.rejects(web.recovery.request(original, '/auth/me'), { status: 401 })
  assert.equal(web.logins(), 0); assert.equal(web.current(), null)
  const legacy = harness(); legacy.switch({ token: 'expired', userId: '' })
  const legacySnapshot = legacy.current()!
  await assert.rejects(legacy.recovery.request(legacySnapshot, '/auth/me'), { status: 401 })
  assert.equal(legacy.logins(), 0)
  const rejected = harness({ send: async () => { throw new ApiError(401, 'Rejected') } })
  await assert.rejects(rejected.recovery.request(original, '/admin/sources'), { status: 401 })
  assert.equal(rejected.logins(), 1); assert.equal(rejected.requests.length, 2); assert.equal(rejected.current(), null)
})
