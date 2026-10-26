"""Three focused POC scenarios: durable acquisition, deferred fallback, and conservative parsing."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from bs4 import BeautifulSoup
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from youju.crawlers import CrawlerMode, CrawlError, CrawlErrorCode, CrawlResponse
from youju.database import create_sqlite_engine, initialize_database
from youju.models import (
    Capture,
    CrawlJob,
    FetchAttempt,
    Organization,
    ParseResult,
    Position,
    Resource,
    ResourceVersion,
    Seed,
    SeedKind,
    Source,
)
from youju.poc.ledger import Ledger
from youju.poc.parsing import parse_html, text
from youju.poc.queries import current_positions
from youju.poc.scope import DiscoveryScope
from youju.poc.storage import BlobStore
from youju.poc.worker import CrawlWorker

BASE = "https://jobs.example.org/cn/"
LIST = BASE + "indexnewslist.aspx?signtype=51"
NOTICE = BASE + "indexnews.aspx?sign=1"
OTHER = BASE + "indexnews.aspx?sign=2"
DOCUMENT = """<!doctype html><html><head><title>劳务派遣招聘公告</title></head><body>
<table><tr><td><div id="oInfoContent"><table><tr><th>岗位名称</th><th>学历</th>
<th>专业</th><th>招聘条件</th></tr><tr><td>管理助理（2人）</td><td>硕士及以上</td>
<td>财经类优先</td><td>具有服务意识</td></tr></table>
<p>示例人力资源有限公司与劳务派遣人员签订劳动合同，劳务派遣至示例大学财务处。</p>
<p>提交报名材料，截止期为2026年10月31日。</p><p>最晚到岗时间2026年12月31日。</p>
</div></td></tr></table></body></html>"""


class FakeCrawler:
    def __init__(self, pages, calls):
        self.pages, self.calls = pages, calls

    async def crawl(self, request):
        self.calls.append((request.mode.value, request.url))
        await asyncio.sleep(0)  # The worker must yield rather than run synchronous network work.
        body = self.pages[request.url]
        if isinstance(body, Exception):
            raise body
        return CrawlResponse(
            request=request,
            final_url=request.url,
            html=body,
            http_status=200,
            captured_at=datetime.now(UTC),
            response_body=body.encode() if request.mode is CrawlerMode.STATIC else None,
        )


@pytest.fixture
def setup(tmp_path):
    engine = create_sqlite_engine(tmp_path / "poc.sqlite3")
    initialize_database(engine)
    with Session(engine) as session, session.begin():
        source = Source(
            id="source",
            name="示例招聘渠道",
            base_url=BASE,
            publisher=Organization(id="org", name="示例大学"),
        )
        session.add(
            Seed(
                id="seed",
                name="招聘列表",
                kind=SeedKind.LISTING,
                source=source,
                resource=Resource(original_url=LIST, source=source),
            )
        )
    ledger = Ledger(engine)
    ledger.approve("source", "example.org", "Synthetic test approval only")
    ledger.enqueue("source")
    yield engine, ledger, tmp_path / "blobs"
    engine.dispose()


def test_async_loop_persists_positions_evidence_scope_and_cancellation(setup):
    engine, ledger, storage = setup
    listing = f'''<a href="{NOTICE}">招聘公告</a>
    <a href="https://department.example.org/jobs">同组织新站</a>
    <a href="https://example.org.evil.com/jobs">外部页面</a>
    <a href="https://notexample.org/jobs">相似域名</a>'''
    pages, calls = {LIST: listing, NOTICE: DOCUMENT}, []
    fake = FakeCrawler(pages, calls)
    worker = CrawlWorker(engine, storage, _crawlers={"static": fake, "dynamic": fake})
    result = asyncio.run(worker.run_once("source", 8))
    assert result["processed"] == 2
    positions = current_positions(engine, "source")
    assert len(positions) == 1
    position = positions[0]
    assert (position["facts"]["title"]["value"], position["facts"]["headcount"]["value"]) == (
        "管理助理",
        2,
    )
    assert position["facts"]["deadline_on"]["value"] == "2026-10-31"  # Not the arrival date.
    assert position["facts"]["employment_type"]["value"] == "labor_dispatch"
    assert position["facts"]["employer"]["value"] == "示例人力资源有限公司"
    assert position["review_status"] == "needs_review"
    with Session(engine) as session:
        urls = session.scalars(select(Resource.normalized_url)).all()
        assert all(DiscoveryScope("example.org").contains(url) for url in urls)
        candidate = session.scalar(
            select(Resource).where(Resource.normalized_url == "https://department.example.org/jobs")
        )
        assert candidate.source_id is None
        assert session.get(CrawlJob, candidate.id) is None
        version = session.scalar(
            select(ResourceVersion).where(
                ResourceVersion.content_sha256 == position["version_sha256"]
            )
        )
        soup = BeautifulSoup(BlobStore(storage).read(version.body_ref), "html.parser")
        for entries in position["evidence"].values():
            for item in entries:
                assert text(soup.select_one(item["locator"])) == item["text"]
    ledger.enqueue("source", revisit=True)
    asyncio.run(worker.run_once("source", 8))
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Position)) == 1
        assert session.scalar(select(func.count()).select_from(ResourceVersion)) == 2
    pages[NOTICE] = DOCUMENT.replace("劳务派遣招聘公告", "取消招聘公告")
    ledger.enqueue("source", revisit=True)
    asyncio.run(worker.run_once("source", 8))
    assert current_positions(engine, "source") == []
    with Session(engine) as session:
        assert (
            session.scalar(select(func.count()).select_from(Position)) == 1
        )  # History is preserved.


def test_static_failures_persist_dynamic_for_a_later_pass_and_recover_leases(setup):
    engine, ledger, storage = setup
    calls = []
    static = FakeCrawler(
        {
            LIST: f'<a href="{NOTICE}">一</a><a href="{OTHER}">二</a>',
            NOTICE: '<html><div id="oInfoContent"></div></html>',
            OTHER: CrawlError(CrawlErrorCode.NETWORK, "Synthetic network failure"),
        },
        calls,
    )
    dynamic = FakeCrawler({NOTICE: DOCUMENT, OTHER: DOCUMENT}, calls)
    adapters = {"static": static, "dynamic": dynamic}
    first = CrawlWorker(engine, storage, retry_delay=0, _crawlers=adapters)
    result = asyncio.run(first.run_once("source", 10))
    assert result["processed"] == 3
    assert all(mode == "static" for mode, _ in calls)
    assert current_positions(engine, "source") == []
    with Session(engine) as session:
        assert (
            len(
                session.scalars(
                    select(CrawlJob).where(CrawlJob.state == "ready", CrawlJob.mode == "dynamic")
                ).all()
            )
            == 2
        )
    # A new worker instance reads the persisted choice. There was no inline retry.
    second = CrawlWorker(engine, storage, retry_delay=0, _crawlers=adapters)
    result = asyncio.run(second.run_once("source", 10))
    assert result["dynamic"] == result["processed"] == 2
    assert len(current_positions(engine, "source")) == 2
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(FetchAttempt)) == 5
        assert session.scalar(select(func.count()).select_from(Capture)) == 4
    ledger.enqueue("source", revisit=True)
    old = ledger.claim("source", set(), 45)
    with Session(engine) as session, session.begin():
        session.get(CrawlJob, old.resource_id).lease_expires_at = datetime.now(UTC) - timedelta(
            seconds=1
        )
    new = ledger.claim("source", set(), 45)
    assert new.resource_id == old.resource_id and new.token != old.token
    assert ledger.fail(old, "late_result", 60)["state"] == "stale"


def test_unknown_conflicting_conditions_and_policy_denials_do_not_become_positions(setup):
    engine, _, storage = setup
    for suffix in ("cn", "edu.cn", "org"):
        with pytest.raises(ValueError):
            DiscoveryScope(suffix)
    html = DOCUMENT.replace("管理助理（2人）", "管理助理").replace(
        "</div>",
        "<p>报名截止日期2026年11月1日。</p></div>",
    )
    parsed = parse_html(html, NOTICE, DiscoveryScope("example.org"))
    assert parsed.positions[0]["headcount"] is None
    assert parsed.positions[0]["deadline_on"] is None
    assert "2026年11月1日" in parsed.positions[0]["deadline_text"]
    calls = []
    blocked = FakeCrawler(
        {LIST: CrawlError(CrawlErrorCode.POLICY_BLOCKED, "Synthetic robots denial")}, calls
    )
    worker = CrawlWorker(engine, storage, retry_delay=0, _crawlers={"static": blocked})
    asyncio.run(worker.run_once("source", 8))
    with Session(engine) as session:
        job = session.scalar(select(CrawlJob))
        assert job.state == "review" and job.mode == "static"
        assert session.scalar(select(func.count()).select_from(ParseResult)) == 0
