import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import ts from 'typescript'
import { ApiError } from '../src/services/api-error'
import * as feedback from '../src/services/feedback'

type Props = Record<string, unknown>
type Node = { type: unknown; props: Props }
type Effect = { deps: unknown[]; cleanup?: () => void }
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(yes => { resolve = yes })
  return { promise, resolve }
}

// Execute the real TSX with deterministic hooks/transport doubles, not a second
// implementation of submission or auth logic. No DOM, network or paid calls.
function harness(options: {
  identity?: () => Promise<unknown>
  own?: () => Promise<unknown>
  submit?: (...args: unknown[]) => Promise<unknown>
  getJob?: (...args: unknown[]) => Promise<unknown>
  page?: boolean
} = {}) {
  const slots: unknown[] = []
  const effects = new Map<number, Effect>()
  let cursor = 0
  let dirty = true
  let tree: unknown
  let show = () => {}
  let hide = () => {}
  let pending: (() => void)[] = []
  const calls = { identity: 0, own: 0, submit: [] as unknown[][], navigation: [] as Props[] }
  const hooks = {
    useState(initial: unknown) {
      const slot = cursor++
      if (!(slot in slots)) slots[slot] = typeof initial === 'function' ? initial() : initial
      return [slots[slot], (next: unknown) => {
        slots[slot] = typeof next === 'function' ? next(slots[slot]) : next
        dirty = true
      }]
    },
    useRef(initial: unknown) {
      const slot = cursor++
      if (!(slot in slots)) slots[slot] = { current: initial }
      return slots[slot]
    },
    useEffect(effect: () => (() => void) | void, deps: unknown[]) {
      const slot = cursor++
      const previous = effects.get(slot)
      if (!previous || deps.some((value, index) => !Object.is(value, previous.deps[index]))) {
        pending.push(() => {
          previous?.cleanup?.()
          const cleanup = effect()
          effects.set(slot, { deps, ...(cleanup ? { cleanup } : {}) })
        })
      }
    },
  }
  const jsx = (type: unknown, props: Props): Node => ({ type, props })
  const navigateTo = async (input: Props) => { calls.navigation.push(input) }
  const job = { id: 'position-one', facts: { title: { value: '合成岗位', tag: 'source' }, hiring_unit: { value: null, tag: 'unknown' } }, material_status: 'partial' }
  const imports: Record<string, unknown> = {
    react: hooks,
    'react/jsx-runtime': { jsx, jsxs: jsx, Fragment: 'Fragment' },
    '@tarojs/components': Object.fromEntries(['Button', 'Picker', 'Text', 'Textarea', 'View'].map(name => [name, name])),
    '@tarojs/taro': { __esModule: true, default: { navigateTo }, useDidShow: (fn: () => void) => { show = fn }, useDidHide: (fn: () => void) => { hide = fn }, useRouter: () => ({ params: { id: job.id, include_partial: 'true' } }) },
    '../../../components/ui': Object.fromEntries(['ActionButton', 'ActionIcon', 'Card', 'Feedback', 'Field', 'SectionLabel'].map(name => [name, name])),
    '../../../services/api': { ApiError, request: () => { throw new Error('unexpected transport') } },
    '../../../services/session': { getIdentity: async () => { calls.identity++; return options.identity ? options.identity() : { id: 'person-one' } } },
    '../../../services/navigation': { displayTime: (value: string) => value },
    '../../../services/jobs': { getJob: (...args: unknown[]) => options.getJob ? options.getJob(...args) : Promise.resolve(job) },
    '../../../services/feedback': { ...feedback, createFeedbackClient: () => ({
      own: async () => { calls.own++; return options.own ? options.own() : { items: [], total: 0 } },
      submit: async (...args: unknown[]) => { calls.submit.push(args); return options.submit ? options.submit(...args) : { status: 'reviewed' } },
    }) },
    '../position-display': { factText: (fact: { value: string | null }) => fact.value || '未知' },
    '../jobs.scss': {},
  }
  const module = { exports: {} as { default: (props: Props) => unknown; JobFeedbackForm: (props: Props) => unknown } }
  const source = readFileSync('src/pages/jobs/feedback/index.tsx', 'utf8')
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  runInNewContext(compiled + '\nexports.JobFeedbackForm = JobFeedbackForm;', {
    exports: module.exports, module, require: (id: string) => {
      if (!(id in imports)) throw new Error(`Unexpected import ${id}`)
      return imports[id]
    }, Error,
  })
  const component = options.page ? module.exports.default : module.exports.JobFeedbackForm
  function render() {
    if (!dirty) return
    dirty = false; cursor = 0; pending = []
    tree = component({ job })
    pending.forEach(effect => effect())
  }
  async function settle() {
    for (let i = 0; i < 20; i++) { render(); await Promise.resolve() }
    render()
  }
  function nodes(): Node[] {
    const found: Node[] = []
    const walk = (value: unknown) => {
      if (Array.isArray(value)) { value.forEach(walk); return }
      if (value && typeof value === 'object' && 'props' in value) {
        const node = value as Node
        found.push(node); walk(node.props.children)
      }
    }
    walk(tree)
    return found
  }
  function action(kind: string) {
    const label = kind === 'send' ? /^(提交问题记录|登录后提交问题记录)$/ : /^重试读取岗位$/
    const node = nodes().find(item => item.type === 'ActionButton' && label.test(String(item.props.children)))
    assert.ok(node, `missing ${kind} action`)
    return node.props as Props & { onClick: () => Promise<void>; disabled: boolean; loading: boolean }
  }
  function input(value: string) {
    const node = nodes().find(item => item.type === 'Textarea')
    assert.ok(node)
    ;(node.props.onInput as (e: unknown) => void)({ detail: { value } })
    render()
  }
  render(); show()
  return {
    calls, settle, nodes, action, input,
    hide: () => { hide(); render() }, show: () => { show(); render() },
    unmount: () => { effects.forEach(effect => effect.cleanup?.()) },
  }
}

