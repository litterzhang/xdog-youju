# xdog-youju

## 名称与定位

- 项目名：`xdog-youju`，简称 `youju`。
- 中文候选名：职有据，尚未完成名称可用性、商标或小程序重名核查。
- `xdog` 是 AI 驱动产品线，区别于不以 AI 为基础的 `zdog` 普通产品线。
- 定位：事业单位招聘垂直搜索与条件匹配，帮助用户快速发现可能符合条件且仍可报名的岗位，并核对官方依据。
- 产品原则：岗位有出处，匹配有依据，信息变化可追踪。不冒充官方，不保证报名资格或录取结果。

## Accuracy follow-up — October 30, 2026 (offline implementation)

The [follow-up implementation](docs/RECIPE_ACCURACY_FOLLOWUP_IMPLEMENTATION.md)
adds scoped requirement-coverage checks, evidence-bound explicit corrections,
host-selected submission regressions, and separates runtime re-audit from terminal
source revision review. These shared changes have not been deployed or used to
recompute production jobs. Notice cost routing and candidate visibility are unchanged.
The October 26 operational counts below remain historical, not a current status check.

## Latest state — October 26, 2026

The [field-visibility client release](docs/CLIENT_RELEASE_20261026.md) is uploaded
as WeChat development **0.1.18** and published to the existing H5 static site.
Public assets and real list/detail behavior were verified through
`youjuh5-h.942295.xyz`; the bare H5 hostname still returns an outer welcome page.
This client delivery did not restart backend services or submit WeChat review/release.

The [shared runtime rollout](docs/RUNTIME_ROLLOUT_20261026.md) is deployed. It
backed up production storage, removed 336 prior job entities and began saved-material
re-extraction without changing source Recipe artifacts. At 14:36 Asia/Shanghai,
the durable source worker had committed 9/184 announcement results and 54 fresh
public candidates; this is **in progress**, not a completed source-wide audit.
Advanced AI review outcomes and unresolved cases are recorded in the rollout note.
The following earlier-run counts are historical, not the current public total.

The [legacy job cleanup](docs/JOBS_CLEANUP_20261026.md) removed old job entities
and migration copies while retaining original materials and audit history. The
subsequent USTC regeneration completed all **183** classified announcement audit
items after [explicit Recipe profile recovery](docs/RECIPE_PROFILE_RECOVERY_20261026.md).
The runtime is active/ready; saved new candidates are visible under the existing
tagged-display policy, not a guarantee of source or field accuracy.

The [filter/detail API update](docs/JOBS_FILTERS_DETAIL_20261026.md) is deployed.
The latest [client refinement](docs/JOBS_MINI_FILTER_SHEET.md) replaces inline
selectors with a transactional bottom sheet, places business information before
the condition card, and presents declared AND/OR logic as nested groups. WeChat
development version **0.1.17** was uploaded successfully, including the
[condition frame refinement](docs/CONDITION_GROUP_STYLE_20261026.md); this client-only iteration
did not restart services or publish H5. Recipe authoring specifies at most one
complete `other` condition per position and rejects split sample output; that
separate code change does not rewrite existing Recipes/jobs or reload the running
Recipe process automatically.

## Field extraction quality investigation (October 26, 2026)

The [field-quality findings](docs/RECIPE_FIELD_QUALITY_NOTES.md) record measured
condition-structure gaps, missing headcount/location, an AI bypass in the detailed
route, and verification/employment-model issues. The subsequent
[shared Agent runtime correction](docs/AGENT_RUNTIME_FIELD_COMPLETENESS.md) adds
field-completion guidance/checks, scoped diagnostics and bounded stage evidence.
It was subsequently deployed through the authorized
[runtime rollout](docs/RUNTIME_ROLLOUT_20261026.md), with old jobs cleared and
asynchronous re-extraction underway. Source Recipe artifacts are unchanged; this
is not a full personnel-model redesign. The original diagnostic counts are historical.

## Optional AI classification review (bounded live run; automation disabled)

