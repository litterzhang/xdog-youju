"""Pure denominators plus synthetic read-only snapshot/public-admission checks."""

import hashlib
import json
import sqlite3
import subprocess
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_jobs_api import saved  # noqa: F401

from youju.database import create_sqlite_engine, initialize_database
from youju.models import (
    AuditResult,
    MaterialDocument,
    PageAssessment,
    ResourceBundle,
    ResourceScan,
    SourcePipeline,
    SourceRecipe,
    SourceRecipeBinding,
)
from youju.models.base import Base
from youju.onboarding.funnel import (
    SCHEMA,
    FunnelContext,
    FunnelObservation,
    aggregate_funnel,
    replay_observations,
    report_source_funnel,
)
from youju.onboarding.store import digest

CLOCK = "2026-10-14T12:00:00+08:00"
CTX = FunnelContext(
    "source",
    CLOCK,
    "new-classifier",
    "new-recipe",
    4,
    mode="new_contract_replay",
    contract="accuracy-v1",
)


def observation(resource_id="one", **kwargs):
    return replace(
        FunnelObservation(
            resource_id,
            "v-" + resource_id,
            "d-" + resource_id,
            "b-" + resource_id,
            CTX.classifier_key,
            CTX.recipe_id,
            CTX.generation,
            material_current=True,
            classified=True,
            page_roles=("article",),
            business_type="recruitment",
            state="accepted",
            quality="full",
            reported_positions=2,
            accepted_positions=2,
        ),
        **kwargs,
    )


def test_pure_denominators_overlap_disjoint_validity_and_quality():
    rows = [
        observation("mixed", page_roles=("notice_list", "article"), validity="not_expired"),
        observation(
            "expired", validity="expired", quality="coarse", state="coarse", accepted_positions=0
        ),
        observation("unknown"),
        observation("cancelled", cancelled=True, state="cancelled", accepted_positions=0),
        observation(
            "list",
            page_roles=("notice_list",),
            business_type="unknown",
            state="missing",
            reported_positions=0,
            accepted_positions=0,
        ),
        observation("attachment", page_roles=("attachment",), validity="expired"),
        observation(
            "missing",
            version_id=None,
            document_id=None,
            bundle_id=None,
            material_current=False,
            classified=False,
            state="missing",
            reported_positions=0,
            accepted_positions=0,
        ),
        observation("failed", state="failed", accepted_positions=0, quality="unversioned"),
    ]
    result = aggregate_funnel(CTX, rows)
    assert result["denominators"] == {
        "unique_resources": 8,
        "latest_versions": 7,
        "latest_material_documents": 7,
        "current_bundles": 7,
    }
    labels = result["classification"]
    assert labels["notice_list"] == 2
    assert labels["recruitment_article"] == 5
    assert labels["recruitment_candidates"] == 6
    assert labels["notice_list_recruitment_article_overlap"] == 1
    recruitment = result["recruitment"]
    assert recruitment["validity"] == {"not_expired": 1, "expired": 2, "unknown": 2}
    assert sum(recruitment["validity"].values()) + recruitment["cancelled"] == 6
    assert result["positions"]["accepted"] == 6
    assert result["positions"]["public_default"] == 0
    matrix = result["positions_by_validity_quality"]
    assert matrix["expired"]["coarse"] == {
        "reported": 2,
        "accepted": 0,
        "public_default": 0,
        "public_with_partial": 0,
    }
    assert matrix["unknown"]["unversioned"]["reported"] == 2
    assert matrix["unknown"]["unversioned"]["accepted"] == 0


@pytest.mark.parametrize("state", ["rejected", "failed", "missing", "stale", "review"])
def test_nonaccepted_states_never_count_as_accepted(state):
    row = observation(state=state, accepted_positions=0, validity="not_expired")
    result = aggregate_funnel(CTX, [row])
    assert result["positions"]["reported"] == 2
    assert result["positions"]["accepted"] == 0
    expected = "unknown" if state in {"missing", "stale"} else "not_expired"
    assert result["recruitment"]["validity"][expected] == 1
    with pytest.raises(ValueError, match="cannot be accepted"):
        aggregate_funnel(CTX, [replace(row, accepted_positions=1)])


