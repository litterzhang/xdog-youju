"""Reviewed fact changes preserve source jobs, exact dates and cancellation safety offline."""

import asyncio
import copy
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_onboarding import BASE, NeedsDetailedSandbox, run_onboarding, setup_source

from youju.jobs import public_jobs
from youju.models import AuditResult, Resource, ResourceBundle, ResourceScan
from youju.models.base import utc_now
from youju.onboarding.auditing import AuditLedger, current_source_positions
from youju.onboarding.materials import parse_material
from youju.onboarding.recipes import RecipeAuthor, RecipeRunner, template_candidate
from youju.onboarding.store import json_bytes
from youju.onboarding.temporal_context import current, register_change


def save_event(engine, store, target_url, *, text, suffix="extension", link=True):
    # Reuse the source's already registered saved resource/version fixture boundary;
    # no fetch, URL approval widening or generated code execution is involved.
    from youju.models import MaterialDocument, ResourceKind, ResourceVersion
    from youju.onboarding.store import MATERIAL_PARSER_KEY

    url = BASE + suffix
    raw = (
        f"<html><body><main><h1>招聘公告补充通知</h1><p>{text}</p>"
        + (f'<a href="{target_url}">原招聘公告</a>' if link else "")
        + "</main></body></html>"
    )
    page = parse_material(
        raw.encode(), media_type="text/html; charset=utf-8", url=url, material_key=suffix
    )
    with Session(engine) as session, session.begin():
        resource = Resource(source_id="source", original_url=url, kind=ResourceKind.PAGE)
        session.add(resource)
        session.flush()
        version = ResourceVersion(
            resource_id=resource.id,
            content_sha256="a" * 64,
            body_ref="fixture",
            byte_length=len(raw),
        )
        session.add(version)
        session.flush()
        page.update(url=url, version_id=version.id, resource_id=resource.id)
        document = MaterialDocument(
            version_id=version.id,
            parser_key=MATERIAL_PARSER_KEY,
            body_ref=store.blobs.put(json_bytes(page)),
            format="html",
            status="ok",
        )
        session.add(document)
        session.flush()
        bundle = {
            "id": suffix,
            "source_id": "source",
            "resource_id": resource.id,
            "url": url,
            "status": "complete",
            "errors": [],
            "page": page,
            "attachments": [],
        }
        row = ResourceBundle(
            id=suffix,
            resource_id=resource.id,
            page_version_id=version.id,
            digest=suffix,
            body_ref=store.blobs.put(json_bytes(bundle)),
            status="complete",
            dependencies=[],
        )
        session.add(row)
        session.flush()
        session.add(
            ResourceScan(
                resource_id=resource.id,
                state="done",
                bundle_id=row.id,
                last_version_id=version.id,
                last_document_id=document.id,
                last_success_at=utc_now(),
            )
        )
        return row.id, resource.id


