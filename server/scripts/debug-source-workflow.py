"""Opt-in expiry tracing: saved recipe + real flow/bwrap; no live model or crawler.

Synthetic dates belong only to independently constructed controls. Saved source
bundles and all accepted authoring artifacts are read without modification.
"""

import argparse
import asyncio
import copy
import hashlib
import json
import sys
from datetime import UTC, datetime, time, timedelta
from importlib.metadata import version
from pathlib import Path
from zoneinfo import ZoneInfo

from xdog.flow.events import NodeFailed, NodeFinished, NodeStarted

from youju.onboarding.recipes import RecipeRunner, candidate_digest
from youju.onboarding.sandbox import Sandbox
from youju.onboarding.toolkit import VALIDITY_SCHEMA, check_validity, extract_positions
from youju.onboarding.xdog import XdogModels, check_schema

SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_REPORT_BYTES = 2 * 1024 * 1024


def read_json(path, limit):
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError(f"Input exceeds its byte limit: {path.name}")
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def save_new(path, value):
    raw = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    if len(raw.encode("utf-8")) > MAX_REPORT_BYTES:
        raise ValueError("Debug report exceeds its byte limit")
    with path.open("x", encoding="utf-8") as stream:
        stream.write(raw + "\n")


class TracedSandbox:
    """Observation only: every candidate import and call still enters real bwrap."""

    def __init__(self, sandbox):
        self.sandbox = sandbox
        self.calls = []

    async def run_script(self, files, module, function, inputs):
        value = await self.sandbox.run_script(files, module, function, inputs)
        self.calls.append(
            {
                "module": module,
                "function": function,
                "bundle_id": inputs["bundle"].get("id"),
                "now_iso": inputs.get("now_iso"),
                "result": copy.deepcopy(value),
            }
        )
        return value


def event_record(event):
    record = {"event": type(event).__name__, "node_id": event.node_id, "step": event.step}
    if isinstance(event, NodeFinished | NodeFailed):
        record["duration_seconds"] = event.duration_s
    if isinstance(event, NodeFailed):
        record["error"] = event.error
    # Exclude previews: they are lossy and are not evidence of node output values.
    return record


async def synthetic_bundle(sandbox, name, deadline_text):
    body = (
        "<!doctype html><html><head><title>合成招聘公告</title></head><body>"
        "<main><h1>合成招聘公告，仅用于有效期控制验证</h1>"
        f"<p>{deadline_text}</p>"
        "<table><tr><th>岗位名称</th><th>招聘人数</th></tr>"
        "<tr><td>合成测试岗位</td><td>1</td></tr></table></main></body></html>"
    ).encode()
    url = f"https://synthetic-expiry.invalid/{name}"
    material_key = "synthetic-expiry:" + name
    document = await sandbox.parse(
        body, media_type="text/html; charset=utf-8", url=url, material_key=material_key
    )
    document.update(url=url, resource_id=material_key, version_id=material_key + ":v1")
    assert document["status"] == "ok" and not document["truncated"]
    return {
        "id": material_key,
        "source_id": "synthetic-expiry-controls-not-ustc",
        "resource_id": material_key,
        "url": url,
        "page": document,
        "attachments": [],
        "status": "complete",
        "errors": [],
    }


async def control(candidate, sandbox, name, bundle, clock, expected):
    traced = TracedSandbox(sandbox)
    events = []
    before = copy.deepcopy(bundle)

    def detailed_stub(task, payload, schema, tier):
        assert task == "extract_positions" and tier == "large"
        assert payload["bundle"]["source_id"] == "synthetic-expiry-controls-not-ustc"
        assert payload["validity"]["status"] != "expired", "Expired branch reached the model"
        return extract_positions(payload["bundle"], payload["config"])

    models = XdogModels(stub=detailed_stub)
    result = await RecipeRunner(traced, models).run(
        candidate, bundle, now_iso=clock, on_event=events.append
    )
    starts = [event.node_id for event in events if isinstance(event, NodeStarted)]
    finishes = [event.node_id for event in events if isinstance(event, NodeFinished)]
    nodes = {node["id"]: node["run"] for node in candidate["workflow"]["nodes"]}
    references = [nodes[node] for node in starts]
    assert references[0] == "validity:check", "First node was not the validity script"
    assert traced.calls[0]["module"] == "validity"
    assert result["validity"] == traced.calls[0]["result"] == check_validity(bundle, clock)
    assert result["validity"]["status"] == expected
    assert starts == finishes and not any(isinstance(event, NodeFailed) for event in events)
    assert result["status"] == "success" and len(result["positions"]) == 1
    # Source workflows may use deterministic parsing for current material too;
    # expiry still mandates the model-free path. Validate the observed branch,
    # not the routing choice of the original reference template.
    if expected == "expired" or "fast_parse:parse" in references:
        assert models.calls == [], "Script-only extraction unexpectedly called a model"
        assert references == ["validity:check", "fast_parse:parse", "youju_toolkit:finalize"]
        assert [call["module"] for call in traced.calls] == ["validity", "fast_parse"]
    else:
        assert len(models.calls) == 1 and models.calls[0]["mode"] == "stub"
        assert references == [
            "validity:check",
            "youju_toolkit:extract_detailed",
            "youju_toolkit:finalize",
        ]
        assert [call["module"] for call in traced.calls] == ["validity"]
    assert bundle == before, "Workflow mutated the input control bundle"
    return {
        "name": name,
        "material_kind": "synthetic_control_not_source_evidence",
        "bundle": bundle,
        "now_iso": clock,
        "expected_validity": expected,
        "node_order": starts,
        "script_references": references,
        "events": [event_record(event) for event in events],
        "sandbox_calls": traced.calls,
        "model_boundary_calls": len(models.calls),
        "model_calls": models.calls,
        "live_model_calls": 0,
        "validity_preserved": True,
        "result": result,
        "passed": True,
    }