def test_duplicates_bad_bindings_and_replay_public_claims_fail_closed():
    row = observation()
    with pytest.raises(ValueError, match="duplicates"):
        aggregate_funnel(CTX, [row, row])
    for changed in (
        replace(row, classifier_key="old"),
        replace(row, generation=3),
        replace(row, recipe_id="old"),
    ):
        with pytest.raises(ValueError, match="bind"):
            aggregate_funnel(CTX, [changed])
    with pytest.raises(ValueError, match="public admission"):
        aggregate_funnel(CTX, [replace(row, public_positions_with_partial=1)])
    with pytest.raises(ValueError, match="UTC offset"):
        aggregate_funnel(replace(CTX, clock="2026-10-14"), [row])


def test_replay_uses_new_observations_not_snapshot_labels_and_requires_full_source():
    row = observation(validity="unknown")
    baseline = replace(
        row, business_type="other", classified=False, state="failed", accepted_positions=0
    )
    envelope = {"schema": SCHEMA, "context": asdict(CTX), "observations": [asdict(row)]}
    ctx, rows = replay_observations(
        envelope, snapshot_rows=[baseline], source_id="source", clock=CLOCK
    )
    assert aggregate_funnel(ctx, rows)["positions"]["accepted"] == 2
    with pytest.raises(ValueError, match="every snapshot resource"):
        replay_observations(
            envelope, snapshot_rows=[baseline, observation("two")], source_id="source", clock=CLOCK
        )
    envelope["observations"][0]["version_id"] = "history"
    with pytest.raises(ValueError, match="current saved snapshot"):
        replay_observations(envelope, snapshot_rows=[baseline], source_id="source", clock=CLOCK)


@pytest.fixture
def snapshot(saved, tmp_path):  # noqa: F811
    """Use synthetic admitted jobs, supplying real content-addressed saved blobs."""
    blobs = tmp_path / "blobs"
    blobs.mkdir()

    def put(value):
        body = json.dumps(value).encode()
        ref = hashlib.sha256(body).hexdigest()
        (blobs / ref).write_bytes(body)
        return ref

    # Copy synthetic rows into a fresh schema, transforming blob references before
    # INSERT. Existing resource versions remain immutable; no trigger is disabled.
    engine = create_sqlite_engine(tmp_path / "snapshot.sqlite3")
    initialize_database(engine)
    with saved.engine.connect() as source, engine.begin() as target:
        scans = {
            r["resource_id"]: r for r in source.execute(select(ResourceScan.__table__)).mappings()
        }
        for table in Base.metadata.sorted_tables:
            records = [dict(r) for r in source.execute(select(table)).mappings()]
            for row in records:
                if table.name == "resource_versions":
                    row["body_ref"] = put({"original": row["id"]})
                    row["content_sha256"] = row["body_ref"]
                    row["byte_length"] = (blobs / row["body_ref"]).stat().st_size
                elif table.name == "material_documents":
                    row["body_ref"] = put({"document": row["id"]})
                elif table.name == "resource_bundles":
                    row["body_ref"] = put(
                        {
                            "id": row["id"],
                            "source_id": "source",
                            "resource_id": row["resource_id"],
                            "page": {"version_id": row["page_version_id"]},
                        }
                    )
                    scan = scans[row["resource_id"]]
                    row["digest"] = digest(
                        {
                            "page_document_id": scan["last_document_id"],
                            "page_state": scan["state"],
                            "page_error": scan["last_error"],
                            "attachments": row["dependencies"],
                        }
                    )
                elif table.name == "source_pipelines":
                    row["summary"] = {"effective_classifier_key": "synthetic"}
            if records:
                target.execute(table.insert(), records)
    yield engine, blobs
    engine.dispose()


def report(snapshot):
    engine, blobs = snapshot
    return report_source_funnel(
        snapshot=engine.url.database, blobs=blobs, source_id="source", clock=CLOCK
    )


def test_snapshot_exact_denominators_public_separation_and_no_mutation(snapshot):
    engine, blobs = snapshot
    database = Path(engine.url.database)
    before = {p: p.read_bytes() for p in [database, *blobs.iterdir()]}
    result = report(snapshot)
    assert result["denominators"]["unique_resources"] == 7
    assert result["denominators"]["latest_material_documents"] == 7
    assert result["recruitment"]["validity"] == {
        "not_expired": 3,
        "expired": 1,
        "unknown": 3,
    }
    assert result["positions"] == {
        "reported": 7,
        "accepted": 7,
        "public_default": 7,
        "public_with_partial": 7,
    }
    assert {p: p.read_bytes() for p in before} == before
    assert set(blobs.iterdir()) == set(before) - {database}


