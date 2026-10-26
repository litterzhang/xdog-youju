import { ApiError } from './api-error'

export interface VerifiedSession { token: string; userId: string }
export interface SessionSnapshot { token: string; userId?: string; generation: number }
interface Dependencies {
  send: <T>(path: string, data: Record<string, unknown> | undefined, token: string) => Promise<T>
  current: () => SessionSnapshot | null
  save: (value: VerifiedSession) => SessionSnapshot
  clear: () => void
  login: () => Promise<VerifiedSession>
  canRecover: () => boolean
}

/** Same-identity, single-flight 401 recovery, following Xiaoman's transport boundary. */
export class AuthRecovery {
  private recovery?: { token: string; generation: number; promise: Promise<SessionSnapshot> }
  constructor(private readonly dependencies: Dependencies) {}
  private matches(session: SessionSnapshot): boolean {
    const current = this.dependencies.current()
    return current?.token === session.token && current.generation === session.generation
  }
  private async recover(session: SessionSnapshot): Promise<SessionSnapshot> {
    if (this.recovery?.token === session.token && this.recovery.generation === session.generation) return this.recovery.promise
    if (!this.matches(session)) throw new Error('登录状态已变化，请重新操作。')
    if (!this.dependencies.canRecover() || !session.userId) {
      this.dependencies.clear()
      throw new ApiError(401, '登录已过期，请重新登录。')
    }
    const promise = (async () => {
      try {
        const renewed = await this.dependencies.login()
        if (!this.matches(session)) throw new Error('登录状态已变化，请重新操作。')
        if (renewed.userId !== session.userId) throw new ApiError(401, '登录身份已变化，请重新登录。')
        return this.dependencies.save(renewed)
      } catch (error) {
        if (error instanceof ApiError && error.status === 401 && this.matches(session)) this.dependencies.clear()
        throw error
      }
    })()
    const recovery = { token: session.token, generation: session.generation, promise }
    this.recovery = recovery
    // Keep one successful receipt for late 401s; temporary failures can recover later.
    void promise.catch(() => { if (this.recovery === recovery) this.recovery = undefined })
    return promise
  }
  async request<T>(session: SessionSnapshot, path: string, data?: Record<string, unknown>): Promise<T> {
    let active = session
    try {
      if (!this.matches(session)) {
        if (this.recovery?.token !== session.token || this.recovery.generation !== session.generation) throw new Error('登录状态已变化，请重新操作。')
        active = await this.recovery.promise
        if (!this.matches(active)) throw new Error('登录状态已变化，请重新操作。')
      }
      let value: T
      try {
        value = await this.dependencies.send<T>(path, data, active.token)
      } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 401) throw error
        active = await this.recover(session)
        if (!this.matches(active)) throw new Error('登录状态已变化，请重新操作。')
        value = await this.dependencies.send<T>(path, data, active.token)
      }
      if (!this.matches(active)) throw new Error('登录状态已变化，请重新操作。')
      return value
    } catch (error) {
      if (error instanceof ApiError && error.status === 401 && this.matches(active)) this.dependencies.clear()
      throw error
    }
  }
}
