import { healthUrl } from './health-contract'

export function authenticatedUrl(base: string, platform: string, path: string, pageOrigin?: string): string {
  const normalized = base.trim().replace(/\/+$/, '')
  const origin = normalized || pageOrigin || ''
  if (!origin || (!origin.startsWith('https://') && !/^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(origin))) {
    throw new Error('登录和管理操作需要 HTTPS 服务地址；仅本机开发允许 HTTP。')
  }
  // Only the exact job-feedback endpoint is authenticated; public job queries stay separate.
  const jobFeedback = /^\/jobs\/[A-Za-z0-9_-]{1,64}\/feedback$(?![\s\S])/.test(path)
  if (!/^\/(auth|admin)\//.test(path) && !jobFeedback) throw new Error('请求路径无效。')
  return healthUrl(normalized, platform).replace(/\/health$/, path)
}
