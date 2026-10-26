import Taro from '@tarojs/taro'
import type { JobsHttpResponse } from './jobs'

/** Keep the static platform import visible to Taro's API build transform. */
export async function sendJobs(url: string): Promise<JobsHttpResponse> {
  return Taro.request<unknown>({ url, method: 'GET', timeout: 10000 })
}
