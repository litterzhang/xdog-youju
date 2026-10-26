import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { inflateSync } from 'node:zlib'
import ts from 'typescript'
import type { ReactElement, ReactNode } from 'react'
import type * as SharedUI from '../src/components/ui'

const names = ['search', 'refresh', 'add', 'copy', 'external', 'feedback', 'send', 'login', 'logout', 'retry', 'save', 'check', 'edit', 'filter', 'close', 'chevron-left', 'chevron-right', 'play', 'pause', 'info'] as const
const require = createRequire(`${process.cwd()}/package.json`)
// Exercise real component functions with inert Taro host elements; no browser/platform/network.
const module = { exports: {} as typeof SharedUI }
const code = ts.transpileModule(readFileSync('src/components/ui.tsx', 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
}).outputText
new Function('require', 'module', 'exports', code)((id: string) => {
  if (id === '@tarojs/components') return { Button: 'Button', Image: 'Image', Text: 'Text', View: 'View' }
  if (id === '@tarojs/taro') return { navigateTo: () => undefined }
  if (id === '../services/navigation') return { flowSteps: [] }
  if (id === 'react/jsx-runtime') return require(id)
  throw new Error(`Unexpected import: ${id}`)
}, module, module.exports)
const { ActionIcon, ActionButton, MenuRow, AdminGate } = module.exports

// Decode all PNG filters without adding a runtime/testing dependency.
function pixels(path: string) {
  const png = readFileSync(path)
  assert.deepEqual(png.subarray(0, 8), Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))
  assert.equal(png.readUInt32BE(16), 64)
  assert.equal(png.readUInt32BE(20), 64)
  assert.equal(png[24], 8, '8-bit channels')
  assert.equal(png[25], 6, 'RGBA, not a flattened opaque icon')
  assert.equal(png[28], 0, 'non-interlaced')
  const parts: Buffer[] = []
  for (let at = 8; at < png.length;) {
    const length = png.readUInt32BE(at)
    if (png.toString('ascii', at + 4, at + 8) === 'IDAT') parts.push(png.subarray(at + 8, at + 8 + length))
    at += 12 + length
  }
  const data = inflateSync(Buffer.concat(parts))
  const stride = 64 * 4
  assert.equal(data.length, 64 * (stride + 1))
  const out = Buffer.alloc(stride * 64)
  for (let y = 0; y < 64; y++) {
    const filter = data[y * (stride + 1)]
    assert.ok(filter <= 4)
    for (let x = 0; x < stride; x++) {
      const i = y * stride + x
      const a = x >= 4 ? out[i - 4] : 0
      const b = y ? out[i - stride] : 0
      const c = y && x >= 4 ? out[i - stride - 4] : 0
      const p = a + b - c
      const paeth = Math.abs(p - a) <= Math.abs(p - b) && Math.abs(p - a) <= Math.abs(p - c) ? a : Math.abs(p - b) <= Math.abs(p - c) ? b : c
      const predictor = [0, a, b, Math.floor((a + b) / 2), paeth][filter]
      out[i] = (data[y * (stride + 1) + x + 1] + predictor) & 255
    }
  }
  return out
}

for (const name of names) test(`${name}: bundled line PNG has matching dark, gray and white alpha variants`, () => {
  let reference: number[] | undefined
  for (const [suffix, tone] of [['', 23], ['-subtle', 115], ['-inverted', 255]] as const) {
    const rgba = pixels(`src/assets/icons/action-${name}${suffix}.png`)
    const alpha: number[] = []
    let opaque = 0, translucent = 0
    for (let i = 0; i < rgba.length; i += 4) {
      const a = rgba[i + 3]
      alpha.push(a)
      if (a > 0) {
        assert.equal(rgba[i], tone)
        assert.equal(rgba[i + 1], tone)
        assert.equal(rgba[i + 2], tone)
      }
      if (a === 255) opaque++
      if (a > 0 && a < 255) translucent++
      const x = (i / 4) % 64, y = Math.floor(i / 4 / 64)
      if (x === 0 || x === 63 || y === 0 || y === 63) assert.equal(a, 0, 'transparent unclipped border')
    }
    assert.ok(opaque > 80 && opaque < 1800, 'visible strokes, not blank or solid tiles')
    assert.ok(translucent > 40, 'antialiased edges')
    if (reference) assert.deepEqual(alpha, reference)
    reference = alpha
  }
})

