import { Button, Input, Text, View } from '@tarojs/components'
import Taro, { usePullDownRefresh, useReachBottom } from '@tarojs/taro'
import { useState } from 'react'
import { ActionButton, Feedback, NavigationArrow } from '../../components/ui'
import { jobDetailRoute, jobTags } from '../../services/jobs'
import type { JobsQuery } from '../../services/jobs'
import { useJobsFeed } from './use-jobs-feed'
import { employmentTraits, listDeadline, listFact } from './list-presentation'
import { hasFactValue } from './position-display'
import { displayTime } from '../../services/navigation'
import { activeJobFilters, facetChoices, initialJobsQuery, resetJobsFilters, resultCountLabel } from './filters'
import { JobFilterSheet } from './filter-sheet'
import type { FacetKey } from './filters'
import './jobs.scss'

export default function Jobs() {
  const [draft, setDraft] = useState('')
  const [filterSection, setFilterSection] = useState<FacetKey>('deadline')
  const [filterOpen, setFilterOpen] = useState(false)
  const [query, setQuery] = useState<JobsQuery>(initialJobsQuery)
  const { result, items, loading, loadingMore, error, moreError, refresh, loadMore } = useJobsFeed(query)
  const [navigationError, setNavigationError] = useState('')
  useReachBottom(() => { if (!filterOpen) void loadMore() })
  usePullDownRefresh(() => { void refresh().finally(() => Taro.stopPullDownRefresh()) })
  function search() { setQuery(value => ({ ...value, page: 1, q: draft.trim() })) }
  function clearSearch() { setDraft(''); setQuery(value => ({ ...value, page: 1, q: '' })) }
  function resetFilters() { setQuery(resetJobsFilters) }
  const activeFilters = activeJobFilters(query)
  const hasFilters = activeFilters.length > 0
  const selectedCount = activeFilters.filter(filter => filter.key !== 'q').length
  function openFilters(section: FacetKey) { setFilterSection(section); setFilterOpen(true) }
  function quickLabel(key: 'deadline' | 'education', fallback: string) {
    return query[key] && query[key] !== 'all' ? facetChoices[key].find(choice => choice.value === query[key])!.label : fallback
  }
  async function openDetail(id: string) {
    setNavigationError('')
    try { await Taro.navigateTo({ url: jobDetailRoute(id, query.includePartial === true) }) }
    catch { setNavigationError('暂时无法打开岗位详情，请重试。') }
  }
  return <View className='screen screen--tab jobs-screen'>
    <View className='jobs-search'>
      <Input className='input-control jobs-search-input' value={draft} maxlength={100} placeholder='搜索岗位、招聘单位或专业' confirmType='search'
        aria-label='搜索岗位、招聘单位或专业' onInput={event => setDraft(event.detail.value)} onConfirm={search} />
      <ActionButton className='button-primary jobs-search-button' disabled={loading} onClick={search}>搜索</ActionButton>
    </View>
    <View className='jobs-filter-bar'>
      <View className='jobs-filter-entries'>
        <Button className={`jobs-filter-entry${query.deadline !== 'all' ? ' is-selected' : ''}`} onClick={() => openFilters('deadline')} aria-label='筛选报名期限'>
          <Text>{quickLabel('deadline', '报名期限')}</Text><NavigationArrow subtle />
        </Button>
        <Button className={`jobs-filter-entry${query.education !== 'all' ? ' is-selected' : ''}`} onClick={() => openFilters('education')} aria-label='筛选学历'>
          <Text>{quickLabel('education', '学历要求')}</Text><NavigationArrow subtle />
        </Button>
        <Button className={`jobs-filter-entry${selectedCount ? ' is-selected' : ''}`} onClick={() => openFilters('deadline')} aria-label='全部筛选'>
          <Text>筛选{selectedCount ? ` · ${selectedCount}` : ''}</Text><NavigationArrow subtle />
        </Button>
      </View>
    </View>
    <View className='jobs-results-heading' role='status' aria-live='polite'>
      <Text className='jobs-results-count'>{resultCountLabel(result?.total ?? null, hasFilters, loading, error)}</Text>
      {selectedCount > 0 ? <ActionButton className='button-text' disabled={loading} onClick={resetFilters}>清空筛选</ActionButton> : query.q ? <ActionButton className='button-text' onClick={clearSearch}>清除搜索</ActionButton> : null}
    </View>
    <Feedback error={error || navigationError} />
    {loading && !result && <View role='status'><Text className='loading-copy'>正在读取岗位…</Text></View>}
    {error && <ActionButton className='button-secondary' onClick={() => { void refresh() }}>重试读取列表</ActionButton>}
    {result && <>
      {items.length === 0 && !loading && !error && <View className='jobs-empty' role='status'>
        <Text className='jobs-empty-title'>{hasFilters ? '暂无符合条件的岗位' : '暂无岗位'}</Text>
      </View>}
      {items.length > 0 && <View className='jobs-list'>
        {items.map(job => {
          const traits = [
            ...(hasFactValue(job.facts.headcount) ? [listFact(job.facts.headcount, '', value => `计划 ${value} 人`)] : []),
            ...employmentTraits(job),
            ...(hasFactValue(job.facts.work_location) ? [listFact(job.facts.work_location, '')] : []),
          ]
          const unit = listFact(job.facts.hiring_unit, '')
          const deadline = listDeadline(job)
          return <Button className='jobs-row' key={job.id} onClick={() => openDetail(job.id)}
          aria-label={`查看岗位详情：${listFact(job.facts.title, '')}${unit ? `，招聘单位：${unit}` : ''}`}>
          <View className='jobs-row-content'>
            <View className='jobs-row-heading'><Text className='jobs-row-title'>{listFact(job.facts.title, '岗位名称待确认')}</Text><NavigationArrow subtle /></View>
            {unit && <Text className='jobs-row-unit'>{unit}</Text>}
            {traits.length > 0 && <View className='jobs-row-traits'>
              {traits.map(trait => <Text className='jobs-trait' key={trait}>{trait}</Text>)}
            </View>}
            {deadline && <View className='jobs-row-timing'><Text className='jobs-row-deadline'>{deadline}</Text></View>}
            <View className='jobs-status-tags'>{jobTags(job).map(tag => <Text className={`jobs-material-tag jobs-tag--${tag.code}`} key={tag.code}>{tag.label}</Text>)}</View>
            <View className='jobs-row-footer'><Text className='jobs-row-source'>{job.source_name}</Text>
              <Text className='jobs-row-check'>{job.last_successful_check ? `检查 ${displayTime(job.last_successful_check)}` : '检查时间未知'}</Text></View>
          </View>
        </Button>})}
      </View>}
      {items.length > 0 && <View className='jobs-feed-footer' role='status'>
        <Text className='jobs-loaded-count'>已展示 {items.length} 个岗位</Text>
        {loadingMore ? <Text>正在加载…</Text> : moreError ? <>
          <Text className='jobs-load-error'>{moreError}</Text>
          <ActionButton className='button-text' onClick={() => { void loadMore() }}>重试加载</ActionButton>
        </> : result.has_next ? <ActionButton className='button-text' disabled={loading} onClick={() => { void loadMore() }}>加载更多</ActionButton>
          : <Text>{result.page === 1000 && result.total > items.length ? '请缩小筛选范围查看更多' : '已展示全部结果'}</Text>}
      </View>}
    </>}
    {filterOpen && <JobFilterSheet query={query} section={filterSection}
      onCancel={() => setFilterOpen(false)} onError={setNavigationError}
      onApply={value => { setQuery(value); setFilterOpen(false) }} />}
  </View>
}
