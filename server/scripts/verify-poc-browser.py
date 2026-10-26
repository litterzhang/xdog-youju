"""Opt-in POC smoke: real headless Chromium, synthetic HTTP fixtures, no external requests."""

import asyncio
import socket
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from youju.crawlers import DynamicCrawler, OriginPolicy, StaticCrawler
from youju.database import create_sqlite_engine, initialize_database
from youju.models import Capture, CrawlJob, Organization, Resource, Seed, SeedKind, Source
from youju.poc.ledger import Ledger
from youju.poc.queries import current_positions
from youju.poc.worker import CrawlWorker

HOST = "jobs.example.org"
URL = f"https://{HOST}/cn/indexnews.aspx?sign=1"
HTML = b"""<!doctype html><html><head><meta charset="utf-8"></head>
<body><div id="oInfoContent"></div><script src="/positions.js"></script></body></html>"""
SCRIPT = """setTimeout(() => {
  document.querySelector('#oInfoContent').innerHTML =
    '<table><tr><th>岗位名称</th><th>学历</th></tr>' +
    '<tr><td>科研助理（1人）</td><td>硕士及以上</td></tr></table>';
}, 100);""".encode()


def resolve(host, port, *args, **kwargs):
    if host != HOST:
        raise AssertionError(f"Unexpected DNS request: {host}")
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


async def fixture(request):
    if request.url.path == "/robots.txt":
        return httpx.Response(404, stream=httpx.ByteStream(b""))
    if request.url.path == "/cn/indexnews.aspx":
        content, media_type = HTML, "text/html; charset=utf-8"
    elif request.url.path == "/positions.js":
        content, media_type = SCRIPT, "text/javascript; charset=utf-8"
    else:
        raise AssertionError(f"Unexpected HTTP fixture request: {request.url}")
    return httpx.Response(
        200, headers={"content-type": media_type}, stream=httpx.ByteStream(content)
    )


async def main():
    with TemporaryDirectory(prefix="youju-poc-browser-") as directory:
        root = Path(directory)
        engine = create_sqlite_engine(root / "poc.sqlite3")
        initialize_database(engine)
        try:
            with Session(engine) as session, session.begin():
                source = Source(
                    id="fixture",
                    name="动态招聘测试",
                    base_url=f"https://{HOST}/",
                    publisher=Organization(id="org", name="测试机构"),
                )
                session.add(
                    Seed(
                        id="seed",
                        name="岗位公告",
                        source=source,
                        kind=SeedKind.LISTING,
                        resource=Resource(original_url=URL, source=source),
                    )
                )
            ledger = Ledger(engine)
            ledger.approve("fixture", "example.org", "Synthetic browser smoke fixture only")
            ledger.enqueue("fixture")
            policy = OriginPolicy(frozenset({f"https://{HOST}"}))
            transport = httpx.MockTransport(fixture)
            adapters = {
                "static": StaticCrawler(policy, min_interval_seconds=0.01, _transport=transport),
                "dynamic": DynamicCrawler(policy, min_interval_seconds=0.01, _transport=transport),
            }
            with patch("socket.getaddrinfo", resolve):
                worker = CrawlWorker(engine, root / "blobs", retry_delay=0, _crawlers=adapters)
                first = await worker.run_once("fixture", 4)
                assert first["processed"] == 1 and first.get("dynamic", 0) == 0
                with Session(engine) as session:
                    job = session.scalar(select(CrawlJob))
                    assert job.state == "ready" and job.mode == "dynamic"
                second = await worker.run_once("fixture", 4)
            positions = current_positions(engine, "fixture")
            if second.get("dynamic") != 1 or len(positions) != 1:
                print("Unexpected worker result:", first, second, positions)
                with Session(engine) as session:
                    for job in session.scalars(select(CrawlJob)):
                        print("Job:", job.mode, job.state, job.last_error)
            assert second["dynamic"] == 1 and len(positions) == 1
            assert positions[0]["title"] == "科研助理"
            assert positions[0]["capture_mode"] == "dynamic"
            with Session(engine) as session:
                assert set(session.scalars(select(Capture.mode))) == {"static", "dynamic"}
            print("PASS: static shell -> deferred job -> real CDP table -> SQLite position")
            print("First pass:", first)
            print("Second pass:", second)
            print("Position:", positions[0]["title"], positions[0]["headcount"])
        finally:
            engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
