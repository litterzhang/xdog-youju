import { Button, Input, Picker, Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useState } from 'react'
import { ActionButton, AdminGate, Card, Field, NavigationArrow, Steps } from '../../../components/ui'
import { useAdminData } from '../../../hooks/use-admin-data'
import { request } from '../../../services/api'
import { Organization, Region } from '../../../services/contracts'
import { registryOptions } from '../../../services/registry'

const kinds = ['university', 'school', 'government', 'hospital', 'research_institute', 'other']
const kindLabels = ['大学', '学校', '政府机关', '医院', '科研机构', '其他']
export default function OrganizationStep() {
  const state = useAdminData(async () => {
    const [organizations, regions] = await Promise.all([registryOptions<Organization>('/admin/organizations'), registryOptions<Region>('/admin/regions')])
    return { organizations, regions }
  })
  const [mode, setMode] = useState<'existing' | 'new'>('existing')
  const [orgId, setOrgId] = useState('')
  const [name, setName] = useState('')
  const [domain, setDomain] = useState('')
  const [kind, setKind] = useState(0)
  const [regionCode, setRegionCode] = useState('')
  const organizations = state.data?.organizations || []
  const regions = state.data?.regions || []
  const selected = organizations.find(o => o.id === orgId)
  const chooseNew = mode === 'new' || organizations.length === 0
  async function next() {
    await state.mutate(async () => {
      const org = chooseNew ? await request<Organization>('/admin/organizations', {
        name, root_domain: domain, organization_type: kinds[kind], location_region_code: regionCode,
      }) : selected
      if (!org?.root_domain) throw new Error('请先配置组织的可注册根域名。')
      await Taro.navigateTo({ url: `/pages/admin/source/index?org=${encodeURIComponent(org.id)}` })
    })
  }
  return <AdminGate {...state} retry={state.reload}><Steps current={0} />
    <View className='segmented'><ActionButton className={!chooseNew ? 'segment segment--active' : 'segment'} onClick={() => setMode('existing')} disabled={state.busy || organizations.length === 0}>已有组织</ActionButton>
      <ActionButton className={chooseNew ? 'segment segment--active' : 'segment'} onClick={() => setMode('new')} disabled={state.busy}>登记组织</ActionButton></View>
    <Card className='form-card'>{chooseNew ? <>
      <Field label='组织名称'><Input className='input-control' value={name} maxlength={256} placeholder='例如：中国科学技术大学' disabled={state.busy} onInput={e => setName(e.detail.value)} /></Field>
      <Field label='发现范围根域名' help='使用可注册根域名，不填协议或路径；它只限定发现范围，不代表官方认证。'><Input className='input-control' value={domain} maxlength={253} placeholder='例如：ustc.edu.cn' disabled={state.busy} onInput={e => setDomain(e.detail.value)} /></Field>
      <Field label='组织类型'><Picker mode='selector' range={kindLabels} value={kind} disabled={state.busy} onChange={e => setKind(Number(e.detail.value))}><Button className='picker-control'>{kindLabels[kind]}<NavigationArrow subtle /></Button></Picker></Field>
      <Field label='组织所在地（可选）'><Picker mode='selector' range={['未指定', ...regions.map(r => r.name)]} value={regions.findIndex(r => r.code === regionCode) + 1} disabled={state.busy} onChange={e => setRegionCode(regions[Number(e.detail.value) - 1]?.code || '')}><Button className='picker-control'>{regions.find(r => r.code === regionCode)?.name || '未指定'}<NavigationArrow subtle /></Button></Picker></Field>
    </> : <><Field label='所属组织'><Picker mode='selector' range={['请选择', ...organizations.map(o => o.name)]} value={organizations.findIndex(o => o.id === orgId) + 1} disabled={state.busy} onChange={e => setOrgId(organizations[Number(e.detail.value) - 1]?.id || '')}><Button className='picker-control'>{selected?.name || '请选择组织'}<NavigationArrow subtle /></Button></Picker></Field>
      <View className='detail-line'><Text>发现范围</Text><Text>{selected?.root_domain || '选择组织后显示'}</Text></View></>}</Card>
    <ActionButton icon='chevron-right' className='button-primary' disabled={state.busy || (chooseNew ? !name.trim() || !domain.trim() : !selected?.root_domain)} loading={state.busy} onClick={next}>下一步：登记来源</ActionButton>
    <ActionButton className='button-text' disabled={state.busy} onClick={() => Taro.navigateTo({ url: '/pages/admin/region/index' })}>缺少地区？登记地区</ActionButton>
    <Text className='footnote'>组织是发布主体。所在地与来源覆盖地区独立记录，不自动推断岗位工作地点。</Text>
  </AdminGate>
}
