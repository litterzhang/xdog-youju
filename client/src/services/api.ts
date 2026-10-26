import { AuthRecovery } from './auth-recovery'
import { createVerifiedSession } from './login-session'
import { getSession, getToken, logout, saveSession } from './session-store'
import { send } from './transport'

export { ApiError } from './api-error'
export { getToken, logout, setToken } from './session-store'
const recovery = new AuthRecovery({
  send, current: getSession, save: saveSession, clear: logout,
  login: createVerifiedSession, canRecover: () => process.env.TARO_ENV === 'weapp',
})
export async function request<T>(path: string, data?: Record<string, unknown>, token = getToken()): Promise<T> {
  const session = getSession()
  if (token && session?.token === token) return recovery.request<T>(session, path, data)
  return send<T>(path, data, token)
}
