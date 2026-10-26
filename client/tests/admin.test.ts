import assert from 'node:assert/strict'
import test from 'node:test'
import { accountDisplayName, canManageSources, counts, lines, parseIdentity, stageLabel } from '../src/services/contracts'

test('admin entry needs server-issued application permission, not a role or cached boolean', () => {
  assert.equal(canManageSources(null), false)
  assert.equal(canManageSources(parseIdentity({ id: 'reader', permissions: ['source:manage', '*'], roles: ['youju-admin'] })), false)
  assert.equal(canManageSources(parseIdentity({ id: 'reader', permissions: [], is_admin: true })), false)
  assert.equal(canManageSources(parseIdentity({ id: 'operator', permissions: ['youju:source:manage'] })), true)
  assert.throws(() => parseIdentity({ id: 'operator', permissions: 'youju:source:manage' }))
  assert.throws(() => parseIdentity({ id: '', permissions: ['youju:source:manage'] }))
})
test('onboarding presentation preserves partial, unknown and failures', () => {
  assert.equal(stageLabel('inventory'), '采集全站链接与材料')
  assert.match(counts({ partial: 3, failed: 1, unknown: 2 }), /部分材料解析 3.*失败 1.*未知 2/)
  assert.equal(counts(undefined), '暂无统计')
  assert.deepEqual(lines(' https://jobs.example.org/one\r\n\nhttps://jobs.example.org/two '), ['https://jobs.example.org/one', 'https://jobs.example.org/two'])
})

test('authenticated requests reject insecure remote origins including same-origin H5', async () => {
  const { authenticatedUrl } = await import('../src/services/auth-contract')
  assert.equal(authenticatedUrl('', 'h5', '/auth/me', 'http://127.0.0.1:10089'), '/api/auth/me')
  assert.equal(authenticatedUrl('https://api.example.org/', 'weapp', '/admin/sources'), 'https://api.example.org/api/admin/sources')
  assert.throws(() => authenticatedUrl('', 'h5', '/auth/login', 'http://public.example.org'))
  assert.throws(() => authenticatedUrl('http://public.example.org', 'h5', '/auth/login'))
  assert.throws(() => authenticatedUrl('http://127.0.0.1', 'weapp', '/auth/login'))
  assert.throws(() => authenticatedUrl('https://api.example.org', 'h5', 'https://evil.example.org'))
})

test('optional verified profile fields are preserved without granting permissions', () => {
  assert.deepEqual(parseIdentity({ id: 'reader', permissions: [], username: ' Alice ', avatar: 'https://images.example.org/avatar.png' }), {
    id: 'reader', permissions: [], username: 'Alice', avatar: 'https://images.example.org/avatar.png',
  })
  const invalidProfiles = [
    { username: null, avatar: null },
    { username: '', avatar: '' },
    { username: 123, avatar: {} },
    { username: 'x'.repeat(129), avatar: 'https://images.example.org/' + 'x'.repeat(2048) },
    { username: 'name\nadmin', avatar: 'javascript:alert(1)' },
    { username: '\u0000', avatar: 'http://images.example.org/avatar.png' },
    { username: ' ', avatar: 'https://user:password@images.example.org/avatar.png' },
    { avatar: '//images.example.org/avatar.png' },
  ]
  for (const profile of invalidProfiles) {
    assert.deepEqual(parseIdentity({ id: 'reader', permissions: [], ...profile }), { id: 'reader', permissions: [] })
  }
  assert.equal(parseIdentity({ id: 'reader', permissions: [], username: 'Alice' }).username, 'Alice')
  assert.equal(parseIdentity({ id: 'reader', permissions: [], avatar: 'https://images.example.org/avatar.png' }).avatar, 'https://images.example.org/avatar.png')
  assert.equal(canManageSources(parseIdentity({ id: 'reader', permissions: [], username: 'admin', avatar: 'https://images.example.org/avatar.png' })), false)
})


test('account display prefers verified nickname and falls back to username or signed-in state', () => {
  const profile = parseIdentity({ id: 'reader', permissions: [], nickname: ' 张大侠 ', username: 'zhang' })
  assert.equal(profile.nickname, '张大侠')
  assert.equal(accountDisplayName(profile), '张大侠')
  assert.equal(accountDisplayName(parseIdentity({ id: 'reader', permissions: [], username: 'zhang' })), 'zhang')
  assert.equal(accountDisplayName(parseIdentity({ id: 'reader', permissions: [], nickname: '\u0000', username: 'zhang' })), 'zhang')
  assert.equal(accountDisplayName(parseIdentity({ id: 'reader', permissions: [] })), '已登录')
})