Uncertain resource types can now be reviewed by a high-tier, read-only Agent in
byte-bounded multi-page batches, with independent per-page validation/adoption,
durable source budgets and no human-review gate. This does not inflate the 95%
rules/local coverage target. See [batched classification review](docs/CLASSIFICATION_AGENT_REVIEW.md)
for default costs, machine-unresolved outcomes and explicit enablement. The running
Recipe task is not interrupted or silently given changed classification pins.
An explicit bounded live review was subsequently run for this rollout; ongoing
automatic high-tier review remains disabled. Its results are AI provenance, not
a claim that the 95% cheap-classification coverage target has been met.

## Candidate display policy (deployed October 26, 2026)

All usable canonical job candidates are displayed by default, including expired,
incomplete and review-needed results, with explicit status and provenance tags.
Recipe acceptance still governs activation and repair, not job visibility. See
[Candidate job display](docs/ALL_JOBS_DISPLAY.md) for version selection, filters
and the separate live-deployment boundary. The approved
[production rollout](docs/ALL_JOBS_ROLLOUT_20261026.md) originally served migrated
candidates. Those legacy jobs were subsequently removed at the user's request;
The subsequent Recipe regeneration is complete; incremental monitoring remains
disabled until explicitly enabled by an administrator.

## Current module redesign (code deployed; Recipe cutover pending)

The current implementation separates **notice cost routing**, **position facts**,
**Recipe execution**, and **Recipe authoring**. See
[Module responsibility boundaries](docs/MODULE_RESPONSIBILITY_REDESIGN.md).
The following accuracy-redesign notes are historical where they describe
`validity.py`, `recipe-accuracy-v1`, `quality_tier`, or notice expiry as a job gate.
Those execution contracts are retired, not compatibility paths.

- Recipes contain `workflow.json`, `parser.py`, `undated_parser.py`, and `config.json` and use
  `recipe-runtime-v3`. Both extraction paths produce `position-draft-v1`; the
  shared domain normalizes `position-facts-v3` for every source.
- [Current workflow](docs/RECIPE_WORKFLOW.md) describes generation and per-notice execution.
  The main graph contains three native subflows; source authors may edit the child graphs
  within capability and data contracts. Parser/model branches are explicit Flow edges.
  See [native subflow verification](docs/RECIPE_SUBFLOW_VERIFICATION.md) for SDK, sandbox
  and saved USTC replay boundaries.
  Native extraction defaults independently to `gpt-5.4-nano`; see the
  [live nano replay funnel](docs/NANO_FLOW_VERIFICATION.md) for the 55-case results,
  incomplete outputs and exact verification scope.
  [Repair hardening verification](docs/REPAIR_HARDENING_VERIFICATION.md) records the
  final 55-case replay, lossless failed-refinement candidates, native Agent-generated
  repairs and the 1,000,000-byte extraction input ceiling. It also records unresolved
  cases, usage limits and the recovered H5 verification incident; no new Recipe was activated.
  [Notice routing](docs/NOTICE_ROUTING.md) uses approximate body/signature/observed metadata
  dates for cost only; genuinely undated cases use the separate RecipeService-authored script.
  See [the 55-case comparison and real Agent generation](docs/NOTICE_ROUTING_VERIFICATION.md).
- Five notice categories select quick/detailed **cost** only. Routing never fills
  position dates, determines publication, or changes known facts. Valid quick
  rows are persisted/read just like valid detailed rows.
- Position dates retain year certainty; only resolved source dates populate real
  indexed SQLite Date projections. Conditions have typed education/degree/major/
  experience values and explicit or explicitly unknown logic. Employment mechanism
  and staffing entitlement are separate. Missing material never erases readable facts.
- RecipeService owns generation, tests, sessions and versions. Its optional native
  [runtime-query tools](docs/RECIPE_RUNTIME_EXTENSION.md) use an injected reverse
  extension; neither service opens the other's private database. Source registration
  and admission remain runtime responsibilities.
- This prelaunch change does not preserve old Recipe/position contracts. Existing
  live schema, agents and admissions are **not** silently upgraded. Use an explicit
  fresh/copied storage path and perform re-audit before any approved rollout.