async def verify(args, report):
    author_report, report_hash = read_json(args.author_report, 1024 * 1024)
    candidate_path = (
        args.candidate or args.author_report.parent / "accepted-candidate.json"
    ).resolve()
    payload_path = (Path(author_report["workspace"]) / "input/payload.json").resolve()
    candidate, candidate_hash = read_json(candidate_path, 300_000)
    payload, payload_hash = read_json(payload_path, 8 * 1024 * 1024)
    samples = payload["samples"]
    assert isinstance(samples, list) and 1 <= len(samples) <= 3
    assert sorted(sample["url"] for sample in samples) == sorted(author_report["sample_urls"])
    report.update(
        input_provenance={
            "candidate": {"path": str(candidate_path), "sha256": candidate_hash},
            "author_report": {"path": str(args.author_report), "sha256": report_hash},
            "payload": {"path": str(payload_path), "sha256": payload_hash},
        },
        recipe_digest=candidate_digest(candidate),
        source_id=author_report.get("source_id"),
        saved_sample_count=len(samples),
    )
    sandbox = Sandbox()
    report["sandbox_probe"] = await sandbox.probe()
    assert report["sandbox_probe"]["isolated"]
    errors = RecipeRunner(sandbox).validate(candidate)
    assert not errors, f"Accepted recipe fails current validation: {errors}"
    script_files = {
        "validity.py": candidate["validity_py"],
        "fast_parse.py": candidate["fast_parse_py"],
    }
    for number, sample in enumerate(samples, 1):
        before = copy.deepcopy(sample)
        observed = await sandbox.run_script(
            script_files,
            "validity",
            "check",
            {"bundle": sample, "now_iso": args.now_iso, "config": candidate["config"]},
        )
        check_schema(observed, VALIDITY_SCHEMA)
        assert observed == check_validity(sample, args.now_iso)
        assert sample == before
        record = {
            "material_kind": "saved_source_bundle_unmodified",
            "bundle_id": sample["id"],
            "resource_id": sample["resource_id"],
            "url": sample["url"],
            "now_iso": args.now_iso,
            "execution": "real_bwrap_validity_check_only",
            "validity": observed,
            "live_model_calls": 0,
            "full_extraction_performed": False,
            "host_check_matches": True,
        }
        report["saved_source_validity"].append(record)
        save_new(args.output / f"saved-validity-{number}.json", record)

    clock = datetime.fromisoformat(args.now_iso)
    if clock.tzinfo is None:
        raise ValueError("Provide an explicit timezone in --now-iso")
    day = clock.astimezone(SHANGHAI).date()
    controls = [
        ("expired", day - timedelta(days=1), args.now_iso, "expired"),
        ("not-expired", day + timedelta(days=1), args.now_iso, "not_expired"),
        ("unknown-rolling", None, args.now_iso, "unknown"),
        (
            "date-only-last-day",
            day,
            datetime.combine(day, time.max, SHANGHAI).isoformat(),
            "not_expired",
        ),
        (
            "date-only-next-midnight",
            day,
            datetime.combine(day + timedelta(days=1), time.min, SHANGHAI).isoformat(),
            "expired",
        ),
    ]
    for name, deadline, control_clock, expected in controls:
        text = f"报名截止日期：{deadline.isoformat()}。" if deadline else "长期招聘，招满为止。"
        bundle = await synthetic_bundle(sandbox, name, text)
        result = await control(candidate, sandbox, name, bundle, control_clock, expected)
        save_new(args.output / f"control-{name}.json", result)
        report["synthetic_controls"].append(
            {
                key: result[key]
                for key in (
                    "name",
                    "now_iso",
                    "node_order",
                    "expected_validity",
                    "model_boundary_calls",
                    "live_model_calls",
                    "validity_preserved",
                    "passed",
                )
            }
        )
    assert read_json(candidate_path, 300_000)[1] == candidate_hash
    assert read_json(payload_path, 8 * 1024 * 1024)[1] == payload_hash
    assert read_json(args.author_report, 1024 * 1024)[1] == report_hash
    report["input_files_unchanged"] = True
    report["success"] = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--author-report", type=Path, required=True)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--now-iso", required=True, help="Explicit evaluation clock with timezone")
    parser.add_argument(
        "--output", type=Path, required=True, help="New directory; never overwritten"
    )
    args = parser.parse_args()
    args.author_report = args.author_report.resolve()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    report = {
        "success": False,
        "started_at": datetime.now(UTC).isoformat(),
        "evaluation_now_iso": args.now_iso,
        "python": sys.version.split()[0],
        "xdog_flow": version("xdog-flow"),
        "execution": "actual_generated_recipe_with_real_flow_and_bwrap",
        "detailed_model_boundary": "offline_stub_only",
        "saved_source_validity": [],
        "synthetic_controls": [],
        "live_model_calls": 0,
        "crawl_performed": False,
        "full_source_audit": False,
        "source_activation_performed": False,
    }
    try:
        asyncio.run(verify(args, report))
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)[:2000]}
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        save_new(args.output / "report.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
