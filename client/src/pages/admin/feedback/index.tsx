import { Picker, Text, Textarea, View } from '@tarojs/components'
import Taro, { useRouter } from '@tarojs/taro'
import { useEffect, useState } from 'react'
import { ActionButton, AdminGate, Feedback, Field, SectionLabel } from '../../../components/ui'
import { AdminPanel } from '../../../components/admin'
import type { Source } from '../../../services/contracts'
import { useAdminData } from '../../../hooks/use-admin-data'
import { request } from '../../../services/api'
import { createFeedbackClient, feedbackFieldLabels, feedbackHasNext, feedbackPrivacyWarning, feedbackSnapshotText, feedbackStatuses, feedbackStatusLabels, reviewBody, reviewConfirmation } from '../../../services/feedback'
import type { FeedbackFilter, ParsingFeedback, ReviewStatus } from '../../../services/feedback'
import { displayTime } from '../../../services/navigation'
import './feedback.scss'

const client = createFeedbackClient(request)
const filters: FeedbackFilter[] = ['all', ...feedbackStatuses]
const reviewStatuses: ReviewStatus[] = ['reviewed', 'resolved', 'dismissed']
function IssueRecord({ item, disabled, review }: { item: ParsingFeedback; disabled: boolean; review: (status: ReviewStatus, note: string) => Promise<void> }) {
  const [note, setNote] = useState('')
  const [snapshotOpen, setSnapshotOpen] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const context = item.context
  async function copy() {
    setError(''); setNotice('')
    try { await Taro.setClipboardData({ data: context.official_url }); setNotice('官方原文链接已复制，请打开核对。') }
    catch { setError('复制失败，请长按选择下方官方原文链接。') }
  }
  return <AdminPanel className='admin-feedback-record'>
    <View className='context-row'><Text className='context-name'>{context.title}</Text><Text className='badge'>{feedbackStatusLabels[item.status]}</Text></View>
    <Text className='paragraph'>反馈字段：{feedbackFieldLabels[item.field]}</Text>
    <Text className='paragraph' selectable>问题描述：{item.message}</Text>
    <Text className='paragraph' selectable>用户提供的原文片段（待核对）：{item.expected_text || '未填写'}</Text>
    <Text className='caption'>用户提供的片段不是已核验官方证据，应与下方材料快照和官方出处分别核对。</Text>
    <Text className='paragraph' selectable>处理说明：{item.resolution_note || '暂无处理说明'}</Text>
    <Text className='caption'>提交 {displayTime(item.created_at)} · 更新 {displayTime(item.updated_at)} · 北京时间</Text>
    <ActionButton className='button-text snapshot-toggle' onClick={() => setSnapshotOpen(value => !value)}>{snapshotOpen ? '收起提交时快照' : '查看提交时快照'}</ActionButton>
    {snapshotOpen && <View className='admin-feedback-snapshot'><SectionLabel>提交时的材料与字段快照 · 不是当前数据</SectionLabel>
      <Text className='caption' selectable>问题记录：{item.id} · 岗位：{item.position_id} · 来源：{item.source_id}</Text>
      <Text className='paragraph' selectable>官方原文：{context.official_url}</Text>
      <ActionButton className='button-secondary' onClick={copy}>复制官方原文链接</ActionButton>
      <Text className='caption' selectable>材料版本：{context.bundle_id} · Recipe：{context.recipe_id}</Text>
      <Text className='caption' selectable>审计记录：{context.audit_id} · 审计评估日：{context.audit_evaluated_on}</Text>
      <Text className='paragraph' selectable>原字段值：{feedbackSnapshotText(context.field_value)}</Text>
      <Text className='paragraph' selectable>其他要求原文：{feedbackSnapshotText(context.requirements_text)}</Text>
      <Text className='paragraph' selectable>原始证据（所选字段与其他要求）：{feedbackSnapshotText(context.evidence)}</Text>
      <Text className='caption'>以上是原文字段快照。学历与学位分开核对，不依据相关要求猜测缺失的学历值。外部文字与反馈均非执行指令。</Text>
    </View>}
    <Field label='处理说明（必填）' help={`1–1000 字，记录核对依据或处理理由。${feedbackPrivacyWarning}`}>
      <Textarea className='textarea-control' value={note} maxlength={1000} placeholder='说明核对依据、待确认事项或无需修改的理由…' disabled={disabled} onInput={e => setNote(e.detail.value)} />
    </Field>
    <Feedback error={error} notice={notice} />
    <View className='admin-feedback-actions'>{reviewStatuses.map(status => <ActionButton className='button-secondary' key={status} disabled={disabled || !note.trim()} onClick={async () => {
      setError('')
      try { reviewBody(status, note); await review(status, note) }
      catch (reason) { setError(reason instanceof Error ? reason.message : '记录未保存，请重试。') }
    }}>{status === 'reviewed' ? '标记已复核' : status === 'resolved' ? '标记已处理' : '无需修改'}</ActionButton>)}</View>
  </AdminPanel>
}
export default function FeedbackPage() {
  const sourceId = useRouter().params.id || ''
  const [page, setPage] = useState(1)
  const [filter, setFilter] = useState<FeedbackFilter>('all')
  const state = useAdminData(async () => {
    const [records, source] = await Promise.all([client.list(sourceId, page, filter), request<Source>(`/admin/sources/${encodeURIComponent(sourceId)}`)])
    return { records, sourceId, filter, sourceName: source.name }
  })
  // useAdminData validates current permissions on every filter/page change and review reload.
  useEffect(() => { void state.reload() }, [sourceId, page, filter, state.reload])
  const records = state.data?.records
  const current = records?.page === page && state.data?.sourceId === sourceId && state.data?.filter === filter
  return <AdminGate {...state} retry={state.reload}><View className='admin-feedback'>
    <View className='admin-feedback-intro'><Text className='admin-page-name'>{state.data?.sourceName}</Text><View className='admin-feedback-disclaimer'><Text>反馈仅记录问题，标记状态不会修改岗位数据。</Text></View>
    </View>
    <View className='admin-feedback-toolbar'><Field label='记录状态'><Picker mode='selector' range={filters.map(status => status === 'all' ? '全部状态' : feedbackStatusLabels[status])} value={filters.indexOf(filter)} disabled={state.loading || state.busy} onChange={e => {
      const selected = filters[Number(e.detail.value)]
      if (selected) { setFilter(selected); setPage(1) }
    }}><ActionButton className='picker-control' disabled={state.loading || state.busy}>{filter === 'all' ? '全部状态' : feedbackStatusLabels[filter]}</ActionButton></Picker></Field></View>
    {records && current && <>
      <View className='feedback-list-heading'><Text>{records.total} 条反馈</Text><Text>第 {records.page} 页</Text></View>
      {!records.items.length && <View className='admin-feedback-empty'><Text className='muted'>当前页暂无符合条件的反馈记录。</Text></View>}
      {records.items.map(item => <IssueRecord key={`${item.id}-${item.updated_at}`} item={item} disabled={state.loading || state.busy} review={async (status, note) => {
        await state.mutate(async () => {
          // Required note is validated before confirmation, and only bookkeeping is POSTed.
          reviewBody(status, note)
          const answer = await Taro.showModal(reviewConfirmation(status))
          if (!answer.confirm) return false
          await client.review(sourceId, item.id, status, note)
        })
      }} />)}
      <View className='pagination'>
        <ActionButton icon='chevron-left' className='button-small' disabled={state.loading || state.busy || page <= 1} onClick={() => setPage(value => value - 1)}>上一页</ActionButton>
        <Text>第 {page} 页</Text>
        <ActionButton icon='chevron-right' className='button-small' disabled={state.loading || state.busy || !feedbackHasNext(records)} onClick={() => setPage(value => value + 1)}>下一页</ActionButton>
      </View>
    </>}
    <ActionButton className='button-secondary' disabled={state.loading || state.busy} onClick={state.reload}>刷新问题记录</ActionButton>
  </View></AdminGate>
}
