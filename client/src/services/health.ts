import Taro from '@tarojs/taro'
import { healthUrl, parseHealthResponse } from './health-contract'

export async function checkHealth() {
  const url = healthUrl(process.env.TARO_APP_API_BASE_URL || '', process.env.TARO_ENV || '')
  const response = await Taro.request<unknown>({ url, method: 'GET', timeout: 8000 })
  if (response.statusCode !== 200) throw new Error('服务暂时不可用，请稍后重试')
  return parseHealthResponse(response.data)
}
