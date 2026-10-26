import { Input, Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useState } from 'react'
import { ActionButton, Card, Feedback, Field } from '../../components/ui'
import { signIn } from '../../services/session'

export default function Login() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const weapp = process.env.TARO_ENV === 'weapp'
  async function submit() {
    if (busy) return
    setBusy(true); setError('')
    try {
      await signIn(username, password); setPassword('')
      await Taro.switchTab({ url: '/pages/me/index' })
    } catch (reason) { setError(reason instanceof Error ? reason.message : '登录未完成，请重试。') }
    finally { setBusy(false) }
  }
  return <View className='screen'>
    <Feedback error={error} />
    <Card className='form-card'>{weapp ? <View className='login-intro'><View className='account-mark'><View className='person-glyph' /></View>
      <Text className='paragraph'>使用微信临时登录凭据核验账号身份。</Text><Text className='caption'>管理功能只对服务端已授权账号开放。</Text></View> : <>
      <Field label='账号'><Input className='input-control' value={username} maxlength={128} placeholder='zdog-auth 账号' onInput={e => setUsername(e.detail.value)} disabled={busy} /></Field>
      <Field label='密码'><Input className='input-control' value={password} maxlength={256} password placeholder='请输入密码' onInput={e => setPassword(e.detail.value)} disabled={busy} /></Field>
    </>}</Card>
    <ActionButton className='button-primary' onClick={submit} loading={busy} disabled={busy || (!weapp && (!username.trim() || !password))}>{weapp ? '微信登录' : '登录'}</ActionButton>
    <ActionButton className='button-text' disabled={busy} onClick={() => Taro.switchTab({ url: '/pages/me/index' })}>暂不登录</ActionButton>
    <Text className='footnote'>使用 zdog-auth 账号登录</Text>
  </View>
}
