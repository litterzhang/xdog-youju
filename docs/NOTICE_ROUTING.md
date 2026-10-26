# Notice cost routing

`youju.notices` is a deterministic, offline cost router integrated before Recipe
execution. It is not a publication-date authority, job-deadline extractor,
cancellation authority, or visibility filter.

## Inputs and approximate age

Use `decide_route(bundle, classification, now=aware_datetime)`. No network, model,
filesystem, wall-clock or Source database access occurs inside this domain.
`notice-routing-v2:old-age-days=N` versions the algorithm and configurable age
threshold (default 365 days). A category selects cost only:

| Category | Mode | Recipe script |
| --- | --- | --- |
| `fixed_active` | detailed | `parser.py` |
| `fixed_expired` | quick | `parser.py` |
| `undated_low_priority` | quick | `undated_parser.py` |
| `undated_high_priority` | detailed | `undated_parser.py` |
| `unknown` | detailed | `parser.py` |

Endpoint recognition tolerates Office HTML years such as `202 5`, Chinese/numeric
full dates, month-bounded application ranges, and adjacent deadline-heading/date
blocks (including `四、报名截止时间`). A fixed endpoint takes precedence over age.
Explicit current-round bounds can coexist with rolling recruitment; a separate
unbounded position scope still prevents blanket fixed-expired routing. This is
approximate scheduling, not scope resolution for job facts.

Without a fixed endpoint, consider dates in readable page blocks, including bare
signatures, not just `发布时间` labels. Also consider separately saved `date_hints`:
HTML date meta tags, content `time` elements, and observed HTTP `Last-Modified`.
Prefer the newest plausible non-future date. Old application-start/interview dates
may lower cost, but never establish fixed expiry. Exclude explicit navigation,
recommendation, copyright, birth, graduation, capture/check clocks and dates in
other-resource URLs. Invalid/future hints are ignored. No capture time, HTTP `Date`,
arbitrary dictionary `updated_at`, or synthetic `document.lastModified` fallback is
used. An observed header can be approximate cache/page age, never proof of when
recruitment content actually changed. Existing captures without saved headers do
not magically gain them.

At age >=365 days, use the undated quick path; a recent hint keeps detail eligible.
Open/application/recruitment intent without a usable age hint remains detail
eligible, not proven recent. Invalid endpoint hints and cancellation candidates
remain unknown/detail for independent assessment. Readable incomplete material is
usable; missing annexes do not veto routing or job/date extraction.

## Boundaries and execution

`RoutingDecision.as_dict()` contains only category, mode, reason, policy_version and
an aware evaluated_at. No hypothesized publication date/deadline is exported. The
runtime removes `date_hints` before either parser or detailed model receives the
material. Exact position dates must independently come from precise applicable
source evidence through the shared position domain. Quick results are not hidden
just because they were cheap.

RecipeService authors a required, separate `undated_parser.py`; it is not a
source-specific cost-policy script. Both source scripts implement
`parse(ctx, bundle, config) -> position-draft-v1` and run in the existing OS sandbox.
No-deadline notices must still yield readable positions/conditions. Honest
`needs_detailed` can request the runtime's detailed fallback, but never force it on
a quick route. The execution receipt identifies the actual script for repair. Parser selection and
model eligibility are native Flow conditions, with a host quick-budget backstop;
authors may adjust graphs within the runtime contract, not replace shared normalization.

See [current workflow](RECIPE_WORKFLOW.md). Inspect a frozen report without model
calls or downloads using `scripts/audit_notice_routing.py --report REPORT --output
NEW_REPORT --now 2026-10-24T13:55:03+08:00`. Compare on the same explicit clock; these
observations are not measured semantic accuracy or production rollout.
