import { getJobs } from '../../services/jobs'
import type { Job, JobsPage, JobsQuery } from '../../services/jobs'

export interface JobsFeedState {
  result: JobsPage | null
  items: Job[]
  loading: boolean
  loadingMore: boolean
  error: string
  moreError: string
}
export type JobsReader = (query: JobsQuery) => Promise<JobsPage>
const message = (error: unknown) => error instanceof Error ? error.message : '读取岗位失败，请重试。'

/** Page numbers are transport details; the view is a single accumulating mobile list. */
export class JobsFeed {
  private state: JobsFeedState = { result: null, items: [], loading: true, loadingMore: false, error: '', moreError: '' }
  private listeners = new Set<() => void>()
  private generation = 0
  private busy = false
  private query: JobsQuery = {}
  private key = ''
  constructor(private read: JobsReader = getJobs) {}
  snapshot = (): JobsFeedState => this.state
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  cancel = () => { this.generation++; this.busy = false }
  private publish(change: Partial<JobsFeedState>) {
    this.state = { ...this.state, ...change }
    this.listeners.forEach(listener => listener())
  }
  private async page(query: JobsQuery, page: number) {
    const result = await this.read({ ...query, page })
    if (result.page !== page || result.page_size !== (query.pageSize ?? 20)) throw new Error('岗位服务返回的分页不一致，请刷新后重试。')
    return result
  }
  async refresh(query: JobsQuery) {
    const generation = ++this.generation
    const normalized = { ...query, page: 1 }
    const key = JSON.stringify(normalized)
    const previous = key === this.key ? this.state.result : null
    this.key = key; this.query = normalized; this.busy = true
    this.publish({ result: previous, items: previous ? this.state.items : [], loading: true, loadingMore: false, error: '', moreError: '' })
    try {
      const result = await this.page(normalized, 1)
      if (generation === this.generation) this.publish({ result, items: result.items })
    } catch (error) {
      if (generation === this.generation) this.publish({ error: message(error) })
    } finally {
      if (generation === this.generation) { this.busy = false; this.publish({ loading: false }) }
    }
  }
  async loadMore() {
    const previous = this.state.result
    if (this.busy || !previous?.has_next) return
    const generation = this.generation
    this.busy = true; this.publish({ loadingMore: true, moreError: '' })
    try {
      const next = await this.page(this.query, previous.page + 1)
      if (generation !== this.generation) return
      const items = new Map(this.state.items.map(item => [item.id, item]))
      next.items.forEach(item => items.set(item.id, item))
      this.publish({ result: next, items: [...items.values()] })
    } catch (error) {
      if (generation === this.generation) this.publish({ moreError: message(error) })
    } finally {
      if (generation === this.generation) { this.busy = false; this.publish({ loadingMore: false }) }
    }
  }
}
