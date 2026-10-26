import { Button, Image, Text, View } from '@tarojs/components'
import type { ButtonProps } from '@tarojs/components'
import Taro from '@tarojs/taro'
import type { PropsWithChildren, ReactNode } from 'react'
import { flowSteps } from '../services/navigation'

export type ActionIconName =
  | 'search' | 'refresh' | 'add' | 'copy' | 'external' | 'feedback'
  | 'send' | 'login' | 'logout' | 'retry' | 'save' | 'check' | 'edit'
  | 'filter' | 'close' | 'chevron-left' | 'chevron-right' | 'play' | 'pause' | 'info'

export function ActionIcon({ name, inverted = false, subtle = false }: { name: ActionIconName; inverted?: boolean; subtle?: boolean }) {
  // These local PNGs are copied by the existing Taro assets configuration on both targets.
  const variant = inverted ? '-inverted' : subtle ? '-subtle' : ''
  return <Image className='action-icon' src={`/assets/icons/action-${name}${variant}.png`} mode='aspectFit' aria-hidden />
}

export type ActionButtonProps = ButtonProps & { icon?: ActionIconName; inverted?: boolean; subtle?: boolean }

export function ActionButton({ icon, inverted, subtle = false, className = '', children, ...props }: ActionButtonProps) {
  const primary = props.type === 'primary' || props.type === 'warn' || className.split(/\s+/).includes('button-primary')
  const white = !props.disabled && (inverted ?? (primary && !props.plain))
  return <Button {...props} className={`action-button ${className}${props.plain ? ' action-button--plain' : ''}`}>
    <View className='action-button__content'>
      {icon && icon !== 'chevron-right' && <ActionIcon name={icon} inverted={white} subtle={subtle || props.disabled} />}
      <Text className='action-button__label'>{children}</Text>
      {icon === 'chevron-right' && <ActionIcon name={icon} inverted={white} subtle={subtle || props.disabled} />}
    </View>
  </Button>
}

export function NavigationArrow({ direction = 'right', subtle = false }: { direction?: 'left' | 'right'; subtle?: boolean }) {
  return <Image className='navigation-arrow' src={`/assets/icons/chevron-${direction}${subtle ? '' : '-selected'}.png`} mode='aspectFit' aria-hidden />
}
export function Card({ children, className = '' }: PropsWithChildren<{ className?: string }>) {
  return <View className={`card ${className}`}>{children}</View>
}
export function SectionLabel({ children }: PropsWithChildren) { return <Text className='section-label'>{children}</Text> }
export function Feedback({ error, notice }: { error?: string; notice?: string }) {
  return <>{error && <View className='feedback feedback--error' role='alert'><Text>{error}</Text></View>}
    {notice && <View className='feedback' role='status'><Text>{notice}</Text></View>}</>
}
export function EmptyState({ icon = 'jobs', label, detail, children }: PropsWithChildren<{ icon?: string; label: string; detail: string }>) {
  return <View className='empty-state'><View className='empty-mark'><Image className='empty-icon' src={`/assets/tabbar/${icon}-selected.png`} /></View>
    <Text className='empty-label'>{label}</Text><Text className='empty-detail'>{detail}</Text>{children}</View>
}
export function MenuRow({ label, detail, badge, onClick, disabled, loading, icon, trailingIcon }: {
  label: string; detail?: string; badge?: string; onClick?: () => void; disabled?: boolean; loading?: boolean
  icon?: ActionIconName; trailingIcon?: ActionIconName | null
}) {
  const trailing = trailingIcon === undefined ? (onClick ? 'chevron-right' : null) : trailingIcon
  const content = <View className='menu-row__content'>
      {icon && <ActionIcon name={icon} subtle />}
      <View className='menu-copy'><Text className='menu-label'>{label}</Text>{detail && <Text className='menu-detail'>{detail}</Text>}</View>
      {badge && <Text className='badge'>{badge}</Text>}{trailing && <ActionIcon name={trailing} subtle />}
    </View>
  return onClick ? <Button className='menu-row shared-action-row' disabled={disabled} loading={loading} onClick={onClick}>{content}</Button> :
    <View className='menu-row shared-action-row menu-row--readonly'>{content}</View>
}
export function Field({ label, help, children }: PropsWithChildren<{ label: string; help?: string }>) {
  return <View className='form-field'><Text className='field-label'>{label}</Text>{children}{help && <Text className='field-help'>{help}</Text>}</View>
}
export function Steps({ current }: { current: number }) {
  return <View className='steps'>{flowSteps.map((step, index) => <View className={`step ${index === current ? 'step--current' : ''}`} key={step}>
    <Text className='step-number'>{String(index + 1).padStart(2, '0')}</Text><Text className='step-label'>{step}</Text>
  </View>)}</View>
}
export function Metric({ value, label }: { value: ReactNode; label: string }) {
  return <View className='metric'><Text className='metric-value'>{value}</Text><Text className='metric-label'>{label}</Text></View>
}
export function AdminGate({ allowed, loading, error, notice, retry, children }: PropsWithChildren<{ allowed: boolean; loading: boolean; error: string; notice?: string; retry: () => unknown }>) {
  return <View className='screen'><Feedback error={error} notice={notice} />
    {loading && <Text className='loading-copy'>正在核验权限与读取状态…</Text>}
    {!allowed && !loading ? <Card><EmptyState icon='me' label='需要管理权限' detail='此入口仅向已授权账号开放，权限由服务端核验。'>
      <ActionButton className='button-primary' onClick={() => Taro.navigateTo({ url: '/pages/login/index' })}>登录已授权账号</ActionButton>
      <ActionButton className='button-text' subtle onClick={retry}>重新核验</ActionButton>
    </EmptyState></Card> : allowed && children}
  </View>
}
