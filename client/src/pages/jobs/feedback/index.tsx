import { Button, Picker, Text, Textarea, View } from '@tarojs/components'
import Taro, { useDidHide, useDidShow, useRouter } from '@tarojs/taro'
import { useEffect, useRef, useState } from 'react'
import { ActionButton, ActionIcon, Card, Feedback, Field, SectionLabel } from '../../../components/ui'
import { getJob } from '../../../services/jobs'
import type { Job } from '../../../services/jobs'
import { ApiError, request } from '../../../services/api'
import { getIdentity } from '../../../services/session'
import { displayTime } from '../../../services/navigation'
import { createFeedbackClient, feedbackFieldLabels, feedbackFields, feedbackPrivacyWarning, feedbackStatusLabels } from '../../../services/feedback'
import type { FeedbackField, FeedbackPage } from '../../../services/feedback'
import { factText } from '../position-display'
import '../jobs.scss'

const feedbackClient = createFeedbackClient(request)
function JobFeedbackForm({ job }: { job: Job }) {
  const [field, setField] = useState<FeedbackField>('conditions')
  const [message, setMessage] = useState('')
  const [expected, setExpected] = useState('')
  const [own, setOwn] = useState<FeedbackPage | null>(null)
  const [authenticated, setAuthenticated] = useState(false)
  const [checking, setChecking] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [revision, setRevision] = useState(0)
  const epoch = useRef(0)
  const submitting = useRef(false)
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])
  const shown = useRef(false)
  useDidShow(() => {
    if (shown.current) setRevision(value => value + 1)
    shown.current = true
  })
  // Identity errors belong to this page, never to anonymous job reading.
  useEffect(() => {
    const attempt = ++epoch.current
    setOwn(null); setAuthenticated(false); setChecking(true); setError('')
    void (async () => {
      const identity = await getIdentity()
      if (epoch.current !== attempt || !identity) return
      setAuthenticated(true)
      const records = await feedbackClient.own(job.id)
      if (epoch.current === attempt) setOwn(records)
    })().catch(reason => {
      if (epoch.current !== attempt) return
      if (reason instanceof ApiError && reason.status === 401) setAuthenticated(false)
      setError(reason instanceof Error ? reason.message : '读取我的反馈失败，请重试。')
    }).finally(() => { if (epoch.current === attempt) setChecking(false) })
    return () => { epoch.current++ }
  }, [job.id, revision])
  useDidHide(() => {
    epoch.current++; setOwn(null); setAuthenticated(false); setChecking(true); setNotice(''); setError('')
    setMessage(''); setExpected('')
  })
  async function submit() {
    if (submitting.current || checking || !message.trim()) return
    submitting.current = true; setBusy(true); setError(''); setNotice('')
    const attempt = epoch.current
    try {
      // Re-check the current identity immediately before POST; no client ownership claim.
      const identity = await getIdentity()
      if (epoch.current !== attempt) return
      if (!identity) {
        setOwn(null); setAuthenticated(false)
        setNotice('请先登录。登录后可返回此页重新填写并提交反馈，匿名阅读不受影响。')
        await Taro.navigateTo({ url: '/pages/login/index' })
        return
      }
      setAuthenticated(true)
      const result = await feedbackClient.submit(job.id, { field, message, ...(expected === '' ? {} : { expected_text: expected }) })
      if (epoch.current !== attempt) return
      // Repeated submissions can return an already reviewed record; don't reset it to open.
      setNotice(`问题记录已保存 · ${feedbackStatusLabels[result.status]}。不会自动修复解析或更正岗位数据。`)
      setMessage(''); setExpected('')
      setRevision(value => value + 1)
    } catch (reason) {
      if (epoch.current !== attempt) return
      if (reason instanceof ApiError && reason.status === 401) { setAuthenticated(false); setOwn(null) }
      setError(reason instanceof Error ? reason.message : '反馈未提交，请重试。')
    } finally { submitting.current = false; if (mounted.current) setBusy(false) }
  }
  return <>
    <SectionLabel>解析反馈 · 仅记录问题</SectionLabel>
    <Card className='form-card'>
      <Text className='caption'>反馈仅记录问题，不会自动修改岗位数据。</Text>
      <Field label='反馈字段'><Picker mode='selector' range={feedbackFields.map(key => feedbackFieldLabels[key])} value={feedbackFields.indexOf(field)} disabled={busy} onChange={e => {
        const selected = feedbackFields[Number(e.detail.value)]
        if (selected) setField(selected)
      }}><Button className='picker-control' disabled={busy}>{feedbackFieldLabels[field]}<ActionIcon name='chevron-right' subtle /></Button></Picker></Field>
      <Field label='问题描述（必填）' help={`1–1000 字。${feedbackPrivacyWarning}`}>
        <Textarea className='textarea-control textarea-control--large' value={message} maxlength={1000} placeholder='描述原文字段遗漏或解析疑点，不填个人情况…' disabled={busy} onInput={e => setMessage(e.detail.value)} />
      </Field>
      <Field label='用户提供的原文片段（待核对）' help='选填，最多 500 字。用户提供的片段不是已核验官方证据。'>
        <Textarea className='textarea-control' value={expected} maxlength={500} placeholder='填写您提供的原文片段，供人工核对出处与内容…' disabled={busy} onInput={e => setExpected(e.detail.value)} />
      </Field>
      <Feedback error={error} notice={notice} />
      <ActionButton className='button-primary' disabled={busy || checking || !message.trim()} loading={busy} onClick={submit}>{authenticated ? '提交问题记录' : '登录后提交问题记录'}</ActionButton>
      {!authenticated && !checking && <>
        <Text className='caption'>匿名可继续阅读岗位；提交及查看自己的反馈需要登录。登录不会自动提交表单；登录后请重新打开此岗位的反馈页填写。</Text>
        <ActionButton className='button-secondary' disabled={busy} onClick={() => Taro.navigateTo({ url: '/pages/login/index' })}>先登录，再填写或查看我的反馈</ActionButton>
      </>}
      {checking && <View role='status'><Text className='loading-copy'>正在核验身份与读取我的反馈…</Text></View>}
      <ActionButton className='button-secondary' disabled={checking || busy} onClick={() => setRevision(value => value + 1)}>重新核验并读取我的反馈</ActionButton>
    </Card>
    {authenticated && own && <>
      <SectionLabel>我的反馈 · 最近最多 50 条</SectionLabel>
      {!own.items.length && <Card className='detail-card'><Text className='muted'>暂无本人反馈记录。</Text></Card>}
      {own.total > own.items.length && <Text className='caption'>当前展示最近 {own.items.length} 条，共 {own.total} 条；不是全部历史记录。</Text>}
      {own.items.map(item => <Card className='detail-card' key={item.id}>
        <Text className='section-label'>{feedbackFieldLabels[item.field]} · {feedbackStatusLabels[item.status]}</Text>
        <Text className='paragraph' selectable>{item.message}</Text>
        {item.expected_text !== null && <Text className='paragraph' selectable>用户提供的原文片段（待核对）：{item.expected_text || '未填写'}</Text>}
        <Text className='paragraph' selectable>处理说明：{item.resolution_note || '暂无处理说明'}</Text>
        <Text className='caption'>提交 {displayTime(item.created_at)} · 更新 {displayTime(item.updated_at)} · 北京时间</Text>
        <Text className='caption'>仅为问题记录状态，不代表数据已更正。</Text>
      </Card>)}
    </>}
  </>
}
export default function JobFeedbackPage() {
  const params = useRouter().params
  const id = params.id || ''
  const includePartial = params.include_partial !== 'false'
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [revision, setRevision] = useState(0)
  const shown = useRef(false)
  useDidShow(() => {
    if (shown.current) setRevision(value => value + 1)
    shown.current = true
  })
  useEffect(() => {
    let active = true
    setJob(null); setLoading(true); setError('')
    void getJob(id, includePartial).then(value => { if (active) setJob(value) })
      .catch(reason => { if (active) setError(reason instanceof Error ? reason.message : '读取岗位失败，请重试。') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [id, includePartial, revision])
  return <View className='screen jobs-screen'>
    <Feedback error={error} />
    {loading && <View role='status'><Text className='loading-copy'>正在读取反馈关联岗位…</Text></View>}
    {error && <ActionButton className='button-secondary' onClick={() => setRevision(value => value + 1)}>重试读取岗位</ActionButton>}
    {job && <>
      <View className='jobs-feedback-context'>
        <Text className='jobs-title'>{factText(job.facts.title)}</Text>
        <Text className='jobs-copy'>招聘单位：{factText(job.facts.hiring_unit)}</Text>
        <Text className='jobs-copy'>{job.material_status === 'partial' ? '部分材料 · 缺失材料可能影响报名条件' : '当前材料已解析，不代表已人工核验'}；当前招聘状态待核验。</Text>
      </View>
      <JobFeedbackForm key={job.id} job={job} />
    </>}
  </View>
}
