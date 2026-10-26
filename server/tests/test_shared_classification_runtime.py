"""Synthetic runtime integration, not gold evaluation or a generic accuracy claim.

Only already-parsed test_sourcekit documents are used. No acquisition adapter,
provider, Recipe Agent, network request or paid model is invoked.
"""

import asyncio
import copy
import json
from contextlib import contextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_sourcekit import FakeModel, bundle, document, unknown

from youju import background, create_app
from youju.admin import pipeline_json, source_json
from youju.crawlers.material import MaterialResponse
from youju.database import create_sqlite_engine, initialize_database
from youju.models import (
    Discovery,
    Organization,
    Resource,
    ResourceScan,
    ScanPolicy,
    Seed,
    SeedKind,
    Source,
    SourcePipeline,
)
from youju.models.base import utc_now
from youju.onboarding import cli
from youju.onboarding.classification import Classifier, validate_classification
from youju.onboarding.classification_budget import ClassificationBudget, covered
from youju.onboarding.classification_corpus import assert_disjoint
from youju.onboarding.classification_evaluation import evaluate_pipeline
from youju.onboarding.classification_features import content_group, feature_text
from youju.onboarding.local_classifier import LocalPageModel
from youju.onboarding.pipeline import Pipeline
from youju.onboarding.shared_classifier import SharedPageModel
from youju.onboarding.store import PipelineStore, json_bytes
from youju.onboarding.toolkit import evidence_index, extract_positions, material_issues
from youju.poc.ledger import Ledger

BASE = "https://jobs.example.org/"


class Prediction:
    """Deliberately confident scores cannot bypass the host's semantic guards."""

    key = "a" * 64

    def __init__(self, role, business, **extra):
        self.value = {
            "page_role": role,
            "business_type": business,
            "confidence": 0.999,
            "margin": 0.998,
            **extra,
        }
        self.inputs = []

    def predict(self, text):
        self.inputs.append(text)
        return dict(self.value)


def supporting(business, key="page"):
    text = {
        "guide": "本手册提供入职后的生活指南与办事流程，介绍住宿交通和后勤服务。" * 5,
        "report": "会议回顾过去一年后勤服务安排，讨论食堂交通及宿舍设施的日常维护。" * 5,
        "unrelated": "用户名和密码用于账户访问，请阅读用户协议并妥善保存登录凭证。" * 5,
    }[business]
    return bundle(key, title="服务信息", texts=[text])


@pytest.fixture
def shared_artifact(tmp_path):
    """Actual numerical fit on disjoint synthetic source/template/feature records."""
    parts, replays = {}, []
    for split, count in (("training", 12), ("calibration", 6), ("replay", 2)):
        rows = []
        for role, business in (("article", "guide"), ("article", "report"), ("other", "unrelated")):
            for number in range(count):
                page = supporting(business, f"{split}-{business}-{number}")
                page["source_id"] = f"{split}-source"
                # feature_text normalizes digits, so use genuinely distinct text suffixes.
                page["page"]["blocks"][-1]["text"] += split + chr(0x4E00 + number)
                rows.append(
                    {
                        "id": page["id"],
                        "source_id": page["source_id"],
                        "template_family": f"{split}-template",
                        "group": content_group(page),
                        "text": feature_text(page),
                        "page_role": role,
                        "business_type": business,
                        "label_quality": "synthetic",
                    }
                )
                if split == "replay":
                    replays.append((page, role, business))
        parts[split] = rows
    assert_disjoint(parts)
    model = SharedPageModel.fit(parts["training"], parts["calibration"], allow_synthetic=True)
    path = tmp_path / "synthetic-shared.json"
    model.save(path)
    return SharedPageModel.load(path), path, replays


