import { Image, Text, View } from '@tarojs/components'
import Taro, { useDidHide, useDidShow } from '@tarojs/taro'
import { useRef, useState } from 'react'
import { ActionButton, Card, Feedback, MenuRow, SectionLabel } from '../../components/ui'
import { logout } from '../../services/api'
import { accountDisplayName, canManageSources, Identity } from '../../services/contracts'
import { getIdentity } from '../../services/session'
import { checkHealth } from '../../services/health'

export default function Me() {
  const [identity, setIdentity] = useState<Identity | null>(null)
  const [loading, setLoading] = useState(true)
  const [failedAvatar, setFailedAvatar] = useState('')
  const [error, setError] = useState('')
  const [service, setService] = useState('未检查')
  const [checking, setChecking] = useState(false)
  const epoch = useRef(0)
  useDidShow(() => {
    const attempt = ++epoch.current
    setIdentity(null); setLoading(true); setError(''); setFailedAvatar('')
    void getIdentity().then(value => { if (epoch.current === attempt) setIdentity(value) })
      .catch(reason => { if (epoch.current === attempt) setError(reason instanceof Error ? reason.message : '身份核验未完成。') })
      .finally(() => { if (epoch.current === attempt) setLoading(false) })
  })
  useDidHide(() => { epoch.current++; setIdentity(null) })
  async function inspectService() {
    if (checking) return
    setChecking(true)
    try { await checkHealth(); setService('连接正常，仅表示 API 存活') }
    catch (reason) { setService(reason instanceof Error ? reason.message : '连接失败') }
    finally { setChecking(false) }
  }
  async function copyAccountId() {
    if (!identity) return
    setError('')
    try { await Taro.setClipboardData({ data: identity.id }) }
    catch { setError('账号编号复制失败，请长按编号选择复制。') }
  }
  async function signOut() {
    const result = await Taro.showModal({ title: '退出登录', content: '清除当前设备的登录状态？不会撤销其他设备的会话。' })
    if (!result.confirm) return
    epoch.current++; logout(); setIdentity(null); setError('')
  }
  return <View className='screen screen--tab'>
    <Feedback error={error} />
    <Card className='account-card account-card--with-id'><View className='account-header'><View className='account-mark'>
      {identity?.avatar && failedAvatar !== identity.avatar
        ? <Image className='account-avatar' src={identity.avatar} mode='aspectFill' aria-label='账号头像' onError={() => setFailedAvatar(identity.avatar || '')} />
        : <View className='person-glyph' />}
    </View><View className='account-copy'>
      <Text className='account-name'>{loading ? '核验身份中' : identity ? accountDisplayName(identity) : '尚未登录'}</Text>
      <Text className='muted'>{identity?.avatar && failedAvatar === identity.avatar ? '头像暂时无法加载' : identity ? '账号身份已由 zdog-auth 核验' : '登录后使用账号对应的已授权功能'}</Text>
    </View>{identity && <Text className='badge'>{canManageSources(identity) ? '管理权限' : '普通用户'}</Text>}</View>
      {identity && <View className='account-id-row'>
        <View className='account-id-copy'><Text className='account-id-label'>账号编号</Text><Text className='account-id-value' selectable>{identity.id}</Text></View>
        <ActionButton className='button-text account-id-action' subtle onClick={copyAccountId} aria-label='复制账号编号'>复制</ActionButton>
      </View>}
    </Card>
    {!identity && !loading && <ActionButton className='button-primary' onClick={() => Taro.navigateTo({ url: '/pages/login/index' })}>登录账号</ActionButton>}
    {canManageSources(identity) && <><SectionLabel>管理</SectionLabel><Card>
      <MenuRow label='来源接入与管理' detail='登记组织、核验官方来源、查看采集进度' badge='独立流程' onClick={() => Taro.navigateTo({ url: '/pages/admin/index' })} />
    </Card></>}
    <SectionLabel>服务与说明</SectionLabel>
    <Card><MenuRow trailingIcon={null} loading={checking} label='服务连接' detail={service} onClick={inspectService} disabled={checking} badge={checking ? '检查中' : '检查'} />
      <MenuRow label='数据与报名提示' detail='以官方公告为准，不保证报名资格。' /></Card>
    {identity && <ActionButton className='button-secondary' onClick={signOut}>退出当前设备登录</ActionButton>}
  </View>
}
