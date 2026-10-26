import type { Incremental, IncrementalDetails, IncrementalRun, IncrementalRunDetail, IncrementalNotice, Onboarding } from './contracts'

export const onboardingStages = ['inventory', 'classification', 'authoring', 'audit', 'completed'] as const
export const onboardingStageNames = ['全站发现与材料采集', '页面类型判断', '解析脚本与工作流', '全量公告审计', '接入完成'] as const
export function onboardingCompleted(onboarding: Pick<Onboarding, 'stage' | 'state'>): boolean {
  return onboarding.stage === 'completed' && onboarding.state === 'completed'
}
export function onboardingRecoveryTask(onboarding: Onboarding) {
  const completed = onboardingCompleted(onboarding)
  const maintenanceBlocked = onboarding.maintenance?.state === 'blocked'
  return {
    blockedTask: maintenanceBlocked || (!completed && onboarding.state === 'blocked'),
    label: maintenanceBlocked ? '维护处理' : '阻塞处理',
    errorCode: maintenanceBlocked ? onboarding.maintenance?.error?.code ?? undefined : onboarding.summary.error?.code,
    resumeTitle: maintenanceBlocked ? '恢复维护任务' : '恢复接入任务',
  }
}
export function incrementalStateLabel(state: string): string {
  return ({ disabled: '未开启', idle: '等待下一批次', running: '批次处理中', blocked: '已阻塞，需处理' } as Record<string, string>)[state] || '未知状态'
}
export function incrementalControlLabel(incremental: Incremental): string {
  return !incremental.enabled && incremental.latest_run?.status === 'running' ? '已暂停，重新开启后续跑' : incrementalStateLabel(incremental.state)
}
export function incrementalRunLabel(status: string, enabled = true): string {
  if (status === 'running' && !enabled) return '已暂停，重新开启后续跑'
  return ({ queued: '已排队', pending: '等待处理', running: '处理中', complete: '已完成', completed: '已完成',
    success: '已完成', succeeded: '已完成', failed: '失败', blocked: '已阻塞', cancelled: '已取消' } as Record<string, string>)[status] || `待核验（${status}）`
}
export function incrementalNavigationDetail(incremental?: Incremental): string {
  if (!incremental) return '默认未开启 · 与接入摘要分开记录'
  return `${incremental.enabled ? '已开启' : '已关闭'} · ${incrementalControlLabel(incremental)}`
}
export function incrementalRoute(id: string): string {
  return `/pages/admin/incremental/index?id=${encodeURIComponent(id)}`
}
export function incrementalConfirmation(enabled: boolean): { title: string; content: string } {
  return enabled ? {
    title: '开启增量抓取',
    content: '仅在接入完成后开启。后台将抓取新增或变化的材料，可能产生模型费用；重新开启会清除阻塞状态并保留历史记录，不改变已冻结的接入摘要。是否继续？',
  } : {
    title: '关闭增量抓取',
    content: '关闭后暂停所有后续有界处理轮次，包括同一运行周期内的后续小批次。当前已开始的小批次会安全结束。历史 running 记录保留，已暂停，重新开启后续跑，不代表取消。接入摘要与增量历史记录会保留。是否继续？',
  }
}
export function incrementalDeltaText(delta: IncrementalRun['summary']['delta']): string {
  if (delta.state === 'unavailable') return '本批次的材料对比或审查快照不可用，不能据此判断没有增量。'
  if (delta.state === 'pending') return '批次尚在处理中，以下仅展示已得到的增量结果；不是完整结果。'
  return delta.notice_count ? '仅含本轮新增或材料变化的公告及关联岗位，不含未变化公告。' : '本批次未发现新增或材料变化的公告。'
}
// The existing authenticated request adapter is injected so offline tests never load platform APIs.
type IncrementalRequest = <T>(path: string, data?: Record<string, unknown>) => Promise<T>
export function createIncrementalClient(request: IncrementalRequest) {
  function path(id: string): string {
    if (!id.trim()) throw new Error('缺少来源标识，请从来源管理重新进入。')
    return `/admin/sources/${encodeURIComponent(id)}/incremental`
  }
  return {
    read(id: string): Promise<IncrementalDetails> { return request<IncrementalDetails>(path(id)) },
    run(id: string, runId: string, page = 1): Promise<IncrementalRunDetail> {
      if (!/^[A-Za-z0-9_-]{1,64}$/.test(runId) || !Number.isInteger(page) || page < 1 || page > 10000) throw new Error('批次或分页标识无效，请重新选择。')
      return request<IncrementalRunDetail>(`${path(id)}/runs/${encodeURIComponent(runId)}?page=${page}`)
    },
    setEnabled(id: string, enabled: boolean, onboarding: Pick<Onboarding, 'stage' | 'state'>): Promise<IncrementalDetails> {
      if (enabled && !onboardingCompleted(onboarding)) throw new Error('接入尚未完成，不能开启增量抓取。')
      return request<IncrementalDetails>(path(id), { enabled })
    },
  }
}

/** Execution cost never establishes notice expiry or position-fact quality. */
export function incrementalNoticeParseLabel(notice: Pick<IncrementalNotice, 'extraction_path' | 'status' | 'position_count'>): string {
  if (notice.status === 'cancelled') return '公告取消关联已确认'
  const path = notice.extraction_path === 'quick' ? '低成本路径' : notice.extraction_path === 'detailed' ? '详细路径' : '路径待核验'
  if (notice.status === 'success' || notice.status === 'partial') {
    if (!notice.position_count && notice.extraction_path === 'quick') return `${path} · 归档无岗位事实（非过期确认）`
    return `${path} · ${notice.status === 'success' ? '完整解析' : '部分材料解析'}`
  }
  return `${path} · ${notice.status === 'failed' ? '解析失败' : '解析待复核'}`
}
