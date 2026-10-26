"""Focused safety and durable-cost checks; no model service is contacted."""

import asyncio

from sqlalchemy.orm import Session
from test_onboarding import setup_source
from test_sourcekit import FakeModel, bundle

from youju.models import ResourceScan, SourcePipeline
from youju.onboarding.classification import Classifier
from youju.onboarding.classification_budget import ClassificationBudget, covered


def test_page_type_rules_do_not_confuse_navigation_or_reports_with_vacancies():
    page = bundle(title="示例大学人才招聘网")
    page["page"]["blocks"] = [
        {
            "id": "nav-js",
            "text": "聘期制科研岗",
            "locator": "body > ul:nth-of-type(1) > li:nth-of-type(1) > a:nth-of-type(1)",
        },
        {
            "id": "contact",
            "text": "地址：某地示例大学 联系电话：000000 邮箱：office@example.org",
            "locator": "body > div:nth-of-type(3)",
        },
    ]
    for index, label in enumerate(
        ("专项招聘岗位", "人工智能与数据科学学院", "化学与材料科学学院", "环境科学与工程系")
    ):
        page["page"]["blocks"].append(
            {
                "id": f"link-{index}",
                "text": label,
                "locator": (
                    f"body > ul:nth-of-type(1) > li:nth-of-type({index + 2}) > a:nth-of-type(1)"
                ),
            }
        )
        page["page"]["links"].append(
            {"label": label, "url": f"https://example.org/n{index}", "is_attachment": False}
        )
    model = FakeModel()
    result = asyncio.run(Classifier(model).classify(page))
    assert result["page_roles"] == ["navigation"] and result["business_type"] == "recruitment"
    assert covered(result) and model.tiers == []

    report = bundle(
        title="学校召开人才专项工作会议",
        texts=[
            "会上介绍了人才招聘政策与工作安排，相关部门介绍了过去一年的人才服务工作，并就下一阶段工作交流意见。"
        ],
    )
    result = asyncio.run(Classifier(model).classify(report))
    assert result["page_roles"] == ["article"] and result["business_type"] == "report"
    guide = bundle(
        title="示例大学人才招聘网",
        texts=[
            "本指南提供进校后常规工作与生活信息，包括报到流程。",
            "为配合优秀人才引进工作，人力资源部推出《新进指南》，介绍政策和生活资源。",
        ],
    )
    result = asyncio.run(Classifier(model).classify(guide))
    assert result["page_roles"] == ["article"] and result["business_type"] == "guide"
    assert result["revisit"] == "weekly" and covered(result)
    vacancy = bundle(
        title="【科技日报】公开招聘研究助理公告",
        texts=["招聘条件：硕士。报名方式：向单位提交申请材料。"],
    )
    assert asyncio.run(Classifier(model).classify(vacancy))["page_roles"] == ["article"]

    class RelevanceOnlyModel:
        key = "0" * 64

        def predict(self, text):
            return {"label": "recruitment", "confidence": 0.99, "margin": 0.98}

    discussion = bundle(
        title="人才发展交流纪实",
        texts=[
            "交流介绍了本校人才引进工作的历史与成果，并总结师资服务工作经验，讨论人才服务政策。"
        ],
    )
    unresolved = asyncio.run(Classifier(local_model=RelevanceOnlyModel()).classify(discussion))
    assert unresolved["requires_review"] and not covered(unresolved)
    mixed = bundle(
        title="人才宣讲茶话会",
        texts=["会议将介绍人才招聘政策。学院现急需引进核安全方向的人才，具体资格条件有待公布。"],
    )
    unresolved = asyncio.run(Classifier(local_model=RelevanceOnlyModel()).classify(mixed))
    assert unresolved["requires_review"] and not covered(unresolved)
    empty = bundle(title="重要科研新闻")
    empty["page"]["blocks"] = []
    result = asyncio.run(Classifier(model).classify(empty))
    assert result["requires_review"] and not covered(result) and model.tiers == []


def test_llm_budget_reservations_are_atomic_and_survive_worker_restart(tmp_path):
    engine, store, _, pipeline = setup_source(tmp_path)

    async def inventory():
        for _ in range(4):
            await pipeline.worker.run_once("source", 50)
            store.build_bundles("source")
            if store.finish_inventory("source"):
                break

    asyncio.run(inventory())
    pages = store.bundles("source")
    assert len(pages) == 4
    tiny = ClassificationBudget(store, "default-test", 0.05)
    assert not asyncio.run(tiny.reserve(pages[0], "small"))
    # An explicitly larger test fraction allows exactly one reservation for four pages.
    budget = ClassificationBudget(store, "bounded-test", 0.25)

    async def reserve():
        return await asyncio.gather(
            budget.reserve(pages[0], "small"), budget.reserve(pages[1], "large")
        )

    assert sorted(asyncio.run(reserve())) == [False, True]
    restarted = ClassificationBudget(store, "bounded-test", 0.25)
    assert not asyncio.run(restarted.reserve(pages[0], "large"))
    revised = ClassificationBudget(store, "new-rule-profile", 0.25)
    assert not asyncio.run(revised.reserve(pages[1], "small"))
    revised_summary = revised.summarize("source")
    assert revised_summary["llm_reserved_calls"] == 1
    assert revised_summary["llm_profile_reserved_calls"] == 0
    with Session(engine) as session:
        usage = session.get(SourcePipeline, "source").summary["classification_budgets"][
            "bounded-test"
        ]
        assert usage["reserved"] == usage["limit"] == 1 and usage["denominator"] == 4
    summary = restarted.summarize("source")
    assert summary["cheap_coverage"] == 0 and not summary["target_met"]
    assert summary["unassessed"] == 4
    for page in pages:
        store.assess(page, asyncio.run(Classifier().classify(page)), "bounded-test")
    assert restarted.summarize("source")["target_met"]
    with Session(engine) as session, session.begin():
        session.get(ResourceScan, pages[0]["resource_id"]).state = "ready"
    pending = restarted.summarize("source")
    assert pending["pending_acquisitions"] == 1 and not pending["target_met"]
    engine.dispose()
