import { request } from './api'
import { Identity, parseIdentity } from './contracts'
import { createVerifiedSession } from './login-session'
import { getSession, loginGeneration, rememberIdentity, reserveLogin, saveSession } from './session-store'

export async function getIdentity(): Promise<Identity | null> {
  const session = getSession()
  if (!session) return null
  const identity = parseIdentity(await request('/auth/me', undefined, session.token))
  // Only a successful response for this exact stored session can migrate its legacy subject.
  rememberIdentity(session, identity.id)
  return identity
}
export async function signIn(username: string, password: string): Promise<Identity> {
  const generation = reserveLogin()
  const session = await createVerifiedSession(username, password)
  if (loginGeneration() !== generation) throw new Error('登录状态已变化，请重新操作。')
  saveSession(session)
  return session.identity
}