**Historical redesign verification scope:** verification used the isolated
locked 2.4.9 environment without a production rollout. The October 26 rollout
subsequently upgraded `server/.venv`, copied/migrated production storage and
deployed candidate display; it did not activate the experimental Recipe. See [the verified results and USTC funnel](docs/MODULE_REDESIGN_VERIFICATION.md).

## Recipe 准确性重设计指导（提案）

资源分类、报名有效期与 Recipe 生成/验证闭环的重设计要求，见
[Recipe 准确性重设计指导](docs/RECIPE_ACCURACY_REDESIGN_GUIDE.md)。
这是供接手 Agent 审计和设计的指导文档，不代表相关调整已经实现或部署。

The audited response and staged design are in `docs/RECIPE_ACCURACY_REDESIGN.md`.
The xdog dependency lock is upgraded to 2.4.9 with local SDK compatibility fixes.
The three domain redesigns are implemented as **opt-in, offline-verified changes**,
with an accuracy Recipe contract, region observations and frozen case tools. See
`docs/RECIPE_ACCURACY_IMPLEMENTATION.md` for delivered behavior, verification and
qualification limits. Normal source onboarding does not require manual annotations.
Optional host-reviewed semantic cases add independent checks to actual audits,
publication and repair/adoption; see `docs/SEMANTIC_RECIPE_CASES.md`. Archived-only
admission, bounded original rejection views and persistent validation/progress
receipts are documented in `docs/RECIPE_DIAGNOSTICS.md`. Publication-anchored rules,
reviewed workday calendars and original/change associations are documented in
`docs/TEMPORAL_RULES.md`. Explicit copied native cutover and independent
classification measurement limits are recorded in `docs/RECIPE_SDK_CUTOVER.md` and
`docs/REDESIGN_ACCEPTANCE_AUDIT.md`. Verification uses an isolated environment; the
live 2.4.1 environment, data and sessions are not migrated or deployed by this task.


New author defaults use `gpt-6.1-sol` with the official application-scoped Flow
capability and packaged skill, transported through the existing sandbox. Small,
explicit independent model audits use a separate budgeted ledger; they never
become human gold or publication authority. See `docs/SOL_FLOW_INTEGRATION.md`.
Read-only saved-source and new-contract replay funnel definitions are in
`docs/SOURCE_DATA_FUNNEL.md`; historical counts are not current replay results.

Application timing now uses `application-evaluator-v3.0`: acquisition completeness
never vetoes date extraction/computation or available position facts. Capture
warnings remain separate; only actual temporal evidence can leave timing unresolved.
See `docs/APPLICATION_TIMING_REDESIGN.md` and the saved USTC comparison in
`docs/PERIOD_FAILURE_CASES.md`. This is a local change, not a production rollout.

## 用户问题

事业单位招聘包括统一招聘、联考、自主招聘、专项招聘等多种形式。公告、岗位附件、报考指南和更正通知散落在人社部门、主管部门、招聘单位及指定平台，用户难以持续查全、筛选和跟踪。

事业单位发布的岗位不一定具有事业编制；岗位性质必须以官方明确说明为准，未说明时标注待确认。

## 初期范围

1. 从明确、有限的官方来源范围采集公告和附件；首批地域、单位类别及来源名单待确定，不宣称全国全覆盖。
2. 关联公告、岗位表、通用要求、专业目录及后续更正通知。
3. 用户以最少必要的个人条件筛选岗位，结合词法搜索与结构化规则输出结果。
4. 展示岗位详情、报名时间、官方出处、匹配依据和未确认条件；收藏及变更跟踪按后续实现阶段推进。
5. 初期不包含自动报名、录取预测、培训、简历生成或通用聊天机器人。

## 数据真实性与时效性

- 官方公开来源优先；转载仅用于发现线索，不默认替代权威出处。
- 区分公告发布日期、抓取时间、最近成功检查时间、发现变更时间。
- 保留来源 URL、内容版本及可定位的段落、工作表/行号或页码。
- 跟踪延期、取消、人数或条件调整以及附件替换；更新与删除要传播到搜索结果。
- 来源不可访问时不得标记为已更新或已确认失效，应显示最后成功核验时间及待核验状态。
- 尊重访问规则与限流，不绕过验证码、登录、付费墙或其他访问控制。

