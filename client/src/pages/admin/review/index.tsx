import { Text, Textarea, View } from '@tarojs/components'
import Taro, { useRouter } from '@tarojs/taro'
import { useState } from 'react'
import { ActionButton, AdminGate, Card, Field, Steps } from '../../../components/ui'
import { useAdminData } from '../../../hooks/use-admin-data'
import { request } from '../../../services/api'
import { registryStatusLabel, Source } from '../../../services/contracts'
import { displayTime } from '../../../services/navigation'

export default function ReviewStep() {
  const id = useRouter().params.id || ''
  const state = useAdminData(() => request<Source>(`/admin/sources/${encodeURIComponent(id)}`))
  const [evidence, setEvidence] = useState('')
  const source = state.data
  return <AdminGate {...state} retry={state.reload}><Steps current={2} />
    {source && <><Card className='detail-card'><View className='context-row'><Text className='context-name'>{source.name}</Text><Text className='badge'>{registryStatusLabel(source.status)}</Text></View>
      <Text className='muted'>{source.organization_name}</Text>
      <View className='detail-line'><Text>来源入口</Text><Text className='detail-value'>{source.base_url}</Text></View>
      <ActionButton className='button-text' onClick={() => Taro.setClipboardData({ data: source.base_url })}>复制来源地址</ActionButton></Card>
      {source.status === 'candidate' ? <><Card className='form-card'><Field label='官方归属依据' help='填写发布主体、核对出处及理由。域名猜测或转载页面不能代替官方依据。'>
        <Textarea className='textarea-control textarea-control--large' placeholder='记录已人工核对的依据…' maxlength={2000} value={evidence} disabled={state.busy} onInput={e => setEvidence(e.detail.value)} />
      </Field></Card><ActionButton className='button-primary' disabled={state.busy || !evidence.trim()} loading={state.busy} onClick={() => state.mutate(async () => {
        const result = await Taro.showModal({ title: '确认官方来源', content: '确认已人工核对发布主体与官方依据？批准后，独立 worker 才有资格访问此来源。' })
        if (!result.confirm) return false
        await request(`/admin/sources/${encodeURIComponent(id)}/approve`, { authority_evidence: evidence })
      })}>确认依据并批准来源</ActionButton></> : <Card className='detail-card'>
        <Text className='section-label'>已记录的官方依据</Text><Text className='paragraph'>{source.authority_evidence || '尚未提供'}</Text>
        <Text className='caption'>人工核验：{displayTime(source.verified_at)} · 北京时间</Text></Card>}
      {source.status === 'active' && source.onboarding.stage === 'not_started' && <ActionButton className='button-primary' loading={state.busy} disabled={state.busy} onClick={() => state.mutate(async () => {
        const result = await Taro.showModal({ title: '启动来源接入', content: '提交全站发现与材料采集任务？页面请求只排队，任务由独立 worker 推进。' })
        if (!result.confirm) return false
        await request(`/admin/sources/${encodeURIComponent(id)}/onboard`, {})
        await Taro.redirectTo({ url: `/pages/admin/status/index?id=${encodeURIComponent(id)}` })
      })}>提交接入任务</ActionButton>}
      {source.onboarding.stage !== 'not_started' && <ActionButton className='button-secondary' onClick={() => Taro.redirectTo({ url: `/pages/admin/status/index?id=${encodeURIComponent(id)}` })}>查看来源详情</ActionButton>}
    </>}
    <Text className='footnote'>来源批准与任务启动是两个独立步骤。人工来源核验时间不是公告发布时间或最近抓取时间。</Text>
  </AdminGate>
}
