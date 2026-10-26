export const tabs = [
  { pagePath: 'pages/index/index', text: '首页', icon: 'home' },
  { pagePath: 'pages/jobs/index', text: '岗位', icon: 'jobs' },
  { pagePath: 'pages/me/index', text: '我的', icon: 'me' },
] as const
export const flowSteps = ['组织', '来源', '核验', '进度'] as const
export function sourceRoute(source: { id: string; status: string; onboarding: { stage: string } }): string {
  const page = source.status === 'candidate' || source.onboarding.stage === 'not_started' ? 'review' : 'status'
  return `/pages/admin/${page}/index?id=${encodeURIComponent(source.id)}`
}
export function displayTime(stamp: string | undefined | null): string {
  if (!stamp || !Number.isFinite(Date.parse(stamp))) return '暂无记录'
  const day = new Date(Date.parse(stamp) + 8 * 3600000)
  const pad = (v: number) => String(v).padStart(2, '0')
  return `${day.getUTCFullYear()}/${pad(day.getUTCMonth() + 1)}/${pad(day.getUTCDate())} ${pad(day.getUTCHours())}:${pad(day.getUTCMinutes())}`
}