## AI 与匹配的职责

- 确定性解析优先：HTML、Excel 和可提取文本的 PDF 不默认转为图片识别。
- 使用 xdog 分析新增或变更材料，辅助提取资格条件、关联通用与岗位要求、识别重要变化。
- xdog-flow 可编排解析、提取、校验及复核；无需为每个岗位启动自主 Agent。
- Position fields carry `source`, `ai_inferred` or `unknown` tags. AI inference is allowed but must remain explicit; per-field original quotations are not mandatory. Notice-level expiry still requires independently validated material support.
- 专业、年龄计算基准、应届身份等按本次公告规则处理，不跨招聘统一猜定。
- 明确条件优先规则匹配；语义相近不能直接视为资格相同。
- 结果区分“初步符合已识别条件”“需要进一步确认”“存在明确不符项”，不承诺“保证可报”。
- 用户反复检索优先使用已结构化数据与索引，不必每次调用模型。

## 技术栈与目录

```text
xdog-youju/
├── README.md       # 项目说明
├── AGENTS.md       # 编码代理开发约定
├── client/         # Taro + React + TypeScript，H5 与微信小程序
└── server/         # Python + uv + pyproject.toml + Flask + xdog
```

### 服务端

使用 uv 管理 Python 环境与依赖，在 `server/pyproject.toml` 声明项目配置，采用 Flask application factory。xdog 依赖按实际用到的包引入，例如 xdog-ai、xdog-flow；不预先引入全套组件。版本、数据库及具体索引实现待初始化时核验后选择，小规模优先简单方案。

凭据仅在服务端读取，不提交 Git，不写入日志或模型输入。个人项目不得使用雇主内部代码、数据、账号额度或非公开设计。

### 客户端

使用 Taro + React + TypeScript，共用前端构建 H5 与微信小程序，样式使用 SCSS。构建走官方 Taro 工具链；登录、订阅提醒、分享等平台能力单独适配。H5 构建成功不代表微信真机通过。

## UI 方向

See `docs/UI_REFINEMENT.md` for the current flat-list UI, standalone feedback and
run-scoped incremental-result presentation.

**极简、冷淡、现代、黑白灰。** 信息与阅读效率优先，而不是仪表盘式装饰。

- 白色或浅灰背景，深色主文字，灰色次级文字，细灰分隔线。
- 黑色主操作按钮、白色文字；次要操作采用文字或轻描边。
- 留白充足，排版清晰，组件扁平；避免过度卡片化、厚重阴影、大圆角堆叠。
- 不使用彩色品牌点缀、渐变、玻璃拟态、装饰插画、emoji 或无意义动效。
- 状态依靠明确文本、图标与层级区分，不仅靠颜色。灰色文字仍需可读对比度，保留焦点、加载、错误、空状态和触控可用性。
- 重要信息：单位与岗位、地点、报名截止、匹配疑点、官方来源；不以视觉极简为由隐藏这些内容。
- 中文产品界面；代码标识使用英文。

## 验证目标与当前状态

先用 20–30 份公开且许可合适的官方材料验证字段提取、证据定位、条件匹配和变更跟踪，再决定上线范围与收费点。收入目标是低成本验证真实付费需求，不默认规模化创业；任何付费或主动提醒方案需核验平台规则。

