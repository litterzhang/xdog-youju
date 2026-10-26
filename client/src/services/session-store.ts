import Taro from '@tarojs/taro'
import { ApiError } from './api-error'
import type { SessionSnapshot, VerifiedSession } from './auth-recovery'

const key = 'youju.session.v1'
const legacyKey = 'youju.access-token.v1'
let generation = 0
function validToken(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= 8192 && !/\s/.test(value)
}
function validSubject(value: unknown): value is string {
  return typeof value === 'string' && !!value.trim() && value.length <= 256
}
export function getSession(): SessionSnapshot | null {
  const value: unknown = Taro.getStorageSync(key)
  if (value && typeof value === 'object') {
    const item = value as Record<string, unknown>
    if (validToken(item.token) && validSubject(item.userId)) return { token: item.token, userId: item.userId, generation }
  }
  const legacy: unknown = Taro.getStorageSync(legacyKey)
  return validToken(legacy) ? { token: legacy, generation } : null
}
export function saveSession(value: VerifiedSession): SessionSnapshot {
  if (!validToken(value.token) || !validSubject(value.userId)) throw new Error('登录身份响应无效。')
  Taro.setStorageSync(key, { token: value.token, userId: value.userId })
  Taro.removeStorageSync(legacyKey)
  generation++
  return { token: value.token, userId: value.userId, generation }
}
export function logout(): void {
  generation++
  Taro.removeStorageSync(key); Taro.removeStorageSync(legacyKey)
}
export function getToken(): string | undefined { return getSession()?.token }
export function setToken(token: string): void {
  if (!validToken(token)) throw new Error('登录令牌无效。')
  generation++; Taro.removeStorageSync(key); Taro.setStorageSync(legacyKey, token)
}
export function reserveLogin(): number { return ++generation }
export function loginGeneration(): number { return generation }
export function rememberIdentity(snapshot: SessionSnapshot, userId: string): void {
  const current = getSession()
  if (!current || current.token !== snapshot.token || current.generation !== snapshot.generation) return
  if (snapshot.userId && snapshot.userId !== userId) {
    logout(); throw new ApiError(401, '登录身份已变化，请重新登录。')
  }
  if (!snapshot.userId) saveSession({ token: snapshot.token, userId })
}
