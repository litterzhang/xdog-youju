import { Text, View } from '@tarojs/components'
import Taro, { useDidShow, useRouter } from '@tarojs/taro'
import { useEffect, useRef, useState } from 'react'
import { ActionButton, Card, Feedback, NavigationArrow } from '../../../components/ui'
import { evidenceRows, getJob, jobReviewRows } from '../../../services/jobs'
import type { Job } from '../../../services/jobs'
import { fieldLabels, JobBusinessDetails, JobConditions, JobFacts } from '../presentation'
import { hasBusinessFacts } from '../position-display'
import { displayTime } from '../../../services/navigation'
import '../jobs.scss'

function failure(error: unknown): string { return error instanceof Error ? error.message : '读取岗位失败，请重试。' }
export default function JobDetailPage() {
  const params = useRouter().params
  const id = params.id || ''
  const includePartial = params.include_partial !== 'false'
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [loading, setLoading] = useState(true)
  const [revision, setRevision] = useState(0)
  const [showRecords, setShowRecords] = useState(false)
  const [showIssues, setShowIssues] = useState(false)
  const shown = useRef(false)
  useDidShow(() => {
    if (shown.current) setRevision(v => v + 1)
    shown.current = true
  })
  useEffect(() => {
    let active = true
    setLoading(true); setError(''); setJob(null); setNotice('')
    void getJob(id, includePartial).then(value => { if (active) setJob(value) })
      .catch(reason => { if (active) setError(failure(reason)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [id, includePartial, revision])
  async function copyUrl() {
    if (!job) return
    try { await Taro.setClipboardData({ data: job.official_url }); setNotice('官方链接已复制，请打开并核对原文。') }
    catch { setNotice('复制失败，可长按选择下方官方链接。') }
  }
  async function openFeedback() {
    if (!job) return
    try { await Taro.navigateTo({ url: `/pages/jobs/feedback/index?id=${encodeURIComponent(job.id)}&include_partial=${includePartial}` }) }
    catch { setNotice('暂时无法打开解析反馈，请重试。') }
  }
  return <View className='screen jobs-screen'>
    <Feedback error={error} notice={notice} />
    {loading && <View role='status'><Text className='loading-copy'>正在读取岗位详情…</Text></View>}
    {error && <ActionButton className='button-secondary' onClick={() => setRevision(v => v + 1)}>重试读取详情</ActionButton>}
    {job && <>
      <Card className='jobs-card job-overview'><JobFacts job={job} /></Card>
      {hasBusinessFacts(job) && <Card className='jobs-card job-detail-section'>
        <Text className='job-section-title'>岗位信息</Text>
        <JobBusinessDetails job={job} />
      </Card>}
      <Card className='jobs-card job-detail-section job-conditions-card'>
        <Text className='job-section-title'>招聘条件</Text>
        <JobConditions job={job} />
      </Card>
      <Card className='jobs-card job-detail-section'>
        <Text className='job-section-title'>信息核验</Text>
        {jobReviewRows(job).map(item => <View className='job-record-row' key={item.label}>
          <Text className='job-fact-label'>{item.label}</Text><Text className='job-fact-value'>{item.value}</Text>
        </View>)}
        {job.field_issues.length > 0 && <View className='job-field-issues'>
          <ActionButton className='button-text job-record-toggle' aria-expanded={showIssues} onClick={() => setShowIssues(value => !value)}><Text>{showIssues ? '收起具体疑点' : `查看 ${job.field_issues.length} 项疑点`}</Text><NavigationArrow subtle /></ActionButton>
          {showIssues && job.field_issues.map((issue, i) => <Text className='job-review-note' key={i}>{issue.message}</Text>)}
        </View>}
      </Card>
      <Card className='jobs-card job-detail-section'>
        <Text className='job-section-title'>官方来源</Text>
        <View className='job-record-row'><Text className='job-fact-label'>来源</Text><Text className='job-fact-value'>{job.source_name}</Text></View>
        <View className='job-record-row'><Text className='job-fact-label'>最近检查</Text><Text className='job-fact-value'>{job.last_successful_check ? displayTime(job.last_successful_check) : '未知'}</Text></View>
        <Text className='job-official-url' selectable>{job.official_url}</Text>
        <ActionButton className='button-secondary jobs-copy-action' onClick={copyUrl}>复制官方原文链接</ActionButton>
        <ActionButton className='button-text job-record-toggle' aria-expanded={showRecords} onClick={() => setShowRecords(value => !value)}>
          <Text>{showRecords ? '收起材料记录' : '材料与解析记录'}</Text><NavigationArrow subtle />
        </ActionButton>
        {showRecords && <View className='job-records'>
          <View className='job-record-row'><Text className='job-fact-label'>材料审查</Text><Text className='job-fact-value'>{displayTime(job.audit_evaluated_at)}</Text></View>
          <View className='job-record-row'><Text className='job-fact-label'>期限判断</Text><Text className='job-fact-value'>{job.expiry_evaluated_on}</Text></View>
          <Text className='jobs-copy' selectable>判断依据：{job.validity.reason}</Text>
          {job.facts.requirements_text.value && <View className='jobs-evidence'><Text className='job-fact-label'>招聘要求原文</Text><Text className='job-fact-value' selectable>{job.facts.requirements_text.value}</Text></View>}
          {job.warnings.map((warning, i) => <Text className='jobs-copy' selectable key={i}>[{warning.code}] {warning.message}{warning.url ? ` · ${warning.url}` : ''}</Text>)}
          {evidenceRows(job).map(({ field, block }, i) => <View className='jobs-evidence' key={`${field}-${block.id}-${i}`}>
            <Text className='job-fact-label'>{fieldLabels[field] || field}</Text>
            <Text className='job-fact-value' selectable>{block.text}</Text>
            <Text className='jobs-copy' selectable>{block.locator}</Text>
          </View>)}
          <Text className='jobs-copy' selectable>材料版本：{job.bundle_id}</Text>
          <Text className='jobs-copy' selectable>解析记录：{job.audit_id}</Text>
        </View>}
      </Card>
      <Text className='job-detail-disclaimer'>信息仅供参考，报名条件以官方原文为准；不保证报名资格或录取。</Text>
      <ActionButton className='button-secondary jobs-detail-action' onClick={openFeedback}>反馈解析问题 / 查看我的反馈</ActionButton>
    </>}
  </View>
}
