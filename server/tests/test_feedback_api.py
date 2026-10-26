"""Own-report isolation and explicit human review; source data never changes."""

import copy
import json

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_jobs_api import DAY, saved  # noqa: F401

from youju.api.auth import AuthClient
from youju.models import AuditResult, AuditRun, ParsingFeedback, Position, SourcePipeline


def headers(token="reporter"):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def reporting(saved, monkeypatch):  # noqa: F811
    app = saved.client.application
    app.config.update(
        AUTH_ISSUER="https://auth.example.org",
        AUTH_CLIENT_ID="synthetic",
        AUTH_CLIENT_SECRET="synthetic-only",
    )

    def issuer(request):
        data = json.loads(request.content)
        assert data["client_id"] == "synthetic"
        return httpx.Response(
            200,
            json={
                "sub": data["access_token"],
                "permissions": ["youju:source:manage"]
                if data["access_token"] == "operator"
                else [],
            },
        )

    app.extensions["auth"] = AuthClient(app.config, transport=httpx.MockTransport(issuer))
    monkeypatch.setattr("youju.api.feedback.current_day", lambda: DAY)
    return saved


def payload():
    return {
        "field": "conditions",
        "message": "原文有学位要求，但字段显示未识别。",
        "expected_text": "博士学位",
    }


def test_own_feedback_binds_saved_evidence_and_is_idempotent_without_fixing(reporting):
    client, position = reporting.client, reporting.ids["unknown"]
    url = f"/api/jobs/{position}/feedback"
    assert client.post(url, json=payload()).status_code == 401
    with Session(reporting.engine) as session:
        before = {
            "positions": session.scalar(select(func.count()).select_from(Position)),
            "audits": session.scalar(select(func.count()).select_from(AuditRun)),
            "pipeline": copy.deepcopy(session.get(SourcePipeline, "source").summary),
            "facts": copy.deepcopy(session.get(Position, position).facts),
            "evidence": copy.deepcopy(session.get(Position, position).evidence),
        }
    first = client.post(url, json=payload(), headers=headers())
    assert first.status_code == 201
    report = first.json
    assert report["status"] == "open" and report["resolution_note"] is None
    assert report["position_id"] == position and report["source_id"] == "source"
    assert report["context"]["official_url"] == "https://example.org/unknown"
    assert report["context"]["bundle_id"] and report["context"]["recipe_id"] == "recipe"
    assert report["context"]["audit_id"]
    detail = client.get(f"/api/jobs/{position}").json
    assert report["context"]["field_value"] == detail["facts"]["conditions"]
    assert report["context"]["title"] == "unknown 助理"
    assert report["context"]["facts"] == detail["facts"]
    assert report["context"]["condition_logic"] == detail["facts"]["condition_logic"]
    assert "reporter_sub" not in report and "reviewer_sub" not in report
    retry = client.post(url, json=payload(), headers=headers())
    assert retry.status_code == 200 and retry.json["id"] == report["id"]
    assert client.get(url, headers=headers()).json["total"] == 1
    assert client.get(url, headers=headers("other-user")).json["items"] == []
    assert client.get("/api/admin/sources/source/feedback", headers=headers()).status_code == 403
    with Session(reporting.engine) as session:
        assert session.scalar(select(func.count()).select_from(ParsingFeedback)) == 1
        assert session.scalar(select(func.count()).select_from(Position)) == before["positions"]
        assert session.scalar(select(func.count()).select_from(AuditRun)) == before["audits"]
        assert session.get(SourcePipeline, "source").summary == before["pipeline"]
        assert session.get(Position, position).facts == before["facts"]
        assert session.get(Position, position).evidence == before["evidence"]


def test_review_requires_current_central_permission_and_is_scoped_and_audited(reporting):
    client, position = reporting.client, reporting.ids["future"]
    row = client.post(f"/api/jobs/{position}/feedback", json=payload(), headers=headers()).json
    list_url = "/api/admin/sources/source/feedback"
    assert client.get(list_url, headers=headers("operator")).json["items"][0]["id"] == row["id"]
    url = f"{list_url}/{row['id']}/review"
    change = {"status": "reviewed", "resolution_note": "确认需要核对原文，尚未更改解析器。"}
    assert client.post(url, json=change, headers=headers()).status_code == 403
    assert (
        client.post(
            url.replace("/source/", "/another-source/"), json=change, headers=headers("operator")
        ).status_code
        == 404
    )
    reviewed = client.post(url, json=change, headers=headers("operator"))
    assert reviewed.status_code == 200 and reviewed.json["status"] == "reviewed"
    assert client.post(url, json=change, headers=headers("operator")).status_code == 200
    own = client.get(f"/api/jobs/{position}/feedback", headers=headers()).json
    assert own["items"][0]["resolution_note"] == change["resolution_note"]
    assert client.get(list_url + "?status=open", headers=headers("operator")).json["total"] == 0
    with Session(reporting.engine) as session:
        stored = session.get(ParsingFeedback, row["id"])
        assert stored.reporter_sub == "reporter" and stored.reviewer_sub == "operator"
        assert len(stored.review_history) == 1
        assert stored.review_history[0]["previous_status"] == "open"
        assert stored.message == payload()["message"]
        assert session.get(SourcePipeline, "source").stage == "active"
    assert client.post(url, json=change, headers=headers("operator-now-revoked")).status_code == 403


