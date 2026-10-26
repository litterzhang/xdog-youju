import { request } from './api'
import type { RegistryPage } from './contracts'
export async function registryOptions<T>(path: string): Promise<T[]> {
  const items: T[] = []
  for (let page = 1; page <= 10000; page++) {
    const result = await request<RegistryPage<T>>(`${path}?page=${page}`)
    items.push(...result.items)
    if (items.length >= result.total || result.items.length === 0) return items
  }
  throw new Error('登记列表过大，请联系管理员。')
}
