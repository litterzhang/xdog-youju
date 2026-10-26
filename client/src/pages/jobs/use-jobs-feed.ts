import { useEffect, useMemo, useSyncExternalStore } from 'react'
import type { JobsQuery } from '../../services/jobs'
import { JobsFeed } from './feed'

export function useJobsFeed(query: JobsQuery) {
  const feed = useMemo(() => new JobsFeed(), [])
  const state = useSyncExternalStore(feed.subscribe, feed.snapshot, feed.snapshot)
  useEffect(() => {
    void feed.refresh(query)
    return feed.cancel
  }, [feed, query])
  return { ...state, refresh: () => feed.refresh(query), loadMore: () => feed.loadMore() }
}