@pytest.fixture
def parsed_store(tmp_path):
    """Persist parsed fixtures through the real ledger, without fetching/parsing bytes."""
    engine = create_sqlite_engine(tmp_path / "runtime.sqlite3")
    initialize_database(engine)
    stores = []

    def save(pages):
        assert not stores, "One inventory per test"
        with Session(engine) as session, session.begin():
            source = Source(
                id="source",
                name="合成来源",
                base_url=BASE,
                publisher=Organization(id="org", name="合成单位"),
            )
            for key in pages:
                session.add(
                    Seed(
                        name=key,
                        kind=SeedKind.LISTING,
                        source=source,
                        resource=Resource(original_url=BASE + key, source=source),
                    )
                )
        Ledger(engine).approve("source", "example.org", "Synthetic integration fixture")
        store = PipelineStore(engine, tmp_path / "blobs")
        store.start("source", {"retry_delay": 0})
        # start() also registers the source homepage; leave that unsupplied slot pending.
        with Session(engine) as session:
            excluded = set(
                session.scalars(select(Resource.id).where(Resource.normalized_url == BASE))
            )
        for _ in pages:
            claim = store.claim("source", excluded)
            assert claim is not None and claim.url.removeprefix(BASE) in pages
            page = copy.deepcopy(pages[claim.url.removeprefix(BASE)]["page"])
            page["url"] = claim.url
            for link in page["links"]:
                link.setdefault("raw_url", link["url"])
            response = MaterialResponse(
                url=claim.url,
                final_url=claim.url,
                body=json_bytes(page),
                media_type="text/html",
                http_status=200,
                captured_at=utc_now(),
                headers={},
            )
            assert store.save(claim, response, page) == "done"
        store.build_bundles("source")
        stores.append(store)
        return store

    yield save
    engine.dispose()


def test_numerical_shared_heads_replay_through_host_evidence_and_notice_selection(
    shared_artifact, parsed_store, tmp_path
):
    model, _, replays = shared_artifact
    assert model.metadata["calibration_mode"] == "synthetic_test"
    assert model.metadata["training"]["label_quality_counts"] == {"synthetic": 36}
    classifier = Classifier(local_model=model)
    for page, role, business in replays:
        # This checks the numerical -> _local -> host-validation seam on six fixtures,
        # not an estimate of real-world accuracy or qualification for promotion.
        result = classifier._local(page)
        assert result is not None, (business, model.predict(feature_text(page)))
        assert (result["page_roles"], result["business_type"]) == ([role], business)
        assert result["method"] == "local" and covered(result)
        assert result["evidence_ids"] and set(result["evidence_ids"]) <= evidence_index(page).keys()
        assert validate_classification(page, result) == []
        assert result["model_metadata"]["local_model_sha256"] == model.key
        assert result["model_metadata"]["schedule_owner"] == "host"
        assert result["revisit"] == "weekly"
        page["assessment"] = asyncio.run(classifier.classify(page))
        assert page["assessment"]["method"] == "local"
        assert not Pipeline._is_notice(page)
        assert extract_positions(page)["positions"] == []
        forged = {**result, "evidence_ids": ["invented:block"]}
        assert "UNKNOWN_EVIDENCE" in {e["code"] for e in validate_classification(page, forged)}

    material = {page["id"]: page for page, _, _ in replays}
    rows = [
        {
            "id": page["id"],
            "source_id": page["source_id"],
            "template_family": "replay-template",
            "group": content_group(page),
            "text": feature_text(page),
            "page_role": role,
            "business_type": business,
            "label_quality": "synthetic",
        }
        for page, role, business in replays
    ]
    report = evaluate_pipeline(model, rows, lambda key, source: material[key], allow_synthetic=True)
    assert report["evaluation_path"] == "rules-plus-shared-with-host-guards-no-llm"
    assert report["external_model_calls"] == 0 and report["promotion_qualified"] is False
    assert report["accepted"] == len(rows)
    assert sum(report["methods"].values()) == report["total"]
    store = parsed_store({page["id"]: page for page, _, _ in replays})
    saved_rows = []
    for page in store.bundles("source"):
        business = page["url"].split("/")[-1].split("-")[1]
        saved_rows.append(
            {
                "id": page["id"],
                "source_id": "source",
                "template_family": "snapshot-template",
                "group": content_group(page),
                "text": feature_text(page),
                "page_role": "other" if business == "unrelated" else "article",
                "business_type": business,
                "label_quality": "synthetic",
            }
        )
    records_path = tmp_path / "snapshot-test.json"
    records_path.write_text(json.dumps(saved_rows))
    result_path = tmp_path / "combined-report.json"
    invocation = (
        create_app({"TESTING": True})
        .test_cli_runner()
        .invoke(
            args=[
                "classifier-evaluate-pipeline",
                str(shared_artifact[1]),
                str(records_path),
                "--database",
                str(store.engine.url.database),
                "--storage",
                str(store.blobs.root),
                "--output",
                str(result_path),
                "--allow-synthetic",
            ]
        )
    )
    assert invocation.exit_code == 0, (invocation.output, invocation.exception)
    combined = json.loads(result_path.read_text())
    assert combined["external_model_calls"] == 0 and combined["total"] == len(saved_rows)
    wrong = copy.deepcopy(rows)
    wrong[0]["text"] += "tampered-annotation"
    with pytest.raises(ValueError, match="features changed"):
        evaluate_pipeline(model, wrong, lambda key, source: material[key], allow_synthetic=True)


