import { Button, ScrollView, Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useEffect, useState } from 'react'
import { ActionIcon } from '../../components/ui'
import type { JobsQuery } from '../../services/jobs'
import { applyJobsFilters, changeJobsFilters, facetChoices, facetLabels, materialChoices, resetJobsFilters } from './filters'
import type { FacetKey } from './filters'
import './filter-sheet.scss'

/** Selections stay local until confirmation; dismissing never changes the feed. */
export function JobFilterSheet({ query, section, onApply, onCancel, onError }: {
  query: JobsQuery; section: FacetKey; onApply: (query: JobsQuery) => void
  onCancel: () => void; onError: (message: string) => void
}) {
  const [pending, setPending] = useState<JobsQuery>(() => ({ ...query }))
  useEffect(() => {
    // Use Taro's platform adapter, never a browser or wx global. Restore even
    // when navigation disposes the sheet before the hide promise completes.
    let disposed = false
    const restore = () => Taro.showTabBar({ animation: false }).catch(() => onError('底部导航暂未恢复，请重新进入岗位页。'))
    void Taro.hideTabBar({ animation: false }).then(() => {
      if (disposed) void restore()
    }).catch(() => { if (!disposed) { onError('暂时无法打开筛选，请重试。'); onCancel() } })
    return () => { disposed = true; void restore() }
  }, [])
  function reset() {
    setPending(resetJobsFilters)
  }
  return <View className='job-filter-overlay' catchMove>
    <View className='job-filter-backdrop' onClick={onCancel} aria-label='取消筛选' />
    <View className='job-filter-sheet' role='dialog' aria-modal aria-label='筛选岗位'>
      <View className='job-filter-sheet-header'>
        <Text className='job-filter-sheet-title'>筛选岗位</Text>
        <Button className='job-filter-sheet-close' aria-label='关闭筛选' onClick={onCancel}><ActionIcon name='close' subtle /></Button>
      </View>
      <ScrollView className='job-filter-sheet-scroll' scrollY enhanced showScrollbar={false} scrollIntoView={`job-filter-${section}`}>
        <View className='job-filter-sheet-content'>
          {(Object.keys(facetChoices) as FacetKey[]).map(key => <View className='job-filter-section' id={`job-filter-${key}`} key={key}>
            <Text className='job-filter-section-label'>{facetLabels[key]}</Text>
            <View className='job-filter-options'>
              {facetChoices[key].map(choice => <Button key={choice.value}
                className={`job-filter-option${(pending[key] ?? 'all') === choice.value ? ' is-selected' : ''}`}
                aria-label={`${facetLabels[key]}：${choice.label}`} aria-pressed={(pending[key] ?? 'all') === choice.value}
                onClick={() => setPending(value => changeJobsFilters(value, { [key]: choice.value }))}>
                {choice.value === 'all' ? '全部' : choice.label}
              </Button>)}
            </View>
          </View>)}
          <View className='job-filter-section' id='job-filter-material'>
            <Text className='job-filter-section-label'>材料范围</Text>
            <View className='job-filter-options job-filter-options--two'>
              {materialChoices.map((label, index) => <Button key={label} className={`job-filter-option${(pending.includePartial !== false) === Boolean(index) ? ' is-selected' : ''}`}
                aria-label={`材料范围：${label}`} aria-pressed={(pending.includePartial !== false) === Boolean(index)}
                onClick={() => setPending(value => changeJobsFilters(value, { includePartial: Boolean(index) }))}>{label}</Button>)}
            </View>
          </View>
        </View>
      </ScrollView>
      <View className='job-filter-sheet-footer'>
        <Button className='job-filter-reset' onClick={reset}>重置</Button>
        <Button className='job-filter-confirm' onClick={() => onApply(applyJobsFilters(query, pending))}>确认筛选</Button>
      </View>
    </View>
  </View>
}