def test_reviewed_extension_preserves_target_jobs_and_changed_event_invalidates_it(tmp_path):
    engine, store, transport, pipeline = setup_source(tmp_path)
    try:
        assert asyncio.run(run_onboarding(pipeline))[-1]["stage"] == "active"
        target = next(b for b in store.bundles("source") if b["url"] == BASE + "one")
        day = utc_now().astimezone(ZoneInfo("Asia/Shanghai")).date()
        new_date = day.isoformat()
        runner = RecipeRunner(pipeline.sandbox, pipeline.models)
        before = asyncio.run(
            runner.run(
                template_candidate(),
                target,
                now_iso=f"{new_date}T12:00:00+08:00",
            )
        )
        assert before["positions"]
        event, resource_id = save_event(
            engine, store, target["url"], text=f"本次招聘报名截止日期延长至{new_date}。"
        )
        # Saving a later notice alone must not supply any new exact job date.
        plain = store.load_bundle(target["id"])
        unrelated = asyncio.run(
            runner.run(
                template_candidate(),
                plain,
                now_iso=f"{new_date}T12:00:00+08:00",
            )
        )
        assert unrelated["positions"] == before["positions"]
        registered = register_change(
            store,
            "source",
            target["id"],
            event,
            effect="extension",
            reviewer="fixture-reviewer",
            reference="independent fixture association",
        )
        value = store.load_bundle(target["id"])
        calls = list(transport.calls)
        result = asyncio.run(
            runner.run(
                template_candidate(),
                value,
                now_iso=f"{new_date}T12:00:00+08:00",
            )
        )
        assert result["schema_version"] == "position-facts-v3"
        assert "validity" not in result and "quality_tier" not in result
        assert len(result["positions"]) == len(before["positions"])
        for old_position, position in zip(before["positions"], result["positions"], strict=True):
            assert position["deadline_on"] == {"value": new_date, "tag": "source"}
            assert position["application_dates"]["value"]["end"]["date"] == new_date
            assert position["application_dates"]["value"]["end"]["year_status"] == "explicit"
            # The reviewed change is temporal, not permission to rewrite other business facts.
            for key in (
                "title",
                "hiring_unit",
                "headcount",
                "employment_type",
                "conditions",
                "condition_logic",
            ):
                assert position[key] == old_position[key]
        receipt = result["fact_dependencies"]
        assert receipt["contract"] == "position-fact-dependencies-v1"
        with Session(engine) as session:
            change = receipt["change"]
            assert change["review_id"] == registered["review_id"]
            assert change["effect"] == "extension" and change["status"] == "current"
            assert change["outcome"] == "applied"
            assert change["target_bundle_id"] == target["id"] and change["event_bundle_id"] == event
            assert change["event_version_id"] == session.get(ResourceBundle, event).page_version_id
            old = session.scalar(
                select(AuditResult)
                .where(
                    AuditResult.bundle_id == target["id"],
                )
                .order_by(AuditResult.created_at.desc())
            )
            assert old is not None and not current(session, old)
        recorded = AuditLedger(store).record(
            value,
            store.status("source")["active_recipe_id"],
            day.isoformat(),
            result,
            generation=20,
        )
        assert recorded["status"] in {"success", "partial"} and not recorded["errors"]
        with Session(engine) as session:
            assert current(session, session.get(AuditResult, recorded["id"]))
        # A dependency claim must bind this exact reviewed event/version, not just its effect.
        for generation, key in enumerate(("review_id", "event_bundle_id", "event_version_id"), 21):
            forged = copy.deepcopy(result)
            forged["fact_dependencies"]["change"][key] = "not-the-reviewed-dependency"
            rejected = AuditLedger(store).record(
                value,
                store.status("source")["active_recipe_id"],
                day.isoformat(),
                forged,
                generation=generation,
            )
            assert rejected["status"] == "review" and rejected["errors"]
        with Session(engine) as session, session.begin():
            session.get(ResourceScan, resource_id).state = "ready"
        changed = store.load_bundle(target["id"])
        stale = asyncio.run(
            runner.run(
                template_candidate(),
                changed,
                now_iso=f"{new_date}T12:00:00+08:00",
            )
        )
        assert len(stale["positions"]) == len(before["positions"])
        assert all(position["deadline_on"]["value"] is None for position in stale["positions"])
        assert stale["fact_dependencies"]["change"]["status"] == "stale"
        assert any(issue["code"] == "ASSOCIATED_CHANGE_UNRESOLVED" for issue in stale["errors"])
        with Session(engine) as session:
            assert not current(session, session.get(AuditResult, recorded["id"]))
            rows = [
                job
                for job in public_jobs(session, day=day, include_partial=True)
                if job["bundle_id"] == target["id"]
            ]
            assert rows and all("needs_review" in job["display_tags"] for job in rows)
        assert transport.calls == calls
    finally:
        engine.dispose()