The application foundation is initialized: a Flask application factory with a
JSON health endpoint, a shared Taro React client with a Chinese monochrome
empty-state page, dependency lockfiles, and local-only tests. SQLite/SQLAlchemy
models now cover organizations, regions, sources, seeds, resources, discovery
provenance, fetch attempts, and immutable content versions. Local CLI commands
can initialize SQLite and import the USTC candidate source catalog without
fetching URLs. See `docs/DATA_MODEL.md`. HTML crawler contracts now define
`static`, `dynamic` (headless CDP), and `headed` (visible browser) modes. The
static HTTP adapter and shared CDP browser adapters are implemented with
origin/IP checks, robots handling, timeouts and body limits. Browser rendering
uses dedicated sessions and a restricted resource broker. See
`docs/CRAWLERS.md`. A bounded async POC now queues organization pages, stores
captures and original versions, defers static failures to dynamic acquisition
on a later pass, and extracts evidence-backed USTC position-table rows. Discovery
stays within a reviewed organization root domain. See `docs/POC.md` for commands
and the recorded run. A five-stage source onboarding pipeline now acquires pages and attachments into
versioned resource bundles, classifies saved material, supports opt-in xdog recipe
authoring, audits all identified announcements, and finalizes admission. Incremental monitoring is separately opt-in. Generated scripts execute in an isolated child; source
workflows receive already-parsed materials. See `docs/ONBOARDING.md`.
Source-recipe authoring now runs through a separate RecipeService and an explicitly
started native `recipe-task-worker`. The source server requests a persistent workspace,
prepares inputs and queues a job; a later `source-step` imports the worker candidate
and continues the full audit. The official xdog coding Agent receives the complete
installed workflow skill in its system prompt and isolated Bash/filesystem tools.
There is no author maximum-turn, total-token or whole-run time cap; extraction and
classification budgets and tool safety limits remain. Work files, validation history
and paginated job progress survive failed runs. The sidecar uses its own SQLite DB,
not the source DB lock; only the authenticated managed execution path remains. Both manual and incremental repair preserve active admission while auditing a replacement. See
`docs/RECIPE_SERVICE.md` for two-terminal operation, cancellation and explicit recovery.
The **managed** RecipeService integrates official xdog native skills, versioned
Agent snapshots and auto compaction. InputCompleted freezes material and queues init;
the Agent creates workspace AGENTS.md, persists its conversation and closes. RPC
build/repair requests restore that session in a fresh runtime. Safe checkpoints have
an awaited persistence barrier; interrupted recovery snapshots are isolated from
resumable state. Candidate submissions validate real files and up to three supplied
samples before a reliable callback outbox reports completion. Callback delivery and
recipe acceptance remain separate from full-source audit and activation. Internal
RPC is opt-in and authenticated; GetTaskResult returns immutable saved artifacts.
Source CLI workspaces now use the managed path only; incompatible existing published
candidates require a fresh explicit storage path and no live source is silently converted. See `docs/RECIPE_CONTROL_PLANE.md` for native operation,
migration and evidence boundaries. The historical native replay used xdog 2.4.1. The current lock pins 2.4.9; see
`docs/RECIPE_ACCURACY_REDESIGN.md` for isolated verification and rollout boundaries.
A real managed USTC replay verified independent-process init/build/repair using the
same native session, Agent-owned source changes, 11 evidence-backed sample positions
(all unknown validity), authenticated result retrieval and five loopback HTTP callbacks.
It reused saved resources and did not activate or rewrite the original source DB;
see the native verification section in `docs/RECIPE_CONTROL_PLANE.md`.
A later Agent-owned validity-script repair separates dated expiry from the declared
application period and current hiring verification. Explicit 长期有效 now emits
application_period.kind=open_ended with literal evidence and not_verified hiring
status, rather than losing the known rule inside an unknown expiry result. The
same saved USTC sample retained all 11 positions. A later independent replay ran all
184 identified saved notices: 16 complete, 147 partial, 19 review and two failed,
with 326 stored historical position rows. Two previously parsed notices regressed,
so the managed candidate was not activated; see `docs/APPLICATION_PERIOD.md`.
The later real USTC source-wide baseline
completed its identified-announcement audit: 14 successful announcements stored
104 evidence-backed positions and the source was activated under the documented
per-resource quarantine policy. The first real incremental recheck also succeeded.
Revised classification covered 1,250 of 1,312 pages through rules/local inference
(95.27%, not accuracy). Deferred dynamic retries and the full current audit also
completed, bringing accepted positions to 126, all with unknown hiring validity;
see `docs/USTC_FULL_POC.md` for measured results and remaining limitations, and
`docs/USTC_LIVE_VERIFICATION.md` for preserved earlier failed experiments.
A follow-up fixed validity loss caused by missing attachments and extraction
failures. Saved-material reanalysis now reports 137 expired by observed deadline,
four not expired and 43 unknown across 184 notices, with explicit evidence scope.
Readable partial notices continue parsing. A subsequent status fix separates
parsed available material from content review: 133 notices now have incomplete-
material status rather than review; eight require content review and 27 remain
failed. Evidence-valid partial positions can be inspected with an explicit CLI
opt-in; warnings and validity stay visible. See `docs/AUDIT_STATUS.md`.
The baseline was reused; only one final incremental request was made. See
`docs/USTC_VALIDITY_FOLLOWUP.md` for limitations and the remaining extraction failures.
The standard audit-driven `source-repair` flow is now implemented and verified.
The official xdog Agent modified the source workflow/config, repaired three selected
not-expired failures and passed the full 184-notice audit without regressing the
149 previously parsed bundles. Final dispositions are 16 complete, 141 parsed with
incomplete material, seven review and 20 failed. See `docs/SOURCE_REPAIR.md`; these
are Agent-owned source changes, separate from earlier host status fixes.
Page classification now uses rules, a local lightweight JSON model and a durable
source-wide LLM dispatch budget. Coverage is not accuracy; see
`docs/PAGE_CLASSIFICATION.md`. Expiry runs in a generated script, not a per-page
Agent decision; `docs/WORKFLOW_EXPIRY_DEBUG.md` documents branch tracing.
The first server/client admin version now integrates a dedicated zdog-auth machine
client following the local Xiaoman contract. Verified identities and centralized
permissions come from zdog-auth userinfo, following zdog-mp; the shared Taro client shows a permission-gated admin entry,
region/organization/source forms, explicit official-source approval and onboarding
status/summary views. HTTP onboarding only queues work. An opt-in foreground
source-worker advances started sources through the existing pipeline and separate
recipe worker; no paid model is launched by the API. See `docs/ADMIN_V1.md` for
configuration, worker commands and verification boundaries. A public saved-job list/detail query now supports literal search, pagination,
expiry filtering and explicit partial-material inspection. OCR, automatic
cross-announcement corrections and eligibility matching remain unimplemented. The explicit `auth-provision` CLI declares `youju:source:manage` and the
`youju-admin` role without assigning users; normal startup never provisions.
The central permission declaration was verified on October 14, 2026; real
user grants and WeChat device login remain unverified.
The API companion source daemon and independent Recipe service are now enabled
and use managed RPC, persisted init/build sessions, callbacks and full-source audit.
See `docs/MANAGED_DEPLOYMENT.md`; submitted sources can invoke live models.
The API and static H5 are deployed as described below. WeChat code versions 0.1.0 through 0.1.3
were uploaded with explicit approval through the official Taro CLI; no review or
production release was submitted. See `docs/WECHAT_UPLOAD.md`.