def test_feedback_accepts_expired_review_candidates_and_rejects_spoofed_context(reporting):
    client, position = reporting.client, reporting.ids["future"]
    url = f"/api/jobs/{position}/feedback"
    for data in [
        payload() | {"field": "script_path"},
        payload() | {"field": "education_text"},
        payload() | {"field": "employment_arrangement"},
        payload() | {"field": "major_text"},
        payload() | {"field": "hiring_unit_text"},
        payload() | {"field": "employer_text"},
        payload() | {"field": "facts_origin"},
        payload() | {"message": " "},
        payload() | {"message": "x" * 1001},
        payload() | {"expected_text": "x" * 501},
        payload() | {"reporter_sub": "operator"},
        payload() | {"source_id": "other"},
        payload() | {"context": {"recipe_id": "fake"}},
    ]:
        assert client.post(url, json=data, headers=headers()).status_code == 400
    assert (
        client.post(
            f"/api/jobs/{reporting.ids['expired']}/feedback", json=payload(), headers=headers()
        ).status_code
        == 201
    )
    with Session(reporting.engine) as session, session.begin():
        parse_id = session.get(Position, position).parse_id
        session.scalar(
            select(AuditResult).where(AuditResult.parse_id == parse_id)
        ).status = "review"
    assert client.post(url, json=payload(), headers=headers()).status_code == 201
    for query in ["page=0", "page=1001", "page=1&page=2", "reporter_sub=other"]:
        assert client.get(url + "?" + query, headers=headers()).status_code == 400
    with Session(reporting.engine) as session:
        assert session.scalar(select(func.count()).select_from(ParsingFeedback)) == 2


def test_explicit_feedback_migration_is_additive_and_idempotent(reporting):
    from sqlalchemy import inspect

    with Session(reporting.engine) as session:
        count = session.scalar(select(func.count()).select_from(Position))
    ParsingFeedback.__table__.drop(reporting.engine)
    runner = reporting.client.application.test_cli_runner()
    path = reporting.client.application.config["DATABASE"]
    first = runner.invoke(args=["feedback-migrate", "--database", path])
    assert first.exit_code == 0, first.output
    assert "No Agent/recipe task" in first.output
    assert "parsing_feedback" in inspect(reporting.engine).get_table_names()
    assert runner.invoke(args=["feedback-migrate", "--database", path]).exit_code == 0
    with Session(reporting.engine) as session:
        assert session.scalar(select(func.count()).select_from(Position)) == count
        assert session.scalar(select(func.count()).select_from(ParsingFeedback)) == 0


@pytest.mark.parametrize(
    "field",
    [
        "title",
        "headcount",
        "hiring_unit",
        "employer",
        "deadline_on",
        "employment_type",
        "work_location",
        "conditions",
        "requirements_text",
        "validity",
        "other",
    ],
)
def test_feedback_fields_capture_v2_context_without_flattening_or_mutation(reporting, field):
    client, position = reporting.client, reporting.ids["future"]
    detail = client.get(f"/api/jobs/{position}").json
    response = client.post(
        f"/api/jobs/{position}/feedback", json=payload() | {"field": field}, headers=headers()
    )
    assert response.status_code == 201
    report = response.json
    assert report["field"] == field
    context = report["context"]
    assert context["title"] == detail["facts"]["title"]["value"] == "future 助理"
    assert context["facts"] == detail["facts"]
    assert context["condition_logic"] == detail["facts"]["condition_logic"]
    assert context["requirements_text"] == detail["facts"]["requirements_text"]["value"]
    assert context["field_value"] == (
        detail["validity"] if field == "validity" else detail["facts"].get(field)
    )
    for key in ("official_url", "bundle_id", "recipe_id", "audit_id", "audit_evaluated_on"):
        assert context[key] == detail[key]
    assert "facts_origin" not in context and "facts_origin" not in context["facts"]
    with Session(reporting.engine) as session:
        assert session.get(Position, position).facts == detail["facts"]
    assert client.get(f"/api/jobs/{position}").json == detail
