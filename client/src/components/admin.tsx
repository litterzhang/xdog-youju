import { Text, View } from '@tarojs/components'
import type { PropsWithChildren } from 'react'
import './admin.scss'

export function AdminPanel({ title, className = '', children }: PropsWithChildren<{ title?: string; className?: string }>) {
  return <View className={`admin-panel ${className}`}>{title && <Text className='admin-panel-title'>{title}</Text>}{children}</View>
}
export function AdminStats({ items }: { items: { label: string; value: number | string }[] }) {
  return <View className='admin-stats'>{items.map(item => <View className='admin-stat' key={item.label}>
    <Text className='admin-stat-value'>{item.value}</Text><Text className='admin-stat-label'>{item.label}</Text>
  </View>)}</View>
}