def test_shared_predictions_cannot_hide_mixed_offers_missing_material_or_legacy_binary():
    for business in ("guide", "report"):
        supporting_page = supporting(business)
        # The legacy label must not override the explicit intent head.
        local = Prediction("article", business, label="recruitment")
        assert Classifier(local_model=local)._local(supporting_page)["business_type"] == business
        mixed = copy.deepcopy(supporting_page)
        mixed["page"]["blocks"].append(
            {
                "id": "offer",
                "text": "学院现面向社会招聘管理助理，具体事项另行公布。",
                "locator": "block 9",
            }
        )
        assert Classifier(local_model=local)._local(mixed) is None
        assert Classifier(local_model=Prediction("article", "unrelated"))._local(mixed) is None
        outcome = asyncio.run(Classifier(local_model=local).classify(mixed))
        assert outcome["requires_review"] or outcome["business_type"] == "recruitment"
        assert not (
            covered(outcome) and outcome["business_type"] in {"guide", "report", "unrelated"}
        )

    for business in ("guide", "report", "unrelated"):
        for failure in ("missing", "unreadable"):
            page = supporting(business)
            attachment = document("attachment", title="", format="pdf")
            page["page"]["links"].append(
                {"url": attachment["url"], "label": "补充材料", "is_attachment": True}
            )
            if failure == "unreadable":
                attachment.update(status="failed", blocks=[], errors=[{"code": "parser_failed"}])
                page.update(attachments=[attachment], status="partial")
            assert material_issues(page)
            classifier = Classifier(local_model=Prediction("article", business))
            assert classifier._local(page) is None
            outcome = asyncio.run(classifier.classify(page))
            assert outcome["requires_review"] and not covered(outcome)
            assert outcome["revisit"] == "weekly"

    offer = bundle(title="用人信息", texts=["学院现面向社会招聘管理助理。"])
    positive = Classifier(local_model=Prediction("article", "recruitment", label="unrelated"))
    result = asyncio.run(positive.classify(offer))
    assert result["method"] == "local" and result["business_type"] == "recruitment"
    assert Pipeline._is_notice({**offer, "assessment": result}) and result["revisit"] == "daily"
    irrelevant = supporting("unrelated")
    assert Classifier(local_model=Prediction("article", "recruitment"))._local(irrelevant) is None

    legacy = Prediction("article", "recruitment")
    legacy.value = {"label": "recruitment", "confidence": 0.999, "margin": 0.998}
    classifier = Classifier(local_model=legacy)
    assert classifier._local(offer) is None
    result = asyncio.run(classifier.classify(offer))
    assert result["requires_review"] and not covered(result) and result["method"] == "unknown"