See `docs/DEVELOPMENT.md` for setup, development commands, security boundaries,
and recorded verification limitations. A successful health response only
confirms service liveness; it does not report the state of the separate POC database.

## Nginx / Static H5 Configuration

The prepared site uses `youju.942295.xyz` for the local API upstream and
`youjuh5.942295.xyz` for built H5 files. Client builds target the public API origin
`https://youju-h.942295.xyz`. See `docs/NGINX_DEPLOYMENT.md` for paths, CORS/Host
settings, SELinux labeling, syntax verification and the explicit installation gate.
With explicit approval, the site was installed/reloaded and the enabled
`youju-api.service` now serves a loopback-only production Waitress API. The existing
public H5 ingress is `https://youjuh5-h.942295.xyz`; the requested non-`-h` public H5
address still reaches the outer gateway's default page and needs external routing.
A fresh empty product DB was initialized without importing or changing POC data.
Administrator grants remain empty and real login/device behavior remains unverified.
The successful official WeChat upload and experience-version selection boundary are
recorded in `docs/WECHAT_UPLOAD.md`.

## Client UI and Native Navigation

The redesigned client uses native 首页 / 岗位 / 我的 tabs, monochrome grouped cards
and native page titles without duplicate in-page headers. Authorized users enter
source management only from 我的; organization, source, official-evidence review and
status are separate onboarding pages. See `docs/CLIENT_UI_V2.md` for flow and staged
verification. This redesign was uploaded as WeChat version 0.1.1 on October 14, 2026, but has
not replaced the live H5 build. That upload predates the now-implemented saved-job
query and independent monitoring pages; their later rollout is recorded below.