def test_reviewed_cancellation_is_not_time_expiry_and_skips_detailed_model(tmp_path):
    from youju.models import OnboardingResult, SourceRecipeBinding

    engine, store, transport, pipeline = setup_source(tmp_path)
    try:
        assert asyncio.run(run_onboarding(pipeline))[-1]["stage"] == "active"
        target = next(b for b in store.bundles("source") if b["url"] == BASE + "two")
        day = utc_now().astimezone(ZoneInfo("Asia/Shanghai")).date()
        with Session(engine) as session:
            before = {job["id"]: job for job in public_jobs(session, day=day, include_partial=True)}
            target_ids = {key for key, job in before.items() if job["bundle_id"] == target["id"]}
            assert target_ids
            frozen = copy.deepcopy(session.get(OnboardingResult, "source").summary)
            generation = session.get(SourceRecipeBinding, "source").generation
        calls = list(transport.calls)
        event, _ = save_event(
            engine, store, target["url"], text="本次招聘予以取消。", suffix="cancel"
        )
        registered = register_change(
            store,
            "source",
            target["id"],
            event,
            effect="cancellation",
            reviewer="fixture-reviewer",
            reference="fixture cancellation",
        )
        with Session(engine) as session:
            after = {job["id"] for job in public_jobs(session, day=day, include_partial=True)}
            assert after == set(before)
            retained = [job for job in public_jobs(session, day=day) if job["id"] in target_ids]
            assert retained and all("cancelled" in job["display_tags"] for job in retained)
        bundle = store.load_bundle(target["id"])
        # Unsupported parsing would require detailed extraction without the cancellation.
        # No detailed provider is configured: successful cancellation proves it is skipped.
        runner = RecipeRunner(NeedsDetailedSandbox())
        result = asyncio.run(
            runner.run(
                template_candidate(),
                bundle,
                now_iso=utc_now().isoformat(),
            )
        )
        assert result["status"] == "cancelled" and result["positions"] == []
        assert result["disposition"] == "cancelled" and not result["execution"]["model_used"]
        assert "validity" not in result and "quality_tier" not in result
        receipt = result["fact_dependencies"]
        assert receipt["contract"] == "position-fact-dependencies-v1"
        assert receipt["change"]["review_id"] == registered["review_id"]
        assert receipt["change"]["effect"] == "cancellation"
        assert receipt["change"]["status"] == "current"
        assert receipt["change"]["outcome"] == "cancelled"
        attempt = asyncio.run(
            RecipeAuthor(None, runner).validate_candidate(
                template_candidate(),
                [bundle],
                utc_now().isoformat(),
                1,
            )
        )
        assert not attempt["errors"]
        recorded = AuditLedger(store).record(
            bundle,
            store.status("source")["active_recipe_id"],
            day.isoformat(),
            result,
            generation=20,
        )
        assert recorded["status"] == "cancelled" and recorded["position_count"] == 0
        assert not recorded["errors"]
        with Session(engine) as session:
            assert current(session, session.get(AuditResult, recorded["id"]))
            retained = [job for job in public_jobs(session, day=day) if job["id"] in target_ids]
            assert retained and all("cancelled" in job["display_tags"] for job in retained)
            assert session.get(OnboardingResult, "source").summary == frozen
            assert session.get(SourceRecipeBinding, "source").generation == generation
        assert target["id"] not in {
            row["bundle_id"]
            for row in current_source_positions(
                store,
                "source",
                include_expired=True,
                include_partial=True,
            )
        }
        assert transport.calls == calls
    finally:
        engine.dispose()


def test_partial_scope_missing_literal_target_and_implicit_precedence_are_rejected(tmp_path):
    engine, store, _, pipeline = setup_source(tmp_path)
    try:
        asyncio.run(run_onboarding(pipeline))
        target = next(b for b in store.bundles("source") if b["url"] == BASE + "one")
        day = utc_now().astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
        partial, _ = save_event(
            engine,
            store,
            target["url"],
            text=f"部分岗位报名截止日期延长至{day}。",
            suffix="partial",
        )
        with pytest.raises(ValueError, match="notice-wide"):
            register_change(
                store,
                "source",
                target["id"],
                partial,
                effect="extension",
                reviewer="reviewer",
                reference="fixture",
            )
        no_link, _ = save_event(
            engine,
            store,
            target["url"],
            text=f"本次招聘报名截止日期延长至{day}。",
            suffix="no-link",
            link=False,
        )
        with pytest.raises(ValueError, match="literally link"):
            register_change(
                store,
                "source",
                target["id"],
                no_link,
                effect="extension",
                reviewer="reviewer",
                reference="fixture",
            )
        first, _ = save_event(
            engine, store, target["url"], text=f"本次招聘报名截止日期延长至{day}。", suffix="first"
        )
        one = register_change(
            store,
            "source",
            target["id"],
            first,
            effect="extension",
            reviewer="reviewer",
            reference="fixture",
        )
        second, _ = save_event(
            engine, store, target["url"], text="本次招聘予以取消。", suffix="second"
        )
        with pytest.raises(ValueError, match="supersedes"):
            register_change(
                store,
                "source",
                target["id"],
                second,
                effect="cancellation",
                reviewer="reviewer",
                reference="fixture",
            )
        register_change(
            store,
            "source",
            target["id"],
            second,
            effect="cancellation",
            reviewer="reviewer",
            reference="fixture",
            supersedes=one["review_id"],
        )
    finally:
        engine.dispose()