def test_llm_none_is_host_scheduled_and_source_budget_survives_runtime_profiles(parsed_store):
    pages = {
        "guide": supporting("guide", "guide"),
        "report": supporting("report", "report"),
        "offer": bundle("offer", title="用人信息", texts=["学院现面向社会招聘管理助理。"]),
        "correction": bundle(
            "correction", title="事项说明", texts=["本次招聘事项更正，细节按原文核对。" * 12]
        ),
        "cancellation": bundle(
            "cancellation", title="事项说明", texts=["本次招聘事项取消，细节按原文核对。" * 12]
        ),
        "list": bundle(
            "list",
            title="信息列表",
            texts=["服务条目说明甲乙", "服务条目说明丙丁", "服务条目说明戊己"],
        ),
    }
    pages["list"]["page"]["links"] = [
        {"url": BASE + target, "label": label, "is_attachment": False}
        for target, label in zip(
            ("guide", "report", "offer"),
            ("服务条目说明甲乙", "服务条目说明丙丁", "服务条目说明戊己"),
            strict=True,
        )
    ]
    store = parsed_store(pages)
    saved = {p["url"].removeprefix(BASE): p for p in store.bundles("source")}
    # Explicit test override permits one small-model call per synthetic bundle.
    budget = ClassificationBudget(store, "first-runtime", 1.0)
    for key, role, business, cadence in (
        ("guide", "article", "guide", "weekly"),
        ("report", "article", "report", "weekly"),
        ("offer", "article", "recruitment", "daily"),
        ("correction", "article", "correction", "daily"),
        ("cancellation", "article", "cancellation", "daily"),
        ("list", "notice_list", "unrelated", "daily"),
    ):
        page = saved[key]
        response = {
            **unknown(),
            "page_roles": [role],
            "business_type": business,
            "revisit": "none",
            "requires_review": False,
            "evidence_ids": [page["page"]["blocks"][1]["id"]],
        }
        model = FakeModel(response)
        classifier = Classifier(model, llm_budget=budget.reserve)
        result = asyncio.run(classifier.classify(page))
        assert model.tiers == ["small"] and result["method"] == "small"
        assert result["revisit"] == cadence and covered(result)
        assert validate_classification(page, result) == []
        assert store.assess(page, result, "first-runtime")
        with Session(store.engine) as session:
            policy = session.get(ScanPolicy, page["resource_id"])
            scan = session.get(ResourceScan, page["resource_id"])
            assert policy.cadence == cadence and policy.next_scan_at is not None
            assert (policy.next_scan_at - scan.last_success_at).days == (
                1 if cadence == "daily" else 7
            )
    summary = budget.summarize("source")
    assert summary["llm_reserved_calls"] == summary["llm_call_limit"] == 6
    assert summary["cheap_covered"] == 0 and not summary["target_met"]
    restarted = ClassificationBudget(store, "changed-runtime", 1.0)
    model = FakeModel()  # Exhaustion must be checked before a provider dispatch.
    result = asyncio.run(Classifier(model, llm_budget=restarted.reserve).classify(saved["guide"]))
    assert model.tiers == [] and result["requires_review"]
    assert "LLM_BUDGET_EXHAUSTED" in result["reason"]
    assert restarted.summarize("source")["llm_reserved_calls"] == 6
    with Session(store.engine) as session:
        ledgers = session.get(SourcePipeline, "source").summary["classification_budgets"]
        assert ledgers["first-runtime"]["reserved"] == 6
        assert ledgers["changed-runtime"]["reserved"] == 0
        display = pipeline_json(session, "source")["summary"]["classification"]
        assert display["llm_reserved_calls"] == 6
        assert display["unassessed"] == 6 and display["requires_review"] == 0
        assert source_json(session, session.get(Source, "source"))["base_url"] == BASE


