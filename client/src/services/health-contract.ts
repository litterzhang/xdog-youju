export interface HealthResponse {
  service: 'xdog-youju'
  status: 'ok'
  data_status: 'not_initialized'
}

export function parseHealthResponse(value: unknown): HealthResponse {
  if (typeof value !== 'object' || value === null) {
    throw new Error('服务响应格式无效')
  }
  const data = value as Record<string, unknown>
  if (data.service !== 'xdog-youju' || data.status !== 'ok' ||
      data.data_status !== 'not_initialized') {
    throw new Error('服务响应格式无效')
  }
  return { service: data.service, status: data.status, data_status: data.data_status }
}

export function healthUrl(base: string, platform: string): string {
  const normalized = base.trim().replace(/\/+$/, '')
  if (!normalized && platform === 'h5') return '/api/health'
  if (!/^https?:\/\/[^/\s?#]+(?:\/[^\s?#]*)?$/.test(normalized)) {
    throw new Error('请配置有效的 API 服务地址')
  }
  if (platform !== 'h5' && !normalized.startsWith('https://')) {
    throw new Error('小程序需要配置 HTTPS API 服务地址')
  }
  return `${normalized}/api/health`
}
