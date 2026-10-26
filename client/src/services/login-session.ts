import Taro from '@tarojs/taro'
import { Identity, parseIdentity } from './contracts'
import { send } from './transport'
import type { VerifiedSession } from './auth-recovery'

export async function createVerifiedSession(username?: string, password?: string): Promise<VerifiedSession & { identity: Identity }> {
  const weapp = process.env.TARO_ENV === 'weapp'
  const payload = weapp ? { code: (await Taro.login()).code } : { username, password }
  if (weapp && !payload.code) throw new Error('无法获取微信登录凭据，请重试。')
  const response = await send<{ access_token: unknown }>(weapp ? '/auth/wechat' : '/auth/login', payload)
  const token = response.access_token
  if (typeof token !== 'string' || !token || token.length > 8192 || /\s/.test(token)) throw new Error('登录服务未返回有效令牌。')
  const identity = parseIdentity(await send('/auth/me', undefined, token))
  return { token, userId: identity.id, identity }
}