def test_cli_shared_loader_inventory_and_cosmetic_source_changes_are_independent(
    shared_artifact, parsed_store, monkeypatch, tmp_path
):
    model, artifact, replays = shared_artifact
    original = replays[0][0]
    cosmetic = copy.deepcopy(original)
    cosmetic.update(
        source_id="different-source", url="https://cosmetic.example/招聘/notice?theme=dark"
    )
    cosmetic["page"]["url"] = cosmetic["url"]
    for block in cosmetic["page"]["blocks"]:
        block["id"] = "cosmetic:" + block["id"]
    assert feature_text(original) == feature_text(cosmetic)
    assert content_group(original) == content_group(cosmetic)
    result = Classifier(local_model=model)._local(cosmetic)
    assert result["business_type"] == "guide" and validate_classification(cosmetic, result) == []
    assert all(key.startswith("cosmetic:") for key in result["evidence_ids"])
    structure = feature_text(cosmetic).splitlines()[0]
    assert "paragraphs_" in structure and "position_headers_absent" in structure
    assert not any(role in structure for role in ("article", "navigation", "notice_list"))

    page = supporting("guide")
    page["page"]["links"] = [
        {"url": BASE + "future", "label": "后续公告入口", "is_attachment": False},
        {"url": "https://outside.example/notice", "label": "站外线索", "is_attachment": False},
    ]
    store = parsed_store({"page": page})
    saved = store.bundles("source")[0]
    before = copy.deepcopy(saved)

    def inventory():
        with Session(store.engine) as session:
            return (
                list(
                    session.execute(
                        select(Resource.id, Resource.normalized_url).order_by(Resource.id)
                    )
                ),
                list(
                    session.execute(
                        select(ResourceScan.resource_id, ResourceScan.state).order_by(
                            ResourceScan.resource_id
                        )
                    )
                ),
                list(
                    session.execute(
                        select(Discovery.raw_url, Discovery.target_resource_id).order_by(
                            Discovery.id
                        )
                    )
                ),
            )

    untouched = inventory()
    assert untouched[2]  # Real discovery provenance, not just equality of empty inventories.
    with Session(store.engine) as session:
        future = session.scalar(select(Resource).where(Resource.normalized_url == BASE + "future"))
        assert session.get(ResourceScan, future.id).state == "ready"
    for business in ("guide", "report", "unrelated", "recruitment", "unknown"):
        # assess persists host output; labels are not a discovery filter. This loop
        # deliberately tests that storage seam, not semantic validity of these labels.
        assessment = {**unknown(), "business_type": business}
        assert store.assess(saved, assessment, f"inventory-{business}")
        assert inventory() == untouched
        assert saved == before
        assert store.load_bundle(saved["id"])["page"]["links"] == before["page"]["links"]

    app = create_app(
        {"TESTING": True, "DATABASE": str(tmp_path / "unused.sqlite3"), "AUTH_ISSUER": ""}
    )
    assert {
        "classifier-export",
        "classifier-split",
        "classifier-train",
        "classifier-evaluate",
    } <= app.cli.commands.keys()
    received = []

    class Runtime:
        def __init__(self, selected_store, **kwargs):
            assert selected_store is store
            assert isinstance(kwargs["local_classifier"], SharedPageModel)
            assert kwargs["local_classifier"].key == model.key
            assert kwargs["classification_llm_fraction"] == 0.25
            assert kwargs["models"] is None
            received.append(kwargs)

        async def step(self, source_id, *, limit):
            assert source_id == "source" and limit == 2
            return {"state": "ready", "classification": result, "url": cosmetic["url"]}

    @contextmanager
    def local_store(*args):
        yield store

    monkeypatch.setattr(cli, "_store", local_store)
    monkeypatch.setattr("youju.onboarding.pipeline.Pipeline", Runtime)
    runner = app.test_cli_runner()
    invocation = runner.invoke(
        args=[
            "source-step",
            "source",
            "--limit",
            "2",
            "--local-classifier",
            str(artifact),
            "--classification-llm-fraction",
            "0.25",
        ]
    )
    assert invocation.exit_code != 0
    assert "Runtime shared classifier" in str(invocation.exception)
    # Explicit fixture-only loader substitution exercises CLI plumbing without
    # relabeling synthetic examples as human or weakening the real runtime guard.
    monkeypatch.setattr(
        "youju.onboarding.shared_classifier.load_runtime_model", SharedPageModel.load
    )
    invocation = runner.invoke(
        args=[
            "source-step",
            "source",
            "--limit",
            "2",
            "--local-classifier",
            str(artifact),
            "--classification-llm-fraction",
            "0.25",
        ]
    )
    assert invocation.exit_code == 0, (invocation.output, invocation.exception)
    displayed = json.loads(invocation.output)
    assert displayed["classification"]["business_type"] == "guide"
    assert displayed["url"] == cosmetic["url"] and len(received) == 1

    # Exercise the production worker's loader too, but replace its external runtime.
    monkeypatch.setattr(background, "load_runtime_model", SharedPageModel.load)
    app.config.update(
        DATABASE=store.engine.url.database,
        LOCAL_CLASSIFIER=str(artifact),
        RECIPE_RPC_URL="http://synthetic.invalid",
        RECIPE_RPC_TOKEN="synthetic-only",
    )
    worker_models = []

    async def probe(sandbox):
        return None

    async def watch(config, models, rpc):
        assert models is None and rpc is None
        loaded = config["LOCAL_CLASSIFIER_INSTANCE"]
        assert isinstance(loaded, SharedPageModel) and loaded.key == model.key
        worker_models.append(loaded)

    monkeypatch.setattr(background, "load_app", lambda: app)
    monkeypatch.setattr(background, "configured_models", lambda config: None)
    monkeypatch.setattr("youju.onboarding.sandbox.Sandbox.probe", probe)
    monkeypatch.setattr(background, "RecipeRPCClient", lambda *args: None)
    monkeypatch.setattr(background, "watch_source", watch)
    monkeypatch.setattr(background, "run_cancellable", asyncio.run)
    background.main()
    assert len(worker_models) == 1

    # An actual old binary artifact is rejected by the CLI loader before Runtime.
    binary = LocalPageModel.fit(
        [
            {
                "id": f"{label}-{i}",
                "group": f"{label}-{i}",
                "label": label,
                "text": text + chr(0x4E00 + i),
            }
            for label, text in (("recruitment", "招聘报名岗位"), ("unrelated", "校园交通生活"))
            for i in range(6)
        ],
        [],
    )
    legacy_path = tmp_path / "legacy-binary.json"
    binary.save(legacy_path)
    invocation = runner.invoke(
        args=["source-step", "source", "--local-classifier", str(legacy_path)]
    )
    assert invocation.exit_code != 0
    assert isinstance(invocation.exception, ValueError) and "not a shared model" in str(
        invocation.exception
    )
    assert len(received) == 1 and inventory() == untouched

    app.config["LOCAL_CLASSIFIER"] = str(legacy_path)
    with pytest.raises(ValueError, match="not a shared model"):
        background.main()
    assert len(worker_models) == 1


