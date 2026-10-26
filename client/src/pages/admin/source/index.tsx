import { Button, Input, Picker, Text, Textarea, View } from '@tarojs/components'
import Taro, { useRouter } from '@tarojs/taro'
import { useState } from 'react'
import { ActionButton, AdminGate, Card, Field, NavigationArrow, Steps } from '../../../components/ui'
import { useAdminData } from '../../../hooks/use-admin-data'
import { request } from '../../../services/api'
import { lines, Organization, Region, Source } from '../../../services/contracts'
import { registryOptions } from '../../../services/registry'

export default function SourceStep() {
  const orgId = useRouter().params.org || ''
  const state = useAdminData(async () => {
    const [organizations, regions] = await Promise.all([registryOptions<Organization>('/admin/organizations'), registryOptions<Region>('/admin/regions')])
    const organization = organizations.find(o => o.id === orgId)
    if (!organization) throw new Error('未找到所选组织，请返回重新选择。')
    return { organization, regions }
  })
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [regionCode, setRegionCode] = useState('')
  const [seeds, setSeeds] = useState('')
  const [description, setDescription] = useState('')
  const regions = state.data?.regions || []
  return <AdminGate {...state} retry={state.reload}><Steps current={1} />
    <Card><View className='context-row'><Text className='context-name'>{state.data?.organization.name}</Text><Text className='badge'>{state.data?.organization.root_domain}</Text></View></Card>
    <Card className='form-card'>
      <Field label='来源名称'><Input className='input-control' placeholder='例如：人才招聘网站' maxlength={256} value={name} disabled={state.busy} onInput={e => setName(e.detail.value)} /></Field>
      <Field label='来源入口' help='仅接受组织根域名范围内的 HTTPS 地址。入口自身会作为发现种子。'><Input className='input-control' placeholder='https://…' maxlength={2048} value={url} disabled={state.busy} onInput={e => setUrl(e.detail.value)} /></Field>
      <Field label='覆盖地区（可选）'><Picker mode='selector' range={['未指定', ...regions.map(r => r.name)]} value={regions.findIndex(r => r.code === regionCode) + 1} disabled={state.busy} onChange={e => setRegionCode(regions[Number(e.detail.value) - 1]?.code || '')}><Button className='picker-control'>{regions.find(r => r.code === regionCode)?.name || '未指定'}<NavigationArrow subtle /></Button></Picker></Field>
      <Field label='补充种子地址（可选）' help='每行一个、最多 20 条，必须与来源入口同源。'><Textarea className='textarea-control' placeholder='公告列表等补充入口' maxlength={12000} value={seeds} disabled={state.busy} onInput={e => setSeeds(e.detail.value)} /></Field>
      <Field label='来源说明（可选）'><Textarea className='textarea-control' placeholder='来源负责发布哪些信息' maxlength={2000} value={description} disabled={state.busy} onInput={e => setDescription(e.detail.value)} /></Field>
    </Card><ActionButton className='button-primary' loading={state.busy} disabled={state.busy || !name.trim() || !url.trim()} onClick={() => state.mutate(async () => {
      const source = await request<Source>('/admin/sources', { name, organization_id: orgId, base_url: url, region_codes: regionCode ? [regionCode] : [], seed_urls: lines(seeds), description })
      await Taro.redirectTo({ url: `/pages/admin/review/index?id=${encodeURIComponent(source.id)}` })
    })}>保存并核验官方依据</ActionButton><Text className='footnote'>登记后仍为待核验来源，不会因为填写网址而自动启动抓取。</Text>
  </AdminGate>
}