The latest WeChat upload is **0.1.8**, adding structured position facts, recipe
revision/repair status and explicit contract-migration recovery. API/source/Recipe
services and H5 have been deployed. Actual device behavior and experience-version
selection still require verification; see the October 15 rollout section below.


Blocked-source status now includes confirmed recovery and audit-driven Agent repair.
Production/default discovery depth is 100 with a hard 100-depth boundary. The live
USTC inventory resumed with all 480 previously completed slots preserved; a later
read-only audit confirmed acquisition remained on employment.ustc.edu.cn. Other
USTC host links remain unscheduled discovery candidates. See the recovery section
in `docs/MANAGED_DEPLOYMENT.md` for measured checks and remaining boundaries.


WeChat 0.1.5 includes same-identity automatic session recovery and packaged left/right
navigation arrows. Expired legacy token-only sessions require one explicit login
to bind the subject; H5 continues explicit login without storing passwords. See
`docs/CLIENT_UI_V2.md` for tested concurrency/safety behavior and device limitations.


Onboarding completion is now frozen separately from opt-in incremental monitoring,
which defaults off and has an independent administrator page and run history.
The 岗位 tab reads audited jobs rather than a static empty state, retaining original
evidence, material warnings and honest hiring uncertainty. Existing databases need
the explicit additive `source-lifecycle-migrate` command before updated services
restart. See `docs/LIFECYCLE_AND_JOBS.md` for APIs, rollout and offline USTC-copy
verification; code/build verification is not a production deployment or WeChat upload.


The subsequently approved live rollout on October 14, 2026 migrated the product DB
without dropping resources or positions, restarted API/source services, and published
the new H5 static build. Recipe service stayed independent; monitoring remains off.
Public HTTP and real H5-browser checks returned 126 default saved jobs and 160 with
partial-material opt-in, not verified current vacancies. WeChat 0.1.6 uploaded at
16:40:37 Asia/Shanghai; no review/release was submitted. The verified H5 ingress is
`https://youjuh5-h.942295.xyz`; the non-`-h` outer-gateway route remains unresolved.
See `docs/LIFECYCLE_AND_JOBS.md` and `docs/WECHAT_UPLOAD.md` for receipts and boundaries.


A subsequent client refinement routes job details through native navigation/back
and combines literal filters with search. An authenticated, evidence-bound parsing
feedback path lets users report discrepancies and administrators record reviews;
it never auto-runs a Recipe Agent or edits positions. USTC diagnosis distinguishes
missing separate education fields from captured doctoral-degree requirements.
See `docs/PARSING_FEEDBACK.md`. These later changes require the explicit feedback
table migration and a new approved client rollout; the earlier 0.1.6 predates them.


The approved October 15 upload rolled out feedback storage and the updated API,
preserving the existing incremental switch and active recipes. Official Taro CI
uploaded WeChat **0.1.7** at **08:45:13 Asia/Shanghai** (451.318359375 KB). No Recipe
repair, role change, review/release or H5 static replacement occurred. Select the
new experience version in the console if needed; actual device behavior remains
unverified. Receipts and boundaries are recorded in `docs/WECHAT_UPLOAD.md`.

## Structured Position Contract

The position refactor now uses one `position-facts-v2` contract throughout Recipe
extraction, SQLite/SQLAlchemy storage, public jobs, client rendering and feedback.
Business fields have value/source-inference-unknown tags; recruitment conditions
are independent array entries with explicit ALL/ANY relationships. Employment type
means personnel management (事业编制 / 人事代理 / 劳务派遣 / 劳动合同制), not contract
duration. Original evidence is not mandatory per field; notice-level expiry still
uses independently validated material and an explicit clock. Inferred deadlines
cannot hide potentially active jobs.

This is an intentional breaking POC change: old flat contracts and old position
schemas are not supported or automatically migrated. Initialize a fresh database
path and regenerate/audit source recipes before a new end-to-end run. Existing data
is not automatically deleted. The implementation is not deployed and no live
source/model run is implied. See `docs/POSITION_MODEL.md` for the schema, commands
and remaining boundaries; Recipe service now uses immutable revisions, explicit source bindings and unified repair.

