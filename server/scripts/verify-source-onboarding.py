"""Opt-in integration: real HTTP broker, bwrap, Chromium and flow; synthetic site/stub models."""

import argparse
import asyncio
import json
import runpy
import socket
from datetime import timedelta
from pathlib import Path
from tempfile import mkdtemp
from unittest.mock import patch

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from youju.crawlers import DynamicCrawler, OriginPolicy
from youju.crawlers.material import MaterialCrawler
from youju.models import AuditResult, Capture, Resource, ResourceBundle, ResourceScan, ScanPolicy
from youju.models.base import utc_now
from youju.onboarding.auditing import current_source_positions
from youju.onboarding.sandbox import Sandbox, SandboxError

FIXTURES = runpy.run_path(str(Path(__file__).resolve().parents[1] / "tests/test_onboarding.py"))
BASE = FIXTURES["BASE"]


def resolve(host, port, *args, **kwargs):
    if host != "jobs.example.org":
        raise AssertionError(f"Unexpected DNS lookup: {host}")
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


async def main(root):
    sandbox = Sandbox()
    assert (await sandbox.probe())["isolated"]
    engine, store, fixtures, pipeline = FIXTURES["setup_source"](root, sandbox=sandbox)
    requests = []

    async def fixture(request):
        assert request.headers["host"] == "jobs.example.org"
        requests.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(404, stream=httpx.ByteStream(b""))
        body, content_type = fixtures.pages[BASE.rstrip("/") + request.url.raw_path.decode()]
        return httpx.Response(
            200, headers={"content-type": content_type}, stream=httpx.ByteStream(body)
        )

    transport = httpx.MockTransport(fixture)
    policy = OriginPolicy(frozenset({BASE.rstrip("/")}))
    static = MaterialCrawler(policy, min_interval_seconds=0.01, _transport=transport)
    dynamic = DynamicCrawler(policy, min_interval_seconds=0.01, _transport=transport)
    dynamic._lock, dynamic._last_request, dynamic._delays = (
        static._lock,
        static._last_request,
        static._delays,
    )
    pipeline.worker._material, pipeline.worker._dynamic = static, dynamic
    try:
        with patch("socket.getaddrinfo", resolve):
            stages = await FIXTURES["run_onboarding"](pipeline)
            assert stages[-1]["stage"] == "active", stages[-1]
            before = current_source_positions(store, "source")
            assert len(before) == 2
            fixtures.pages[BASE + "positions.xlsx"] = (
                FIXTURES["xlsx"]("更新后的实验助理"),
                fixtures.pages[BASE + "positions.xlsx"][1],
            )
            listing, content_type = fixtures.pages[BASE]
            fixtures.pages[BASE] = (
                listing.replace(
                    b"</body>", '<a href="/dynamic-notice">新增公开招聘公告</a></body>'.encode()
                ),
                content_type,
            )
            cutoff = (utc_now() + timedelta(days=10)).date()
            rendered = (
                f"<h1>新增公开招聘公告</h1><p>报名截止日期：{cutoff:%Y年%m月%d日}。</p>"
                + FIXTURES["table"]("动态教师")
            )
            shell = (
                "<html><head><meta charset='utf-8'><title>Loading</title></head><body>"
                "<script>setTimeout(() => {document.title='新增公开招聘公告';"
                f"document.body.innerHTML={json.dumps(rendered, ensure_ascii=False)};"
                "}, 100);</script></body></html>"
            ).encode()
            fixtures.pages[BASE + "dynamic-notice"] = (shell, "text/html; charset=utf-8")
            with Session(engine) as session, session.begin():
                for cadence in session.scalars(select(ScanPolicy)):
                    cadence.next_scan_at = utc_now() - timedelta(seconds=1)
            first = await pipeline.incremental("source", limit=50)
            assert first["pass"].get("dynamic", 0) == 0, first
            with Session(engine) as session:
                new = session.scalar(
                    select(Resource).where(Resource.normalized_url == BASE + "dynamic-notice")
                )
                scan = session.get(ResourceScan, new.id)
                assert (scan.mode, scan.state) == ("dynamic", "ready")
            second = await pipeline.incremental("source", limit=50)
            assert second["pass"]["dynamic"] == 1 and second["summary"]["audit"]["passed"], second
        positions = current_source_positions(store, "source")
        assert {item["title"] for item in positions} == {"更新后的实验助理", "行政助理", "动态教师"}
        boundaries = []
        for code, label in [
            ("from pathlib import Path\nPath('/etc/passwd').read_text()\n", "no_host_files"),
            (
                "import socket\nsocket.create_connection(('93.184.216.34', 443), timeout=1)\n",
                "no_external_network",
            ),
        ]:
            code += "def parse(ctx, bundle, config):\n    return {}\n"
            try:
                await sandbox.run_script(
                    {"fast_parse.py": code}, "fast_parse", "parse", {"bundle": {}, "config": {}}
                )
            except SandboxError:
                boundaries.append(label)
            else:
                raise AssertionError(f"Sandbox boundary failed: {label}")
        with Session(engine) as session:
            counts = {
                "resources": session.scalar(select(func.count()).select_from(Resource)),
                "bundles": session.scalar(select(func.count()).select_from(ResourceBundle)),
                "audits": session.scalar(select(func.count()).select_from(AuditResult)),
                "captures": session.scalar(select(func.count()).select_from(Capture)),
                "modes": sorted(set(session.scalars(select(Capture.mode)))),
            }
        report = {
            "verification": "synthetic-http-real-bwrap-real-chromium-real-flow-stubbed-models",
            "onboarding_stages": [stage["stage"] for stage in stages],
            "first_incremental_pass": first["pass"],
            "second_incremental_pass": second["pass"],
            "counts": counts,
            "current_positions": len(positions),
            "boundaries": boundaries,
            "http_requests": len(requests),
            "external_requests": 0,
            "limitations": [
                "No live model generation or live-source onboarding was evaluated.",
                "No OCR, headed browser, frontend or deployment verification.",
            ],
        }
        (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        (root / "positions.json").write_text(json.dumps(positions, ensure_ascii=False, indent=2))
        (root / "status.json").write_text(
            json.dumps(store.status("source"), ensure_ascii=False, indent=2)
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"Artifacts: {root}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output:
        args.output.mkdir(parents=True, exist_ok=False)
        root = args.output.resolve()
    else:
        root = Path(mkdtemp(prefix="youju-onboarding-"))
    asyncio.run(main(root))