def test_snapshot_excludes_history_wrong_generation_recipe_classifier_and_material(snapshot):
    engine, _ = snapshot
    before = report(snapshot)
    with Session(engine) as session, session.begin():
        binding = session.get(SourceRecipeBinding, "source")
        original = session.scalar(select(AuditResult).where(AuditResult.bundle_id == "future"))
        session.add(SourceRecipe(id="candidate", source_id="source", digest="c", body_ref="c"))
        session.flush()
        for recipe_id, generation, day in [
            ("candidate", binding.generation, "2026-10-14"),
            ("recipe", binding.generation + 1, "2026-10-14"),
            ("recipe", binding.generation, "2099-01-01"),
            ("recipe", binding.generation, "2020-01-01"),
        ]:
            session.add(
                AuditResult(
                    bundle_id=original.bundle_id,
                    recipe_id=recipe_id,
                    generation=generation,
                    evaluated_on=day,
                    status="failed",
                    result={"positions": [1] * 99},
                )
            )
        session.add(
            PageAssessment(
                bundle_id="future",
                classifier_key="old",
                result={"business_type": "other", "page_roles": []},
            )
        )
    observed = report(snapshot)
    assert observed["positions"]["accepted"] == before["positions"]["accepted"]
    assert observed["positions"]["reported"] == before["positions"]["reported"]
    # Failed newer attempts and classifier disagreement label readable saved
    # candidates; activation audit counts remain independent of display counts.
    assert observed["positions"]["public_default"] == 7
    with Session(engine) as session, session.begin():
        session.get(ResourceScan, "future").last_version_id = "version-expired"
    after = report(snapshot)
    assert after["positions"]["accepted"] == 6
    assert after["positions"]["public_default"] == 7
    assert after["classification"]["unclassified_or_stale"] == 1
    assert after["reason_counts"]["stale_material_pointer"] == 1


def test_latest_rejection_quarantines_previously_admitted_positions(snapshot):
    engine, _ = snapshot
    with Session(engine) as session, session.begin():
        original = session.scalar(select(AuditResult).where(AuditResult.bundle_id == "future"))
        session.add(
            AuditResult(
                bundle_id="future",
                recipe_id="recipe",
                generation=0,
                evaluated_on="2026-10-14",
                status="failed",
                result=original.result,
                errors=[{"code": "REJECTED"}],
            )
        )
    result = report(snapshot)
    assert result["positions"]["reported"] == 7
    assert result["positions"]["accepted"] == 6
    assert result["positions"]["public_default"] == 7
    assert result["recruitment"]["states"]["failed"] == 1


def test_missing_blob_and_missing_classifier_do_not_reuse_old_labels(snapshot):
    engine, blobs = snapshot
    with Session(engine) as session:
        ref = session.get(ResourceBundle, "future").body_ref
    (blobs / ref).unlink()
    result = report(snapshot)
    assert result["positions"]["accepted"] == 6
    assert result["reason_counts"]["unreadable_or_invalid_saved_blob"] == 1
    with Session(engine) as session, session.begin():
        session.get(SourcePipeline, "source").summary = {"effective_classifier_key": "not-saved"}
    result = report(snapshot)
    assert result["positions"]["accepted"] == 0
    assert result["classification"]["recruitment_candidates"] == 0


def test_explicit_script_clock_file_and_read_only_sqlite(snapshot, tmp_path):
    engine, blobs = snapshot
    clock_file = tmp_path / "clock.txt"
    clock_file.write_text(CLOCK)
    # The snapshot connection itself is enforced read-only, not just ORM discipline.
    connection = sqlite3.connect(Path(engine.url.database).as_uri() + "?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        connection.execute("DELETE FROM resources")
    connection.close()
    import sys

    script = Path(__file__).resolve().parents[1] / "scripts" / "report_source_funnel.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--snapshot",
            str(engine.url.database),
            "--blobs",
            str(blobs),
            "--source",
            "source",
            "--clock-file",
            str(clock_file),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout)["positions"]["accepted"] == 7


