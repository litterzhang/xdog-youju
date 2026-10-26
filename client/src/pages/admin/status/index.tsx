import { Text, View } from '@tarojs/components'
import Taro, { useDidHide, useDidShow, useRouter } from '@tarojs/taro'
import { useEffect, useState } from 'react'
import { ActionButton, AdminGate, Feedback, MenuRow } from '../../../components/ui'
import { AdminPanel, AdminStats } from '../../../components/admin'
import { useAdminData } from '../../../hooks/use-admin-data'
import { ApiError, request } from '../../../services/api'
import { Source, SourceParsingSummary, stageLabel, stateLabel } from '../../../services/contracts'
import { blockedExplanation, resumeConfirmation, resumeLimits } from '../../../services/onboarding-recovery'
import { incrementalNavigationDetail, incrementalRoute, onboardingCompleted, onboardingRecoveryTask } from '../../../services/incremental'
import { displayTime } from '../../../services/navigation'
import { feedbackRoute } from '../../../services/feedback'
import './status.scss'

export default function StatusPage() {
  const id = useRouter().params.id || ''
  const state = useAdminData(async () => {
    const source = await request<Source>(`/admin/sources/${encodeURIComponent(id)}`)
    try {
      const summary = await request<SourceParsingSummary>(`/admin/sources/${encodeURIComponent(id)}/parsing-summary`)
      if (summary.source_id !== source.id || summary.scope !== 'current_saved_notices') throw new Error('解析摘要来源不匹配。')
      return { source, summary, summaryError: '' }
    } catch (reason) {
      if (reason instanceof ApiError && [401, 403].includes(reason.status)) throw reason
      return { source, summary: null, summaryError: '解析摘要暂不可用，请稍后刷新。' }
    }
  })
  const [copyError, setCopyError] = useState('')
  const [visible, setVisible] = useState(false)
  useDidShow(() => setVisible(true)); useDidHide(() => setVisible(false))
  useEffect(() => {
    if (!visible || !state.allowed || state.loading || state.busy) return
    const timer = setInterval(() => { void state.reload() }, 15000)
    return () => clearInterval(timer)
  }, [visible, state.allowed, state.loading, state.busy, state.reload])
  const source = state.data?.source
  const summary = state.data?.summary
  const recovery = source?.onboarding.recovery
  const completed = !!source && onboardingCompleted(source.onboarding)
  const recoveryTask = source ? onboardingRecoveryTask(source.onboarding) : null
  const blockedTask = recoveryTask?.blockedTask || false
  async function copy(value: string) {
    setCopyError('')
    try { await Taro.setClipboardData({ data: value }) }
    catch { setCopyError('复制失败，请前往接入详情长按选择完整标识或来源地址。') }
  }
  return <AdminGate {...state} retry={state.reload}>
    {source && <View className='source-status'>
      <AdminPanel className='source-overview'>
        <View className='context-row'><Text className='context-name'>{source.name}</Text><Text className='badge'>{stateLabel(source.onboarding.state)}</Text></View>
        <Text className='muted'>{source.organization_name}</Text>
        <Text className='paragraph'>{stageLabel(source.onboarding.stage)}</Text>
        <Text className='caption'>{completed ? '接入完成' : '状态更新'} {displayTime(completed ? source.onboarding.completed_at : source.onboarding.updated_at)} · 北京时间</Text>
      </AdminPanel>
      <AdminPanel title='解析情况' className='source-parsing-summary'>
        <Text className='muted'>全来源已识别公告 · 岗位期限以各岗位事实为准</Text>
        {summary ? <>
          <AdminStats items={[{ label: '公告总数', value: summary.notice_count }, { label: '已解析岗位记录', value: summary.position_count }]} />
          <View className='admin-inline-stats'>
            {!!summary.statuses.associated && <Text>已关联更正 {summary.statuses.associated}</Text>}
            {!!summary.statuses.cancelled && <Text>公告已取消 {summary.statuses.cancelled}</Text>}
            {!!summary.statuses.empty && <Text>当前材料无岗位行 {summary.statuses.empty}</Text>}
            {!!summary.statuses.archived && <Text>低成本归档（无岗位事实，非过期确认） {summary.statuses.archived}</Text>}
            <Text>完整解析 {summary.statuses.success}</Text><Text>部分材料 {summary.statuses.partial}</Text>
            <Text>待复核 {summary.statuses.review} · 需核验（含字段提示） {summary.review_required}</Text><Text>失败 {summary.statuses.failed}</Text><Text>待解析 {summary.statuses.pending}</Text>
          </View>
          {summary.unclassified_pages > 0 && <Text className='caption'>{summary.unclassified_pages} 个已保存页面待分类，公告总数可能变化。</Text>}
          <Text className='caption'>{summary.evaluated_through ? `保存审查日期 ${summary.evaluated_from} — ${summary.evaluated_through}` : '尚无当前版本审查结果'}</Text>
        </> : <Text className='caption'>{state.data?.summaryError}</Text>}
      </AdminPanel>
      {source.onboarding.maintenance && <AdminPanel title='接入后维护'>
        <Text className='paragraph'>{stageLabel(source.onboarding.maintenance.stage)} · {stateLabel(source.onboarding.maintenance.state)}</Text>
        {source.onboarding.maintenance.error?.code && <Text className='caption'>{blockedExplanation(source.onboarding.maintenance.error.code)}（{source.onboarding.maintenance.error.code}）</Text>}
      </AdminPanel>}
      {(blockedTask || recovery?.can_repair) && <AdminPanel className='source-recovery' title={blockedTask ? recoveryTask?.label : '解析脚本维护'}>
        <Text className='paragraph'>{blockedTask ? blockedExplanation(recoveryTask?.errorCode, recovery) : '依据最近的公告审查修复脚本与工作流。审查通过前，当前版本及正常岗位保持可用。'}</Text>
        {recovery?.can_resume && <ActionButton className='button-primary' disabled={state.busy || state.loading} loading={state.busy} onClick={() => state.mutate(async () => {
          const answer = await Taro.showModal({ title: recoveryTask?.resumeTitle, content: resumeConfirmation(recovery) })
          if (!answer.confirm) return false
          await request(`/admin/sources/${encodeURIComponent(id)}/resume`, resumeLimits(recovery))
        })}>{Object.keys(recovery.suggested_limits).length ? '提高限制并继续' : '使用已有资料重试'}</ActionButton>}
        {recovery?.can_repair && <ActionButton className='button-primary' disabled={state.busy || state.loading} loading={state.busy} onClick={() => state.mutate(async () => {
          const answer = await Taro.showModal({ title: '请求 Agent 修复', content: '让 Recipe 服务依据现有审计反馈修复解析脚本与工作流，再执行完整审计？可能产生模型费用，不会直接激活未经审计的结果。' })
          if (!answer.confirm) return false
          await request(`/admin/sources/${encodeURIComponent(id)}/repair`, {})
        })}>请求 Agent 修复</ActionButton>}
        {(!recovery || recovery.requires_operator) && <Text className='caption'>当前没有安全的自动恢复操作。请检查来源配置、尚未结束的任务或可用审计样本；深度最多支持 100，不会绕过安全限制。</Text>}
      </AdminPanel>}
      <AdminPanel title='解析版本' className='source-revisions'>
        {([
          { label: '当前版本', id: source.onboarding.active_revision_id, status: source.onboarding.active_revision_id ? '已采用' : '尚未采用' },
          { label: '候选版本', id: source.onboarding.candidate_revision_id, status: source.onboarding.candidate_revision_id ? '尚未采用 · 需完整审查通过' : '暂无候选' },
        ]).map(revision => <View className='source-revision-row' key={revision.label}>
          <Text className='source-row-label'>{revision.label}</Text>
          <View className='source-revision-copy'><Text className='source-short-id'>{revision.id ? `${revision.id.slice(0, 12)}${revision.id.length > 12 ? '…' : ''}` : '—'}</Text><Text className='caption'>{revision.status}</Text></View>
          {revision.id && <ActionButton className='button-text' aria-label={`复制${revision.label}完整标识`} onClick={() => copy(revision.id!)}>复制</ActionButton>}
        </View>)}
      </AdminPanel>
      <View className='source-navigation'>
        <MenuRow label='接入详情' detail={completed ? '冻结基线、审计与任务记录' : '阶段进度、审计与任务记录'} onClick={() => Taro.navigateTo({ url: `/pages/admin/onboarding-detail/index?id=${encodeURIComponent(source.id)}` })} />
        <MenuRow label='增量抓取' detail={incrementalNavigationDetail(source.incremental)} onClick={() => Taro.navigateTo({ url: incrementalRoute(source.id) })} />
        <MenuRow label='解析反馈' detail='查看与处理岗位解析问题' onClick={() => Taro.navigateTo({ url: feedbackRoute(source.id) })} />
      </View>
      <Feedback error={copyError} />
      <ActionButton className='button-secondary' disabled={state.loading || state.busy} onClick={state.reload}>{completed ? '刷新状态' : '刷新进度'}</ActionButton>
      <ActionButton className='button-text' onClick={() => copy(source.base_url)}>复制来源地址</ActionButton>
    </View>}
  </AdminGate>
}
