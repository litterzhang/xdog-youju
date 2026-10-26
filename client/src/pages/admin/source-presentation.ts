import type { Source } from '../../services/contracts'
import { stageLabel } from '../../services/contracts'
import { onboardingCompleted } from '../../services/incremental'

export function sourceListState(source: Source): { label: string; detail: string; attention: boolean } {
  const onboarding = source.onboarding
  const maintenanceBlocked = onboarding.maintenance?.state === 'blocked'
  const completed = onboardingCompleted(onboarding)
  const detail = completed
    ? source.incremental?.enabled ? '增量已开启' : '增量未开启'
    : stageLabel(onboarding.stage)
  if (source.status === 'paused' || source.status === 'retired') return { label: source.status === 'paused' ? '已暂停' : '已停用', detail, attention: false }
  if (source.status === 'candidate') return { label: '待核验', detail: '核验官方依据后接入', attention: true }
  if (maintenanceBlocked) return { label: '维护受阻', detail, attention: true }
  if (!completed && onboarding.state === 'blocked') return { label: '接入受阻', detail, attention: true }
  if (source.incremental?.state === 'blocked') return { label: '增量受阻', detail, attention: true }
  if (completed) return { label: '已接入', detail, attention: false }
  return { label: onboarding.state === 'running' ? '接入中' : '待推进', detail, attention: false }
}

export function sourceHost(url: string): string {
  // A registry URL is rendered literally, never used as a client navigation target.
  return /^https?:\/\/([^/?#]+)/i.exec(url)?.[1] || '来源地址待确认'
}
