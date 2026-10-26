"""Region-aware observations over real saved-parser output, not URL label guesses."""

import asyncio

from youju.onboarding.classification import Classifier, _template
from youju.onboarding.classification_features import page_shape, prose_blocks
from youju.onboarding.materials import parse_material


def parsed(html):
    page = parse_material(
        html.encode(),
        media_type="text/html; charset=utf-8",
        url="https://example.org/jobs",
        material_key="page-v1",
    )
    page["version_id"] = "page-v1"
    return {"page": page, "attachments": [], "status": "complete", "errors": []}


def test_recommendation_regions_do_not_pollute_detail_but_discovery_stays_intact():
    value = parsed("""<html><body><article><h1>示例学院公开招聘公告</h1>
    <p>本学院现招聘管理助理，报名条件为硕士学位，申请材料请提交报名邮箱。</p>
    <div class="related"><ul>
    <li><a href="/old-1">旧部门招聘取消通知</a></li>
    <li><a href="/old-2">旧部门招聘结果公示</a></li>
    <li><a href="/old-3">旧部门招聘延期通知</a></li>
    </ul></div></article></body></html>""")
    page = value["page"]
    assert len(page["links"]) == 3
    assert all(link["region_kind"] == "recommendation" for link in page["links"])
    assert not any("旧部门" in b["text"] for b in prose_blocks(value))
    assert page_shape(value)["roles"] == ["article"]
    result = asyncio.run(Classifier().classify(value))
    assert result["business_type"] == "recruitment" and result["page_roles"] == ["article"]
    assert any(region["kind"] == "recommendation" for region in page["structure"]["regions"])


def test_mixed_page_can_keep_article_and_list_roles_and_structure_is_not_intent():
    value = parsed("""<html><body><main><h1>示例学院公开招聘公告</h1>
    <p>本院现面向社会公开招聘科研助理，报名需提交应聘申请材料并符合招聘条件。</p>
    <section><div><a href="/a">另一学院招聘公告第一条</a></div>
    <div><a href="/b">另一学院招聘公告第二条</a></div>
    <div><a href="/c">另一学院招聘公告第三条</a></div></section>
    </main></body></html>""")
    result = asyncio.run(Classifier().classify(value))
    assert set(result["page_roles"]) == {"article", "notice_list"}
    assert result["revisit"] == "daily"
    other = parsed("""<html><body><main><h1>示例学院科研会议报道</h1>
    <p>本院成功举办科研会议，与会专家交流科研进展。</p>
    <section><div><a href="/a">最新科研新闻报道第一条</a></div>
    <div><a href="/b">最新科研新闻报道第二条</a></div>
    <div><a href="/c">最新科研新闻报道第三条</a></div></section>
    </main></body></html>""")
    assert _template(value, "article") == _template(other, "article")
    assert asyncio.run(Classifier().classify(other))["business_type"] != "recruitment"


def test_auxiliary_name_cannot_silently_exclude_standalone_vacancy_prose():
    value = parsed("""<html><head><title>科研进展</title></head><body><main>
    <h1>科研成果新闻报道</h1><p>研究人员发表重要科研成果，会议讨论了科研进展。</p>
    <div class="sidebar"><p>本院现面向社会公开招聘科研助理，
    报名需提交申请材料，任职条件为硕士学位。</p></div>
    </main></body></html>""")
    result = asyncio.run(Classifier().classify(value))
    assert result["business_type"] not in {"unrelated", "report", "guide"}
    assert result["requires_review"]