def test_same_version_new_material_document_invalidates_old_bundle(snapshot):
    engine, _ = snapshot
    with Session(engine) as session, session.begin():
        old = session.get(MaterialDocument, "doc-future")
        session.add(
            MaterialDocument(
                id="reparsed",
                version_id=old.version_id,
                parser_key="new-parser",
                format=old.format,
                status=old.status,
                body_ref=old.body_ref,
            )
        )
        session.flush()
        session.get(ResourceScan, "future").last_document_id = "reparsed"
    result = report(snapshot)
    assert result["positions"]["accepted"] == 6
    assert result["positions"]["public_default"] == 7
    assert result["reason_counts"]["stale_bundle_manifest"] == 1


@pytest.mark.parametrize("state", ["rejected", "review", "missing", "cancelled"])
def test_snapshot_latest_dispositions_do_not_admit_positions(snapshot, state):
    engine, _ = snapshot
    with Session(engine) as session, session.begin():
        original = session.scalar(select(AuditResult).where(AuditResult.bundle_id == "future"))
        result = (
            original.result
            if state != "cancelled"
            else {
                "quality_tier": "full",
                "positions": [],
                "validity": {"status": "unknown", "disposition": "cancelled"},
            }
        )
        session.add(
            AuditResult(
                bundle_id="future",
                recipe_id="recipe",
                generation=0,
                evaluated_on="2026-10-14",
                status="success" if state in {"rejected", "missing"} else state,
                result=result,
                parse_id=original.parse_id if state != "missing" else None,
                errors=[{"code": "HOST_REJECTED"}] if state == "rejected" else [],
            )
        )
    result = report(snapshot)
    assert result["positions"]["accepted"] == 6
    assert result["positions"]["public_default"] == 7
    assert result["recruitment"]["states"][state] == 1
    if state == "cancelled":
        assert result["recruitment"]["cancelled"] == 1
        assert sum(result["recruitment"]["validity"].values()) == 6


def test_snapshot_new_contract_replay_never_inherits_saved_labels_or_admission(snapshot):
    engine, blobs = snapshot
    baseline = report(snapshot)
    rows = [
        dict(
            row,
            classifier_key=CTX.classifier_key,
            recipe_id=CTX.recipe_id,
            generation=CTX.generation,
            page_roles=["other"],
            business_type="unrelated",
            state="missing",
            reported_positions=0,
            accepted_positions=0,
            public_positions=0,
            public_positions_with_partial=0,
        )
        for row in baseline["observations"]
    ]
    replay = {"schema": SCHEMA, "context": asdict(CTX), "observations": rows}
    result = report_source_funnel(
        snapshot=engine.url.database, blobs=blobs, source_id="source", clock=CLOCK, replay=replay
    )
    assert result["context"]["mode"] == "new_contract_replay"
    assert result["classification"]["recruitment_candidates"] == 0
    assert result["positions"]["accepted"] == result["positions"]["public_default"] == 0
    assert baseline["positions"]["accepted"] == 7


def test_empty_funnel_has_stable_zero_denominators_and_unknown_bucket():
    result = aggregate_funnel(CTX, [])
    assert result["classification"]["notice_list_recruitment_overlap"] == 0
    assert result["recruitment"]["validity"] == {
        "not_expired": 0,
        "expired": 0,
        "unknown": 0,
    }
    assert all(count == 0 for count in result["denominators"].values())


def test_rejected_temporal_claim_is_unknown_not_an_expired_or_active_assertion(snapshot):
    engine, _ = snapshot
    with Session(engine) as session, session.begin():
        original = session.scalar(select(AuditResult).where(AuditResult.bundle_id == "future"))
        session.add(
            AuditResult(
                bundle_id="future",
                recipe_id="recipe",
                generation=0,
                evaluated_on="2026-10-14",
                status="review",
                result=original.result,
                errors=[{"code": "UNSUPPORTED_VALIDITY"}],
            )
        )
    result = report(snapshot)
    assert result["recruitment"]["validity"] == {
        "not_expired": 2,
        "expired": 1,
        "unknown": 4,
    }
    assert result["positions"]["accepted"] == 6
    assert result["reason_counts"]["rejected_temporal_claim"] == 1


def test_stale_recipe_toolkit_is_not_current_accepted_output(snapshot):
    engine, _ = snapshot
    with Session(engine) as session, session.begin():
        session.get(SourceRecipe, "recipe").validation = {"toolkit_key": "old"}
    result = report(snapshot)
    assert result["recruitment"]["validity"]["unknown"] == 7
    assert result["recruitment"]["states"]["stale"] == 7
    assert result["positions"]["accepted"] == 0
    assert result["positions"]["public_default"] == 7
