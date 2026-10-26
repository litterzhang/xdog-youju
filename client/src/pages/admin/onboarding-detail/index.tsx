import { Text, View } from '@tarojs/components'
import { useRouter } from '@tarojs/taro'
import { useState } from 'react'
import { AdminPanel, AdminStats } from '../../../components/admin'
import { ActionButton, AdminGate } from '../../../components/ui'
import { useAdminData } from '../../../hooks/use-admin-data'
import { request } from '../../../services/api'
import { counts, repairStatusLabel, Source, stageLabel, stateLabel } from '../../../services/contracts'
import { onboardingCompleted, onboardingStageNames, onboardingStages } from '../../../services/incremental'
import { blockedExplanation } from '../../../services/onboarding-recovery'
import { displayTime } from '../../../services/navigation'
import '../status/status.scss'

function DataRow({ label, value }: { label: string; value: string }) {
  return <View className='source-data-row'><Text className='source-row-label'>{label}</Text><Text className='source-data-value' selectable>{value}</Text></View>
}
export default function OnboardingDetailPage() {
  const id = useRouter().params.id || ''
  const state = useAdminData(() => request<Source>(`/admin/sources/${encodeURIComponent(id)}`))
  const [technical, setTechnical] = useState(false)
  const source = state.data
  const completed = !!source && onboardingCompleted(source.onboarding)
  const audit = source?.onboarding.summary.audit
  const classification = source?.onboarding.summary.classification
  const recovery = source?.onboarding.recovery
  const stageIndex = onboardingStages.findIndex(stage => stage === source?.onboarding.stage)
  return <AdminGate {...state} retry={state.reload}>
    {source && <View className='onboarding-detail'>
      <View className='admin-page-context'><Text className='admin-page-name'>{source.name}</Text>
        <Text className='caption'>{completed ? '接入完成' : '状态更新'} {displayTime(completed ? source.onboarding.completed_at : source.onboarding.updated_at)} · 北京时间</Text>
      </View>
      <AdminPanel title={completed ? '接入摘要（已冻结）' : '接入执行摘要'}>
        <Text className='muted'>{completed ? '接入基线与摘要已冻结，不随维护和增量批次变化。' : '接入尚未完成，以下为当前执行记录。'}</Text>
        <AdminStats items={[{ value: audit?.total_announcements ?? '—', label: '识别公告' }, { value: audit?.processed ?? '—', label: '已审查公告' },
          { value: audit?.position_count ?? '—', label: '已提取岗位记录' }, { value: audit?.partial_position_count ?? '—', label: '其中部分解析岗位' }]} />
        <DataRow label='公告解析' value={counts(audit?.statuses)} />
        <DataRow label='审查评估日' value={audit?.evaluated_on || '暂无'} />
        {source.onboarding.summary.error && <View className='feedback feedback--error'>{completed ? '接入摘要错误（冻结记录）' : '阻塞原因'}：{source.onboarding.summary.error.code}</View>}
        {source.onboarding.summary.inventory_incomplete && <View className='feedback feedback--error'>全站扫描不完整，不能视为已全量覆盖。</View>}
      </AdminPanel>
      <AdminPanel title={completed ? '接入阶段（冻结记录）' : '接入阶段'} className='timeline'>
        {onboardingStages.map((stage, index) => <View className={`timeline-row ${index === stageIndex ? 'timeline-row--current' : ''}`} key={stage}>
          <View className='timeline-dot' /><View className='timeline-copy'><Text>{onboardingStageNames[index]}</Text>
            <Text className='caption'>{index === stageIndex ? stateLabel(source.onboarding.state) : index < stageIndex ? '已推进' : '尚未进入'}</Text></View>
        </View>)}
      </AdminPanel>
      <AdminPanel title='来源与采集'>
        <DataRow label='来源地址' value={source.base_url} />
        <DataRow label='来源核验' value={displayTime(source.verified_at)} />
        <DataRow label={completed ? '接入采集队列（冻结记录）' : '采集队列'} value={counts(source.onboarding.acquisition)} />
        {recovery && <DataRow label='当前执行限制' value={`深度 ${recovery.limits.max_depth} · 资源 ${recovery.limits.max_resources}`} />}
      </AdminPanel>
      {source.onboarding.maintenance && <AdminPanel title='接入后维护（独立状态）'>
        <Text className='paragraph'>{stageLabel(source.onboarding.maintenance.stage)} · {stateLabel(source.onboarding.maintenance.state)}</Text>
        {source.onboarding.maintenance.error?.code && <Text className='caption'>{blockedExplanation(source.onboarding.maintenance.error.code)}（{source.onboarding.maintenance.error.code}）</Text>}
        <Text className='caption'>维护状态不改变已冻结的接入摘要；增量批次在增量抓取页独立记录。</Text>
      </AdminPanel>}
      <ActionButton className='button-text technical-toggle' onClick={() => setTechnical(value => !value)}>{technical ? '收起审查明细与任务记录' : '展开审查明细与任务记录'}</ActionButton>
      {technical && <>
        <AdminPanel title='审查明细'>
          <DataRow label='成本路由（非期限结论）' value={counts(audit?.routing_categories)} />
          <DataRow label='实际提取路径（非质量等级）' value={counts(audit?.extraction_paths)} />
          <DataRow label='解析错误与字段提示' value={`错误：${counts(audit?.errors_by_code)} · 字段：${counts(audit?.field_issues_by_code)}`} />
          <DataRow label='材料与覆盖提示' value={`材料：${counts(audit?.warnings_by_code)} · 覆盖：${counts(audit?.coverage_issues_by_code)}`} />
          <DataRow label='本地分类覆盖' value={typeof classification?.cheap_coverage === 'number' ? `${(classification.cheap_coverage * 100).toFixed(1)}% · 不是准确率` : '暂无分类结果'} />
          {recovery && <><DataRow label='超出深度资源' value={String(recovery.depth_limited_resources)} /><DataRow label='阻塞限制' value={recovery.blocking_limits.join(' · ') || '无已记录限制'} /></>}
        </AdminPanel>
        <AdminPanel title='版本与任务记录'>
          <DataRow label='接入基线版本' value={source.onboarding.baseline_recipe_id || '暂无冻结基线'} />
          <DataRow label='接入基线审查' value={source.onboarding.baseline_audit_id || '暂无冻结审查'} />
          <DataRow label='当前已采用版本' value={source.onboarding.active_revision_id || '尚未采用'} />
          <DataRow label='候选版本（未采用）' value={source.onboarding.candidate_revision_id || '暂无候选'} />
          {source.onboarding.summary.authoring_job ? <>
            <DataRow label='工作流任务' value={source.onboarding.summary.authoring_job.id || '暂无任务标识'} />
            <DataRow label='任务状态' value={source.onboarding.summary.authoring_job.status || '等待 Recipe 服务处理或同步结果'} />
          </> : <Text className='muted'>暂无工作流任务。</Text>}
        </AdminPanel>
        <AdminPanel title='Recipe 修复记录'>
          {!source.incremental?.repairs?.length && <Text className='muted'>暂无修复记录。</Text>}
          {source.incremental?.repairs?.map(repair => <View className='source-task' key={repair.id}>
            <Text className='paragraph'>{repairStatusLabel(repair.status)}</Text><Text className='caption'>更新 {displayTime(repair.updated_at)} · 北京时间</Text>
            <DataRow label='修复记录' value={repair.id} /><DataRow label='任务标识' value={repair.task_id || '尚未记录'} />
            <DataRow label='基础版本' value={repair.base_recipe_id} /><DataRow label='候选版本' value={repair.candidate_recipe_id || '暂无候选'} />
            {repair.error_code && <DataRow label='错误' value={repair.error_code} />}
          </View>)}
        </AdminPanel>
      </>}
      <ActionButton className='button-secondary' disabled={state.loading || state.busy} onClick={state.reload}>刷新详情</ActionButton>
    </View>}
  </AdminGate>
}
