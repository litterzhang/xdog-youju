import { Button, Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useRef, useState } from 'react'
import { ActionButton, AdminGate, EmptyState, NavigationArrow } from '../../components/ui'
import { useAdminData } from '../../hooks/use-admin-data'
import { request } from '../../services/api'
import { RegistryPage, Source } from '../../services/contracts'
import { displayTime, sourceRoute } from '../../services/navigation'
import { sourceHost, sourceListState } from './source-presentation'
import './sources.scss'

export default function SourceManagement() {
  const [page, setPage] = useState(1)
  const pageRef = useRef(1)
  const state = useAdminData(() => request<RegistryPage<Source>>(`/admin/sources?page=${pageRef.current}`))
  async function turn(next: number) { pageRef.current = next; setPage(next) }
  // Changing a page requests only that list page; no pipeline work runs here.
  const navigatePage = async (next: number) => { await turn(next); await state.reload() }
  const sources = state.data?.items || []
  const attention = sources.filter(source => sourceListState(source).attention).length
  return <AdminGate {...state} retry={state.reload}>
    <View className='source-management'>
      <View className='sources-toolbar'><View><Text className='sources-count'>{state.data?.total ?? '—'}</Text><Text className='sources-count-label'> 个已登记来源</Text></View></View>
      <ActionButton className='button-primary sources-add' disabled={state.loading} onClick={() => Taro.navigateTo({ url: '/pages/admin/org/index' })}>接入来源</ActionButton>
      <View className='sources-list-heading'><Text>全部来源</Text><Text>{attention ? `本页 ${attention} 个需处理` : '按来源查看接入与采集状态'}</Text></View>
      <View className='sources-list'>{sources.length ? sources.map(source => {
        const status = sourceListState(source)
        return <Button className='source-list-row' key={source.id} onClick={() => Taro.navigateTo({ url: sourceRoute(source) })}>
          <View className='source-list-content'>
            <Text className='source-list-org'>{source.organization_name || '发布组织待核'}</Text>
            <View className='source-list-heading'><Text className='source-list-name'>{source.name}</Text><NavigationArrow subtle /></View>
            <Text className='source-list-host'>{sourceHost(source.base_url)}</Text>
            <View className='source-list-state'><Text className={`source-state-label ${status.attention ? 'source-state-label--attention' : ''}`}>{status.label}</Text>
              <Text className='source-state-detail'>{status.detail}</Text></View>
            <Text className='source-list-updated'>接入更新 {displayTime(source.onboarding.updated_at)} · 北京时间</Text>
          </View>
        </Button>
      }) : !state.loading && <EmptyState icon='home' label='从一个官方来源开始' detail='选择发布组织，登记网址并核验官方依据。' />}</View>
      {(state.data?.total || 0) > 50 && <View className='pagination'>
        <ActionButton icon='chevron-left' className='button-small' disabled={state.loading || page <= 1} onClick={() => navigatePage(page - 1)}>上一页</ActionButton>
        <Text>{page} / {Math.max(1, Math.ceil((state.data?.total || 0) / 50))}</Text>
        <ActionButton icon='chevron-right' className='button-small' disabled={state.loading || page * 50 >= (state.data?.total || 0)} onClick={() => navigatePage(page + 1)}>下一页</ActionButton>
      </View>}
      <ActionButton className='button-text sources-refresh' disabled={state.loading} onClick={state.reload}>刷新来源状态</ActionButton>
    </View>
  </AdminGate>
}