@pytest.mark.parametrize("effect", ["extension", "cancellation", "all_cancelled"])
def test_full_onboarding_handles_reviewed_events_without_duplicate_job_extraction(tmp_path, effect):
    from youju.onboarding.source_summary import parsing_summary

    engine, store, _, pipeline = setup_source(tmp_path)
    try:
        for _ in range(6):
            state = asyncio.run(pipeline.step("source", limit=50))
            if state["stage"] == "authoring":
                break
        assert state["stage"] == "authoring" and state["state"] == "ready"
        targets = [
            b
            for b in store.bundles("source")
            if b["url"] in {BASE + suffix for suffix in ("one", "two", "three")}
        ]
        if effect != "all_cancelled":
            targets = [b for b in targets if b["url"] == BASE + "one"]
        for number, target in enumerate(targets):
            end = (utc_now() + timedelta(days=10)).date().isoformat()
            text = (
                f"本次招聘报名截止日期延长至{end}。"
                if effect == "extension"
                else "本次招聘予以取消。"
            )
            event, _ = save_event(
                engine, store, target["url"], text=text, suffix=f"reviewed-{number}"
            )
            register_change(
                store,
                "source",
                target["id"],
                event,
                effect="extension" if effect == "extension" else "cancellation",
                reviewer="fixture-reviewer",
                reference="synthetic full-loop association",
            )
        assert asyncio.run(pipeline._classify("source", 50)) == 0
        states = asyncio.run(run_onboarding(pipeline))
        audit = states[-1]["summary"]["audit"]
        assert states[-1]["stage"] == "active", {
            "stage": states[-1]["stage"],
            "state": states[-1]["state"],
            "statuses": audit["statuses"],
            "admission": audit["admission"],
        }
        assert audit["total_announcements"] == 3 and audit["associated_event_count"] == len(targets)
        assert audit["position_count"] == (
            3 if effect == "extension" else 0 if effect == "all_cancelled" else 2
        )
        with Session(engine) as session:
            jobs = list(
                public_jobs(
                    session,
                    day=utc_now().astimezone(ZoneInfo("Asia/Shanghai")).date(),
                    include_partial=True,
                )
            )
            assert len(jobs) == audit["position_count"]
            summary = parsing_summary(session, "source")
            assert summary["statuses"]["associated"] == len(targets)
            if effect == "all_cancelled":
                assert audit["passed"] and audit["statuses"]["cancelled"] == 3
                assert summary["statuses"]["cancelled"] == 3
    finally:
        engine.dispose()


def test_separate_partial_scope_or_conditional_text_is_not_whole_notice_cancellation():
    from youju.onboarding.amendments import amendment_observation

    for heading in ("以下内容仅针对岗位甲。", "如果以下条件成立才执行取消。"):
        document = parse_material(
            f"<html><body><main><p>{heading}</p><p>本次招聘予以取消。</p></main></body></html>".encode(),
            media_type="text/html; charset=utf-8",
            url=BASE + "conditional",
            material_key="conditional",
        )
        assert amendment_observation(document, "cancellation") is None


def test_conflicting_extension_and_cancellation_effects_are_not_silently_selected():
    from youju.onboarding.amendments import amendment_observation

    document = parse_material(
        "<html><body><main><p>本次招聘报名截止日期延长至2026-11-01。</p><p>本次招聘予以取消。</p></main></body></html>".encode(),
        media_type="text/html; charset=utf-8",
        url=BASE + "conflict",
        material_key="conflict",
    )
    assert amendment_observation(document, "extension") is None
    assert amendment_observation(document, "cancellation") is None
