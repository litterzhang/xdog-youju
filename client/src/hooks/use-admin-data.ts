import { useCallback, useRef, useState } from 'react'
import { useDidHide, useDidShow } from '@tarojs/taro'
import { canManageSources } from '../services/contracts'
import { getIdentity } from '../services/session'
import { ApiError } from '../services/api'

/** All admin pages verify current server permissions; stale hidden-page loads are ignored. */
export function useAdminData<T>(loader: () => Promise<T>) {
  const currentLoader = useRef(loader)
  currentLoader.current = loader
  const epoch = useRef(0)
  const visible = useRef(false)
  const mutation = useRef(false)
  const [data, setData] = useState<T | null>(null)
  const [allowed, setAllowed] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const reload = useCallback(async () => {
    const attempt = ++epoch.current
    setLoading(true); setError('')
    try {
      const identity = await getIdentity()
      if (!canManageSources(identity)) throw new ApiError(identity ? 403 : 401, identity ? '此账号没有来源管理权限。' : '请先登录已授权账号。')
      if (!visible.current || epoch.current !== attempt) return
      setAllowed(true)
      const value = await currentLoader.current()
      if (visible.current && epoch.current === attempt) { setData(value); setAllowed(true) }
    } catch (reason) {
      if (visible.current && epoch.current === attempt) {
        if (reason instanceof ApiError && [401, 403].includes(reason.status)) { setAllowed(false); setData(null) }
        setError(reason instanceof Error ? reason.message : '暂时无法读取数据，请重试。')
      }
    } finally { if (epoch.current === attempt) setLoading(false) }
  }, [])
  useDidShow(() => { visible.current = true; setAllowed(false); setData(null); void reload() })
  useDidHide(() => { visible.current = false; epoch.current++; setAllowed(false); setData(null) })
  const mutate = async (action: () => Promise<void | boolean>) => {
    if (mutation.current || !allowed) return
    mutation.current = true; setBusy(true); setError(''); setNotice('')
    try {
      if (await action() === false) return
      if (visible.current) { await reload(); setNotice('已保存。') }
    } catch (reason) {
      if (visible.current) {
        if (reason instanceof ApiError && [401, 403].includes(reason.status)) { setAllowed(false); setData(null) }
        setError(reason instanceof Error ? reason.message : '操作未完成，请重试。')
      }
    } finally { mutation.current = false; setBusy(false) }
  }
  return { data, allowed, loading, busy, error, notice, reload, mutate }
}