test('feedback is a native-titled standalone page with unverified evidence warnings and no repair actions', async () => {
  const root = globalThis as unknown as { definePageConfig?: (value: unknown) => unknown }
  const before = root.definePageConfig
  root.definePageConfig = value => value
  try {
    const { default: config } = await import('../src/pages/jobs/feedback/index.config')
    assert.equal(config.navigationStyle, 'default')
    assert.equal(config.navigationBarTitleText, '解析反馈')
  } finally { root.definePageConfig = before }
  const source = readFileSync('src/pages/jobs/feedback/index.tsx', 'utf8')
  for (const copy of ['用户提供的片段不是已核验官方证据', '用户提供的原文片段（待核对）：', 'item.resolution_note', '最近最多 50 条', '不是全部历史记录']) assert.ok(source.includes(copy), copy)
  assert.doesNotMatch(source, /navigateBack|switchTab|signIn\(|dangerouslySetInnerHTML|RichText|\/repair|\/recrawl|\/recipe/)
})

test('page loads associated partial job publicly; its loading/error state is separate from identity', async () => {
  const reads: unknown[][] = []
  const h = harness({ page: true, getJob: async (...args) => { reads.push(args); throw new Error('岗位已不再公开') } })
  await h.settle()
  assert.deepEqual(reads, [['position-one', true]])
  assert.equal(h.calls.identity, 0)
  assert.ok(h.nodes().some(node => node.type === 'Feedback' && node.props.error === '岗位已不再公开'))
  await h.action('retry').onClick(); await h.settle()
  assert.equal(reads.length, 2)
})

test('anonymous feedback never fetches private history or posts without login; login is explicit', async () => {
  const h = harness({ identity: async () => null })
  await h.settle()
  assert.equal(h.calls.own, 0)
  assert.equal(h.action('send').disabled, true)
  h.input('原文条件疑点')
  await h.action('send').onClick(); await h.settle()
  assert.equal(h.calls.identity, 2)
  assert.equal(h.calls.submit.length, 0)
  assert.equal(h.calls.navigation.length, 1)
  assert.equal(h.calls.navigation[0].url, '/pages/login/index')
})

test('submit rechecks identity and locks duplicate clicks; reviewed response stays reviewed', async () => {
  const post = deferred<unknown>()
  const h = harness({ submit: () => post.promise })
  await h.settle(); h.input('合成原文疑点')
  const first = h.action('send').onClick()
  const duplicate = h.action('send').onClick()
  await h.settle()
  assert.equal(h.calls.identity, 2)
  assert.equal(h.calls.submit.length, 1)
  assert.equal(h.action('send').loading, true)
  assert.equal(h.calls.submit[0][0], 'position-one')
  assert.equal(JSON.stringify(h.calls.submit[0][1]), JSON.stringify({ field: 'conditions', message: '合成原文疑点' }))
  post.resolve({ status: 'reviewed' }); await Promise.all([first, duplicate]); await h.settle()
  assert.equal(h.action('send').loading, false)
  assert.ok(h.nodes().some(node => node.type === 'Feedback' && String(node.props.notice).includes(feedback.feedbackStatusLabels.reviewed)))
  assert.ok(h.nodes().some(node => node.type === 'Textarea' && node.props.value === ''))
  assert.equal(h.calls.own, 2)
})

test('page hide cancels stale identity before POST, clears private input, and re-show rechecks identity', async () => {
  const identity = deferred<unknown>()
  let check = 0
  const h = harness({ identity: async () => ++check === 1 ? { id: 'one' } : identity.promise })
  await h.settle(); h.input('不要跨身份保留的问题')
  const submit = h.action('send').onClick()
  h.hide(); identity.resolve({ id: 'one' })
  await submit; await h.settle()
  assert.equal(h.calls.submit.length, 0)
  assert.equal(h.action('send').loading, false)
  assert.ok(h.nodes().some(node => node.type === 'Textarea' && node.props.value === ''))
  h.show(); await h.settle()
  assert.equal(h.calls.identity, 3)
  assert.equal(h.calls.own, 2)
})

test('late own-history result is ignored after hide and anonymous resume', async () => {
  const own = deferred<unknown>()
  let check = 0
  const h = harness({ identity: async () => ++check === 1 ? { id: 'one' } : null, own: () => own.promise })
  await h.settle(); h.hide()
  own.resolve({ total: 1, items: [{ id: 'private-one', field: 'other', status: 'open', message: '旧用户私有内容' }] })
  await h.settle(); h.show(); await h.settle()
  assert.equal(h.calls.own, 1)
  assert.ok(!h.nodes().some(node => node.props.children === '旧用户私有内容'))
  assert.ok(h.nodes().some(node => node.type === 'ActionButton' && node.props.children === '先登录，再填写或查看我的反馈'))
})

test('401 during POST hides private history and offers login without public navigation or repair', async () => {
  const h = harness({ own: async () => ({ total: 1, items: [{ id: 'private-one', field: 'other', status: 'open', message: '私有记录' }] }), submit: async () => { throw new ApiError(401, '登录已失效') } })
  await h.settle(); h.input('待核对')
  await h.action('send').onClick(); await h.settle()
  assert.equal(h.action('send').loading, false)
  assert.ok(!h.nodes().some(node => node.props.children === '私有记录'))
  assert.ok(h.nodes().some(node => node.type === 'Feedback' && node.props.error === '登录已失效'))
  assert.equal(h.calls.navigation.length, 0)
})

test('late submission cannot restore success or own records after hide/unmount', async () => {
  const post = deferred<unknown>()
  const h = harness({ submit: () => post.promise })
  await h.settle(); h.input('合成疑点')
  const submit = h.action('send').onClick(); await h.settle()
  h.hide(); h.unmount(); post.resolve({ status: 'reviewed' }); await submit; await h.settle()
  assert.equal(h.calls.own, 1)
  assert.ok(!h.nodes().some(node => node.type === 'Feedback' && node.props.notice))
})


test('hide during POST keeps duplicate-submit lock until settlement, then releases busy without stale notice', async () => {
  const post = deferred<unknown>()
  const h = harness({ submit: () => post.promise })
  await h.settle(); h.input('原文疑点')
  const submit = h.action('send').onClick(); await h.settle()
  h.hide(); h.show(); await h.settle()
  assert.equal(h.action('send').loading, true)
  await h.action('send').onClick()
  assert.equal(h.calls.submit.length, 1)
  post.resolve({ status: 'reviewed' }); await submit; await h.settle()
  assert.equal(h.action('send').loading, false)
  assert.ok(!h.nodes().some(node => node.type === 'Feedback' && node.props.notice))
})
