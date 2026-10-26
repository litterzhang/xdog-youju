import { incrementalNoticeParseLabel } from '../../../services/incremental'
import { Picker, Text, View } from '@tarojs/components'
import Taro, { useDidHide, useDidShow, useRouter } from '@tarojs/taro'
import { useEffect, useState } from 'react'
import { ActionButton, AdminGate, Feedback, MenuRow, SectionLabel } from '../../../components/ui'
import { AdminPanel, AdminStats } from '../../../components/admin'
import { useAdminData } from '../../../hooks/use-admin-data'
import { request } from '../../../services/api'
import { routingCategoryLabel, type IncrementalNotice, type IncrementalRun, type Source } from '../../../services/contracts'
import { createIncrementalClient, incrementalConfirmation, incrementalControlLabel, incrementalDeltaText, incrementalRunLabel, onboardingCompleted } from '../../../services/incremental'
import { blockedExplanation } from '../../../services/onboarding-recovery'
import { displayTime } from '../../../services/navigation'
import { hasFactValue } from '../../jobs/position-display'
import { listFact } from '../../jobs/list-presentation'
import { employmentLabels } from '../../jobs/position-display'
import './incremental.scss'

const client = createIncrementalClient(request)
function NoticeRow({ notice }: { notice: IncrementalNotice }) {
  const [open, setOpen] = useState(false)
  const [error, setError] = useState('')
  async function copy() {
    setError('')
    try { await Taro.setClipboardData({ data: notice.url }) }
    catch { setError('复制公告地址失败，请长按选择原文链接。') }
  }
  return <View className='incremental-notice'>
    <View className='incremental-notice-heading'><Text className='incremental-notice-title'>{notice.title}</Text>
      <Text className='badge'>{notice.change === 'new' ? '新增公告' : '公告材料变化'}</Text></View>
    <Text className='caption'>{incrementalNoticeParseLabel(notice)} · {notice.position_count} 条关联岗位记录</Text>
    <Text className='caption'>{`${routingCategoryLabel(notice.routing_category)} · 公告期限不由路由确认${notice.review_required ? ' · 待复核' : ''}`} · {notice.material_status === 'complete' ? '材料完整' : '材料不完整'}</Text>
    <Text className='caption' selectable>{notice.url}</Text>
    {!!(notice.error_codes.length || notice.field_issue_codes.length || notice.coverage_issue_codes.length) && <Text className='caption'>解析提示：{[...notice.error_codes, ...notice.field_issue_codes, ...notice.coverage_issue_codes].join(' · ')}</Text>}
    <View className='incremental-notice-actions'><ActionButton className='button-text' onClick={copy}>复制公告原文</ActionButton>
      <ActionButton className='button-text' onClick={() => setOpen(value => !value)}>{open ? '收起岗位' : '查看关联岗位'}</ActionButton></View>
    <Feedback error={error} />
    {open && <View className='incremental-positions'>
      {!notice.positions.length && <Text className='muted'>{notice.positions_unavailable ? '岗位快照无法核验，请核对官方公告。' : notice.extraction_path === 'quick' && !notice.position_count && ['success', 'partial'].includes(notice.status) ? '低成本归档暂无岗位事实，不代表已确认过期；请核对公告原文。' : '本公告暂无可展示的解析岗位，不代表没有招聘岗位。'}</Text>}
      {notice.positions.map(position => <View className='incremental-position' key={position.id}>
        <Text className='incremental-position-title'>{listFact(position.facts.title, '岗位名称待确认')}</Text>
        {hasFactValue(position.facts.hiring_unit) && <Text className='caption'>招聘单位：{listFact(position.facts.hiring_unit, '')}</Text>}
        {hasFactValue(position.facts.headcount) && <Text className='caption'>计划人数（非当前空缺）：{listFact(position.facts.headcount, '')}</Text>}
        {hasFactValue(position.facts.deadline_on) && <Text className='caption'>截止日期：{listFact(position.facts.deadline_on, '')}</Text>}
        {hasFactValue(position.facts.employment_type) && <Text className='caption'>用工形式：{listFact(position.facts.employment_type, '', value => employmentLabels[value])}</Text>}
      </View>)}
      {notice.positions_truncated && <Text className='caption'>每份公告最多展示前 10 条岗位快照，不是完整岗位清单；请核对原文。</Text>}
      {notice.positions_unavailable && <Text className='caption'>部分岗位快照无法核验，未展示其内容。</Text>}
    </View>}
  </View>
}
function RunHeading({ run, enabled }: { run: IncrementalRun; enabled: boolean }) {
  const delta = run.summary.delta
  return <AdminPanel title='本批次结果' className='incremental-run-heading'>
    <Text className='badge'>{incrementalRunLabel(run.status, enabled)}</Text>
    <Text className='caption'>开始 {displayTime(run.started_at)} · 结束 {displayTime(run.finished_at)} · 北京时间</Text>
    <AdminStats items={[{ label: '新增公告', value: delta.new_notices ?? '—' }, { label: '材料变化', value: delta.changed_notices ?? '—' }, { label: '关联岗位', value: delta.position_count ?? '—' }]} />
    {delta.state !== 'ready' && <Text className='caption'>{incrementalDeltaText(delta)}</Text>}
    {run.summary.error && <View className='feedback feedback--error'>批次错误：{run.summary.error.code}</View>}
  </AdminPanel>
}
export default function IncrementalPage() {
  const id = useRouter().params.id || ''
  const [selectedRun, setSelectedRun] = useState('')
  const [page, setPage] = useState(1)
  const [visible, setVisible] = useState(false)
  const state = useAdminData(async () => {
    const [source, incremental] = await Promise.all([
      request<Source>(`/admin/sources/${encodeURIComponent(id)}`), client.read(id),
    ])
    const runId = selectedRun || incremental.latest_run?.id
    const detail = runId ? await client.run(id, runId, page) : null
    return { source, incremental, detail, selectedRun, page }
  })
  useDidShow(() => setVisible(true)); useDidHide(() => setVisible(false))
  useEffect(() => { void state.reload() }, [id, selectedRun, page, state.reload])
  useEffect(() => {
    if (!visible || !state.allowed || state.loading || state.busy) return
    const timer = setInterval(() => { void state.reload() }, 15000)
    return () => clearInterval(timer)
  }, [visible, state.allowed, state.loading, state.busy, state.reload])
  const source = state.data?.source
  const incremental = state.data?.incremental
  const detail = state.data?.detail
  const current = state.data?.selectedRun === selectedRun && state.data?.page === page
  const runs = incremental?.runs || []
  const runId = selectedRun || incremental?.latest_run?.id
  const runIndex = Math.max(0, runs.findIndex(run => run.id === runId))
  const completed = !!source && onboardingCompleted(source.onboarding)
  return <AdminGate {...state} retry={state.reload}>
    {source && incremental && <View className='incremental-page'>
      <AdminPanel className='incremental-overview'><Text className='context-name'>{source.name}</Text>
        <Text className='caption'>{incrementalControlLabel(incremental)}</Text>
        {!completed && <Text className='caption'>接入尚未完成，暂不能开启增量抓取。</Text>}
        {!incremental.enabled && incremental.latest_run?.status === 'running' && <Text className='caption'>已暂停，重新开启后续跑；当前已开始的小批次会安全结束。同一运行周期内的后续有界轮次也已暂停，历史 running 记录保留，不代表取消。</Text>}
      <ActionButton className='button-primary' disabled={state.loading || state.busy || (!incremental.enabled && (!completed || incremental.error?.code === 'RECIPE_UPGRADE_REQUIRED'))} loading={state.busy} onClick={() => state.mutate(async () => {
        const enabled = !incremental.enabled
        const answer = await Taro.showModal(incrementalConfirmation(enabled))
        if (!answer.confirm) return false
        await client.setEnabled(source.id, enabled, source.onboarding)
      })}>{incremental.enabled ? '关闭增量抓取' : '开启增量抓取'}</ActionButton>
      </AdminPanel>
      {incremental.error && <View className='feedback feedback--error'>增量控制错误（不影响接入完成结果）：{blockedExplanation(incremental.error.code)}（{incremental.error.code}）</View>}
      {incremental.state === 'blocked' && <Text className='caption'>请先核查错误。重新开启会清除阻塞标记，不代表错误已解决。</Text>}
      <SectionLabel>增量公告与岗位</SectionLabel>
      {!!runs.length && <Picker mode='selector' range={runs.map(run => `${displayTime(run.started_at)} · ${incrementalRunLabel(run.status, incremental.enabled)}`)} value={runIndex} disabled={state.loading || state.busy} onChange={event => {
        const run = runs[Number(event.detail.value)]
        if (run) { setSelectedRun(run.id); setPage(1) }
      }}><ActionButton className='button-secondary' disabled={state.loading || state.busy}>批次 · {displayTime(runs[runIndex]?.started_at)}</ActionButton></Picker>}
      {detail && current ? <>
        <RunHeading run={detail} enabled={incremental.enabled} />
        <View className='incremental-notice-list'>{detail.notices.items.map(notice => <NoticeRow key={`${detail.id}-${notice.bundle_id}`} notice={notice} />)}</View>
        {!detail.notices.items.length && <Text className='muted'>{detail.summary.delta.state === 'ready' ? '本批次没有新增或材料变化的公告。' : '暂未得到可展示的增量公告，不能据此判断没有更新。'}</Text>}
        <View className='pagination'><ActionButton icon='chevron-left' className='button-small' disabled={state.loading || state.busy || page <= 1} onClick={() => setPage(value => value - 1)}>上一页</ActionButton>
          <Text>第 {page} 页 · 共 {detail.notices.total} 份增量公告</Text>
          <ActionButton icon='chevron-right' className='button-small' disabled={state.loading || state.busy || !detail.notices.has_next} onClick={() => setPage(value => value + 1)}>下一页</ActionButton></View>
      </> : !state.loading && !runs.length && <Text className='muted'>暂无增量批次。开启开关不等于已完成抓取。</Text>}
      <ActionButton className='button-secondary' disabled={state.loading || state.busy} onClick={state.reload}>刷新本轮结果</ActionButton>
      <View className='incremental-navigation'><MenuRow label='来源详情' onClick={() => Taro.navigateTo({ url: `/pages/admin/status/index?id=${encodeURIComponent(source.id)}` })} /></View>
      <Text className='footnote'>仅展示本批次变化，关联岗位不代表新增空缺。</Text>
    </View>}
  </AdminGate>
}
