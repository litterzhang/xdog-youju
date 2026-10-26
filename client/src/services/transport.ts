import Taro from '@tarojs/taro'
import { authenticatedUrl } from './auth-contract'
import { ApiError } from './api-error'

/** One bounded HTTP attempt; never clears sessions or recursively logs in. */
export async function send<T>(path: string, data?: Record<string, unknown>, token?: string): Promise<T> {
  const base = process.env.TARO_APP_API_BASE_URL || ''
  const platform = process.env.TARO_ENV || 'h5'
  const pageOrigin = platform === 'h5' && typeof window !== 'undefined' ? window.location.origin : undefined
  const url = authenticatedUrl(base, platform, path, pageOrigin)
  let response: { statusCode: number; data: unknown }
  try {
    response = await Taro.request({
      url, method: data === undefined ? 'GET' : 'POST', data, timeout: 15000,
      header: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    })
  } catch { throw new Error('暂时连接不上服务，请检查网络后重试。') }
  if (response.statusCode < 200 || response.statusCode >= 300) {
    const body = response.data as { error?: { message?: unknown } } | null
    throw new ApiError(response.statusCode, typeof body?.error?.message === 'string' ? body.error.message : '请求未完成，请重试。')
  }
  return response.data as T
}