def test_combined_evaluation_per_source_runs_rules_and_reports_abstention_separately(
    shared_artifact,
):
    model, _, _ = shared_artifact
    pages, rows = {}, []
    for number, (source, title, prose, role, business) in enumerate(
        [
            (
                "unseen-university",
                "学院公开招聘公告",
                "本院现面向社会公开招聘管理助理，报名需提交申请材料与资格条件说明。",
                "article",
                "recruitment",
            ),
            ("unseen-portal", "招聘公告列表", "", "notice_list", "unknown"),
        ]
    ):
        page = bundle(f"audit-{number}", title=title, texts=[prose] if prose else [])
        page["source_id"] = source
        pages[page["id"]] = page
        rows.append(
            {
                "id": page["id"],
                "source_id": source,
                "template_family": f"unseen-template-{number}",
                "group": content_group(page),
                "text": feature_text(page),
                "page_role": role,
                "business_type": business,
                "label_quality": "synthetic",
            }
        )
    report = evaluate_pipeline(model, rows, lambda key, source: pages[key], allow_synthetic=True)
    positive = report["per_source"]["unseen-university"]
    assert positive["coverage"] == 1 and positive["accuracy_over_total"] == 1
    assert report["whole_chain_safety"]["announcement_recognized"] == 1
    assert report["per_source_safety"]["unseen-portal"]["review_or_unknown"] == 1
    assert report["per_source_safety"]["unseen-portal"]["cheap_resolved"] == 0
    assert report["whole_chain_safety"]["cheap_resolved_coverage"] == 0.5
    assert report["external_model_calls"] == 0 and report["promotion_qualified"] is False