test('account number is selectable/copyable inside the existing user card, not a separate number card', async () => {
  const { readFileSync } = await import('node:fs')
  const { default: ts } = await import('typescript')
  const source = readFileSync('src/pages/me/index.tsx', 'utf8')
  const ast = ts.createSourceFile('me.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const cards: import('typescript').JsxElement[] = []
  function walk(node: import('typescript').Node) {
    if (ts.isJsxElement(node) && node.openingElement.tagName.getText(ast) === 'Card') cards.push(node)
    ts.forEachChild(node, walk)
  }
  walk(ast)
  const accountCards = cards.filter(node => node.openingElement.getText(ast).includes('account-card'))
  assert.equal(accountCards.length, 1)
  const card = accountCards[0].getText(ast)
  assert.match(card, /account-name/)
  assert.match(card, /account-id-row/)
  assert.match(card, /<Text className='account-id-value' selectable>\{identity.id\}<\/Text>/)
  assert.match(card, /<ActionButton[^>]*onClick=\{copyAccountId\}/)
  assert.equal(cards.filter(node => node.getText(ast).includes('账号编号')).length, 1)
  assert.match(source, /await Taro.setClipboardData\(\{ data: identity.id \}\)/)
  assert.match(source, /catch \{ setError\('账号编号复制失败/)
  assert.match(source, /!identity && !loading && <ActionButton/)
  assert.match(source, /identity && <ActionButton/)
  assert.doesNotMatch(source, /<Button\b/)
})

test('source list separates admission, maintenance blockers and incremental settings without fake progress', async () => {
  const { sourceListState, sourceHost } = await import('../src/pages/admin/source-presentation')
  const source = { status: 'active', onboarding: { stage: 'completed', state: 'completed' },
    incremental: { enabled: true, state: 'idle' } } as import('../src/services/contracts').Source
  const original = JSON.stringify(source)
  assert.deepEqual(sourceListState(source), { label: '已接入', detail: '增量已开启', attention: false })
  assert.equal(sourceListState({ ...source, onboarding: { ...source.onboarding, maintenance: { stage: 'authoring', state: 'blocked' } } }).label, '维护受阻')
  assert.equal(sourceListState({ ...source, incremental: { enabled: true, state: 'blocked' } }).label, '增量受阻')
  assert.equal(sourceListState({ ...source, onboarding: { ...source.onboarding, stage: 'audit', state: 'blocked' } }).label, '接入受阻')
  assert.equal(sourceListState({ ...source, status: 'candidate' }).label, '待核验')
  assert.equal(sourceListState({ ...source, status: 'paused' }).label, '已暂停')
  assert.equal(sourceHost('https://jobs.example.org/a?b=1'), 'jobs.example.org')
  assert.equal(sourceHost('invalid'), '来源地址待确认')
  assert.equal(JSON.stringify(source), original)
})

test('source detail separates current parsing totals, recipe card and frozen onboarding with a full-width entry', async () => {
  const { readFileSync } = await import('node:fs')
  const status = readFileSync('src/pages/admin/status/index.tsx', 'utf8')
  const css = readFileSync('src/pages/admin/sources.scss', 'utf8')
  assert.match(readFileSync('src/pages/admin/status/index.config.ts', 'utf8'), /navigationBarTitleText: '来源详情'/)
  assert.match(css, /\.sources-add \{[^}]*width: 100%[^}]*min-height: 48px/)
  assert.match(status, /\/parsing-summary/)
  assert.match(status, /summary\.source_id !== source\.id/)
  assert.match(status, /<AdminPanel title='解析情况' className='source-parsing-summary'/)
  assert.match(status, /<AdminPanel title='解析版本' className='source-revisions'/)
  assert.match(status, /value: summary\.notice_count/)
  assert.match(status, /value: summary\.position_count/)
  assert.match(status, /summary\.statuses\.pending/)
  assert.match(status, /summary: null, summaryError/)
  assert.doesNotMatch(status, /summary\.notice_count\s*\|\|\s*0|summary\.audit\.total_announcements/)
  for (const page of ['onboarding-detail', 'incremental', 'feedback']) {
    const source = readFileSync(`src/pages/admin/${page}/index.tsx`, 'utf8')
    assert.match(source, /components\/admin/)
    assert.match(source, /<AdminPanel/)
    assert.match(source, /<AdminGate/)
  }
})
