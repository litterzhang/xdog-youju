import type { RecoveryOptions } from './contracts'

export function resumeLimits(recovery: RecoveryOptions): Record<string, number> {
  const limits: Record<string, number> = {}
  for (const key of ['max_depth', 'max_resources'] as const) {
    const value = recovery.suggested_limits[key]
    if (value === undefined) continue
    const maximum = key === 'max_depth' ? 100 : 10000
    if (!Number.isInteger(value) || value < recovery.limits[key] || value > maximum) {
      throw new Error('恢复参数无效，请刷新状态后重试。')
    }
    limits[key] = value
  }
  return limits
}
export function blockedExplanation(code?: string, recovery?: RecoveryOptions): string {
  if (recovery?.blocking_limits.includes('depth_limit')) {
    return `发现了超过当前深度 ${recovery.limits.max_depth} 的链接，${recovery.depth_limited_resources} 个资源尚未采集。不能将当前结果视为完整扫描。`
  }
  if (recovery?.blocking_limits.includes('resource_limit')) return '已达到本来源的资源数量上限，仍有发现的链接未被纳入采集。'
  if (recovery?.blocking_limits.includes('discovery_link_limit')) return '单份材料的链接数量超过安全限制，需要检查发现策略；不能直接绕过限制。'
  const explanations: Record<string, string> = {
    AUDIT_NOT_READY: '完整审计未通过。请检查解析错误与材料警告；只有满足审计要求后才能开启增量监测。',
    SOURCE_EXECUTION_FAILURE: '解析脚本、工作流或模型执行存在错误，需要依据审计反馈修复并重新审计。',
    MODEL_DISABLED: '后台尚未启用所需模型，需要管理员检查服务配置后再重试。',
    NO_ANNOUNCEMENT_SAMPLES: '还没有适合生成解析脚本的招聘公告样本，需要检查来源入口或页面分类。',
    RECIPE_UPGRADE_REQUIRED: '现有解析脚本尚不兼容新契约；增量已暂停，需重新生成并审查 Recipe 后恢复。',
    RECIPE_REGENERATION_REQUIRED: '数据契约已升级，旧岗位和解析版本已归档。已保存网页与附件会复用；继续后将重新分类、生成脚本并完成审查。',
    INIT_FAILED: 'Recipe 服务的初始化未成功，后续生成任务没有启动。',
    WORKER_INTERRUPTED: '后台任务被中断，已有资料与会话检查点已保留；重试会提交新的任务。',
  }
  return explanations[code || ''] || '后台任务已暂停。请核对错误码与执行摘要；恢复不会跳过来源核验、解析验证或审计。'
}
export function resumeConfirmation(recovery: RecoveryOptions): string {
  const limits = resumeLimits(recovery)
  const updates = Object.entries(limits).map(([key, value]) => `${key === 'max_depth' ? '深度上限' : '资源上限'}提高到 ${value}`).join('，')
  return `${updates ? updates + '。' : ''}复用已保存资料，重新排队待处理任务；不会强制重下已完成资源。后续分析可能产生模型费用，是否继续？`
}
