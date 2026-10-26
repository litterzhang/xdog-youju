import assert from 'node:assert/strict'
import test from 'node:test'
import { healthUrl, parseHealthResponse } from '../src/services/health-contract'

test('accepts the explicit uninitialized-data contract', () => {
  const value = { service: 'xdog-youju', status: 'ok', data_status: 'not_initialized' }
  assert.deepEqual(parseHealthResponse(value), value)
})

test('rejects malformed or misleading service responses', () => {
  for (const value of [null, '', {}, { service: 'other', status: 'ok' },
    { service: 'xdog-youju', status: 'ok', data_status: 'verified' }]) {
    assert.throws(() => parseHealthResponse(value))
  }
})

test('H5 uses the same-origin proxy by default', () => {
  assert.equal(healthUrl('', 'h5'), '/api/health')
  assert.equal(healthUrl('http://127.0.0.1:8089/', 'h5'), 'http://127.0.0.1:8089/api/health')
})

test('WeChat requires an explicit HTTPS API base', () => {
  assert.throws(() => healthUrl('', 'weapp'))
  assert.throws(() => healthUrl('http://127.0.0.1:8089', 'weapp'))
  assert.equal(healthUrl('https://example.test/', 'weapp'), 'https://example.test/api/health')
})

test('rejects unsupported schemes and malformed endpoint configuration', () => {
  for (const base of ['file:///tmp/data', 'javascript:alert(1)', 'https://', 'https://a.test?x=1']) {
    assert.throws(() => healthUrl(base, 'h5'))
  }
})
