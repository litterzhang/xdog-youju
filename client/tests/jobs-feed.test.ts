import assert from 'node:assert/strict'
import test from 'node:test'
import { JobsFeed } from '../src/pages/jobs/feed'
import type { Job, JobsPage } from '../src/services/jobs'

function page(number: number, ids: string[], more = false): JobsPage {
  return { page: number, page_size: 20, items: ids.map(id => ({id} as Job)), total: 50,
    has_next: more, include_partial: true, evaluated_on:'2026-10-26',timezone:'Asia/Shanghai',
    filters:{education:'all',employment:'all'} }
}
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<T>((yes,no) => {resolve=yes;reject=no})
  return {promise,resolve,reject}
}

test('appends and deduplicates mobile pages; concurrent bottom events send one request', async () => {
  const next = deferred<JobsPage>(); const calls: number[] = []
  const feed = new JobsFeed(async query => {calls.push(query.page!);return query.page===1?page(1,['one','two'],true):next.promise})
  await feed.refresh({q:'教师'})
  const first=feed.loadMore(); await feed.loadMore()
  assert.deepEqual(calls,[1,2]); next.resolve(page(2,['two','three']))
  await first
  assert.deepEqual(feed.snapshot().items.map(job=>job.id),['one','two','three'])
  await feed.loadMore(); assert.deepEqual(calls,[1,2])
})

test('stale filter/refresh responses never replace a newer result or append old rows', async () => {
  const old=deferred<JobsPage>(); const more=deferred<JobsPage>()
  const feed=new JobsFeed(async query=>query.q==='old'?old.promise:query.page===2?more.promise:page(1,[query.q || ''],true))
  const pending=feed.refresh({q:'old'}); await feed.refresh({q:'new'})
  old.resolve(page(1,['old']));await pending
  assert.deepEqual(feed.snapshot().items.map(job=>job.id),['new'])
  const next=feed.loadMore();await feed.refresh({q:'replacement'})
  more.resolve(page(2,['stale']));await next
  assert.deepEqual(feed.snapshot().items.map(job=>job.id),['replacement'])
})

test('load failure keeps rows and retries the same page; refresh replaces accumulated rows', async () => {
  let fail=true;const calls:number[]=[]
  const feed=new JobsFeed(async query=>{
    calls.push(query.page!)
    if(query.page===2&&fail)throw new Error('临时断网')
    return query.page===1?page(1,['one'],true):page(2,['two'])
  })
  await feed.refresh({});await feed.loadMore()
  assert.equal(feed.snapshot().moreError,'临时断网')
  assert.deepEqual(feed.snapshot().items.map(job=>job.id),['one'])
  fail=false;await feed.loadMore();assert.deepEqual(calls,[1,2,2])
  await feed.refresh({});assert.deepEqual(feed.snapshot().items.map(job=>job.id),['one'])
})

test('wrong transport page and disposed responses cannot corrupt feed state', async () => {
  const feed=new JobsFeed(async()=>page(2,['bad']))
  await feed.refresh({});assert.match(feed.snapshot().error,/分页不一致/)
  assert.equal(feed.snapshot().result,null)
  const pending=deferred<JobsPage>();const disposed=new JobsFeed(()=>pending.promise)
  const request=disposed.refresh({});disposed.cancel();pending.resolve(page(1,['late']));await request
  assert.deepEqual(disposed.snapshot().items,[])
})
