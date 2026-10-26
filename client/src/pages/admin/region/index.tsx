import { Input, Text } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useState } from 'react'
import { ActionButton, AdminGate, Card, Field } from '../../../components/ui'
import { useAdminData } from '../../../hooks/use-admin-data'
import { request } from '../../../services/api'
import { Region } from '../../../services/contracts'
import { registryOptions } from '../../../services/registry'

export default function RegionPage() {
  const state = useAdminData(() => registryOptions<Region>('/admin/regions'))
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  return <AdminGate {...state} retry={state.reload}><Card className='form-card'>
    <Field label='地区代码' help='保留稳定的地区代码，已有记录不覆盖。'><Input className='input-control' placeholder='例如：340000' value={code} maxlength={32} disabled={state.busy} onInput={e => setCode(e.detail.value)} /></Field>
    <Field label='地区名称'><Input className='input-control' placeholder='例如：安徽省' value={name} maxlength={128} disabled={state.busy} onInput={e => setName(e.detail.value)} /></Field>
  </Card><ActionButton className='button-primary' loading={state.busy} disabled={state.busy || !code.trim() || !name.trim()} onClick={() => state.mutate(async () => {
    await request('/admin/regions', { code, name }); await Taro.navigateBack()
  })}>保存地区</ActionButton><Text className='footnote'>地区标签只说明来源归属或覆盖范围，不等于报名户籍限制。</Text></AdminGate>
}