## Shared Page Classification

Classification now separates page form from business intent using a reusable
CPU-only two-head classifier. Supporting guides/reports are distinct from vacancy
announcements. Source CLI/worker loading rejects binary single-source and synthetic
smoke artifacts. Host checks retain uncertainty and mixed offers; scheduling and
link discovery are not learned decisions. Unknown lists retain daily monitoring.

Five offline Flask commands export saved material, enforce source/template/content
holdouts, train candidates, evaluate model-only results and replay the complete
rules/local/host path. Artifact/label provenance is explicit; no executable pickles,
paid teacher calls, automatic activation or accuracy guarantees are introduced.
Real cross-source quality still requires audited multi-source data. See
`docs/SHARED_PAGE_CLASSIFIER.md`; older USTC coverage numbers are historical, not
shared-model accuracy.


## Automatic Incremental Recipe Repair

New or materially changed notices with recognized parsing failures now create durable
repair requests independently of onboarding and non-executing user feedback. Managed
`StartRepair` pins a new frozen input revision and the accepted recipe base, restores
the source Agent session, and reports a candidate without activating it. A separate
saved-source regression audit must repair the targets and preserve healthy position
counts before an atomic recipe/generation switch. Healthy jobs and frozen baseline
admission remain visible during repair. Expired notices are excluded from automatic
Agent authoring; unknown validity remains eligible for full extraction. Provider,
policy and infrastructure failures are not treated as recipe bugs.

This path is verified with offline synthetic materials/runtime doubles, not a new
live USTC run. It needs the current source schema and configured managed service/worker;
no existing database reset, deployment or client upload occurred. See
`docs/INCREMENTAL_REPAIR.md` for lifecycle, idempotency and remaining boundaries.


## Recipe Revision and Binding Refactor

Recipe generation now has one managed RPC/native-worker execution path. The old
legacy worker and implicit feedback-to-repair API are removed. Immutable RecipeRevision
records distinguish generated artifacts from task status and full-source acceptance;
SourceRecipeBinding owns candidate/active IDs and publication generation. Manual audit,
first-onboarding candidate repair, incremental failure repair and reviewed feedback retries
share the same fixed-input, full-audit and adoption path. Good admitted jobs remain
available during repair; rejected candidates and review decisions stay immutable.

Full-audit review intents and repair synchronization states survive lost RPC replies.
Admin status exposes revision IDs and bounded repair states, with explicit maintenance
for eligible active sources; human feedback remains non-executing. Existing source
pointer schemas and pre-revision published sidecar candidates need fresh explicit paths,
not silent data conversion. Current commands and boundaries are in
`docs/RECIPE_SERVICE.md`, `docs/RECIPE_CONTROL_PLANE.md` and `docs/SOURCE_REPAIR.md`.
Historical receipts above describe earlier code, not the current worker/API contract.
The refactor is not deployed; no live models, USTC regeneration or WeChat upload is implied.


## Authorized Migration and Deployment — October 15, 2026

The approved breaking-contract rollout now runs on `server/youju-recipe-v4.sqlite3`
and a new schema-v4 sidecar. Registration, 1,313 completed acquisition slots and
1,345 saved versions/documents/bundles were preserved without re-downloading.
Old records, including 655 historical positions and original authoring history,
remain archived. They are not invented as new tagged outputs or reviewed revisions.
USTC requires explicit saved-material recovery, new Agent generation and a full
source audit; current public jobs correctly remain empty until that completes.
Monitoring is disabled and no model task was submitted by the rollout.

All three services were restarted, the new H5 was published to the existing root,
and official Taro CI uploaded WeChat **0.1.8** at **15:02:03 Asia/Shanghai**. Public
API/protected-route and actual H5 checks passed. No review/release was submitted;
experience selection and device behavior remain unverified. The verified public
H5 is still the `-h` ingress; the non-`-h` gateway route was not changed. See
`docs/RECIPE_ROLLOUT_20261015.md` for backups, exact migration policy and receipts.
Earlier undeployed statements above refer to implementation verification before
this explicitly approved cutover, not the current rollout status.
