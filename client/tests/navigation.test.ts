import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { displayTime, flowSteps, sourceRoute, tabs } from '../src/services/navigation'

test('three native tabs and native titles, with admin routes outside tab navigation', async () => {
  const root = globalThis as unknown as { defineAppConfig?: (value: unknown) => unknown }
  const previous = root.defineAppConfig
  root.defineAppConfig = value => value
  try {
    const { default: config } = await import('../src/app.config')
    assert.deepEqual(config.tabBar?.list.map(item => item.text), ['首页', '岗位', '我的'])
    assert.equal(config.window?.navigationStyle, 'default')
    assert.notEqual(config.tabBar?.custom, true)
    assert.equal(tabs.length, 3)
    assert.ok(config.pages?.includes('pages/admin/status/index'))
    for (const tab of config.tabBar?.list || []) {
      assert.ok(!tab.pagePath.startsWith('pages/admin'))
      const icon = readFileSync(`src/${tab.iconPath}`)
      assert.equal(icon.subarray(1, 4).toString(), 'PNG')
      assert.equal(icon.readUInt32BE(16), 64)
    }
  } finally { root.defineAppConfig = previous }
})
test('onboarding routes preserve source identity and review before starting', () => {
  assert.deepEqual([...flowSteps], ['组织', '来源', '核验', '进度'])
  assert.equal(sourceRoute({ id: 'x/y', status: 'candidate', onboarding: { stage: 'not_started' } }), '/pages/admin/review/index?id=x%2Fy')
  assert.match(sourceRoute({ id: 'ready', status: 'active', onboarding: { stage: 'not_started' } }), /review/)
  assert.match(sourceRoute({ id: 'running', status: 'active', onboarding: { stage: 'audit' } }), /status/)
  assert.equal(displayTime(null), '暂无记录')
  assert.equal(displayTime('invalid'), '暂无记录')
  assert.equal(displayTime('2026-10-14T01:02:00+00:00'), '2026/10/14 09:02')
})

test('left and right navigation use packaged monochrome PNG icons, not platform fonts', () => {
  for (const direction of ['left', 'right']) {
    for (const suffix of ['', '-selected']) {
      const icon = readFileSync(`src/assets/icons/chevron-${direction}${suffix}.png`)
      assert.equal(icon.subarray(1, 4).toString(), 'PNG')
      assert.equal(icon.readUInt32BE(16), 64)
    }
  }
})