test('ActionIcon exposes exactly the requested names and chooses proper packaged variants', () => {
  const ast = ts.createSourceFile('ui.tsx', readFileSync('src/components/ui.tsx', 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const type = ast.statements.find(node => ts.isTypeAliasDeclaration(node) && node.name.text === 'ActionIconName') as ts.TypeAliasDeclaration
  assert.ok(ts.isUnionTypeNode(type.type))
  assert.deepEqual(type.type.types.map(node => (node as ts.LiteralTypeNode).literal.getText(ast).slice(1, -1)), [...names])
  for (const name of names) {
    const normal = ActionIcon({ name })
    assert.equal(normal.type, 'Image')
    assert.equal(normal.props.src, `/assets/icons/action-${name}.png`)
    assert.equal(normal.props.mode, 'aspectFit')
    assert.equal(normal.props['aria-hidden'], true)
    assert.equal(ActionIcon({ name, subtle: true }).props.src, `/assets/icons/action-${name}-subtle.png`)
    assert.equal(ActionIcon({ name, subtle: true, inverted: true }).props.src, `/assets/icons/action-${name}-inverted.png`)
  }
})

test('ActionButton forwards native events, disabled/loading, form/open and touch props through one flex child', () => {
  const event = () => undefined
  const props = { icon: 'send' as const, children: '提交', className: 'button-primary', disabled: true, loading: true,
    formType: 'submit' as const, openType: 'share' as const, onClick: event, onTouchStart: event, onTouchEnd: event,
    hoverClass: 'pressed', hoverStartTime: 30, hoverStayTime: 80, id: 'submit', 'aria-label': '提交表单' }
  const result = ActionButton(props)
  assert.equal(result.type, 'Button')
  for (const [key, value] of Object.entries(props)) {
    if (!['icon', 'children', 'className'].includes(key)) assert.equal(result.props[key], value, key)
  }
  assert.equal(result.props.icon, undefined)
  assert.equal(result.props.inverted, undefined)
  assert.equal(result.props.className, 'action-button button-primary')
  assert.equal(result.props.children.type, 'View')
  assert.equal(result.props.children.props.className, 'action-button__content')
  const [icon, label] = result.props.children.props.children
  assert.equal(icon.type, ActionIcon)
  assert.equal(icon.props.inverted, false, 'disabled primary uses gray, not white on light gray')
  assert.equal(icon.props.subtle, true)
  assert.equal(label.props.children, '提交')
  assert.equal(ActionButton({ icon: 'save', children: '保存', className: 'button-primary' }).props.children.props.children[0].props.inverted, true)
  assert.equal(ActionButton({ icon: 'save', type: 'primary' }).props.children.props.children[0].props.inverted, true)
  assert.equal(ActionButton({ icon: 'save', type: 'primary', plain: true }).props.children.props.children[0].props.inverted, false)
  assert.equal(ActionButton({ icon: 'save', className: 'button-primary', inverted: false }).props.children.props.children[0].props.inverted, false)
  assert.equal(elements(ActionButton({ children: '保存' })).filter(item => item.type === ActionIcon).length, 0)
  const next = elements(ActionButton({ icon: 'chevron-right', children: '下一页' }))
  assert.ok(next.findIndex(item => item.type === 'Text') < next.findIndex(item => item.type === ActionIcon), 'right arrow follows label')
})

function elements(node: ReactNode): ReactElement<any>[] {
  if (Array.isArray(node)) return node.flatMap(elements)
  if (!node || typeof node !== 'object' || !('props' in node)) return []
  const element = node as ReactElement<any>
  return [element, ...elements(element.props.children)]
}

test('MenuRow retains legacy navigation defaults and supports explicit copy/leading icons without guessing labels', () => {
  const click = () => undefined
  const row = MenuRow({ label: '任意文案', onClick: click })
  assert.equal(row.props.onClick, click)
  assert.equal(elements(row).filter(item => item.type === ActionIcon).length, 1)
  assert.equal(elements(row).find(item => item.type === ActionIcon)?.props.name, 'chevron-right')
  const readonly = MenuRow({ label: '复制' })
  assert.equal(readonly.type, 'View', 'non-interactive metadata is not a button')
  assert.equal(readonly.props.onClick, undefined)
  assert.equal(elements(readonly).filter(item => item.type === ActionIcon).length, 0)
  assert.deepEqual(elements(MenuRow({ label: '编号', icon: 'info', trailingIcon: 'copy', onClick: click })).filter(item => item.type === ActionIcon).map(item => item.props.name), ['info', 'copy'])
  const loading = MenuRow({ label: '检查', icon: 'refresh', trailingIcon: null, disabled: true, loading: true, onClick: click })
  assert.equal(loading.props.disabled, true)
  assert.equal(loading.props.loading, true)
  assert.equal(elements(loading).filter(item => item.type === ActionIcon).length, 1)
})

test('AdminGate uses explicit login/retry actions, retaining loading and permission gates', () => {
  const retry = () => undefined
  const props = { allowed: false, loading: false, error: '', retry }
  const buttons = elements(AdminGate(props)).filter(item => item.type === ActionButton)
  assert.deepEqual(buttons.map(item => item.props.icon), [undefined, undefined])
  assert.deepEqual(buttons.map(item => item.props.children), ['登录已授权账号', '重新核验'])
  assert.equal(buttons[1].props.onClick, retry)
  assert.equal(elements(AdminGate({ ...props, loading: true })).filter(item => item.type === ActionButton).length, 0)
  assert.equal(elements(AdminGate({ ...props, allowed: true, children: '管理内容' })).filter(item => item.type === ActionButton).length, 0)
})

test('scoped action/account CSS retains focus, touch size, disabled=false handling and single-child flex layout', () => {
  const css = readFileSync('src/app.scss', 'utf8')
  assert.match(css, /\.action-button__content\s*\{[^}]*display:\s*(?:inline-)?flex/)
  assert.match(css, /\.action-button\s*\{[^}]*min-height:\s*44px/)
  assert.match(css, /\.action-button\s*\{[^}]*width:\s*auto/ )
  assert.match(css, /\.menu-row--readonly\s*\{[^}]*box-sizing:\s*border-box/ )
  assert.match(css, /taro-image-core\.action-icon \.taro-img__mode-aspectfit/ )
  assert.match(css, /taro-image-core\.navigation-arrow \.taro-img__mode-aspectfit\s*\{[^}]*left:\s*0/ )
  assert.match(css, /\.action-button:focus-visible/)
  assert.match(css, /action-button\[disabled\]:not\(\[disabled='false'\]\)/)
  assert.match(css, /\.account-card--with-id \.account-id-action\s*\{[^}]*min-height:\s*44px/)
})
