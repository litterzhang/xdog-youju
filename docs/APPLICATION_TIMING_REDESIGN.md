# Saved-notice application timing acceptance contract

Date: 2026-10-24. Strengthened requirement: **材料不完整不影响岗位解析，更不影响日期解析**.
This document and `server/tests/test_practical_periods.py` specify acceptance
requirements, not a deployment or current-vacancy guarantee. The implementation and offline replay now exercise this contract; no production
rollout or new source Recipe activation is implied.

## Contract boundaries

- Retain `application-period-v2` and its evidence/version/window shape. Host clock
  evaluation becomes `application-evaluator-v3.0`; changing the evaluator version
  must change `rule_key`. Reevaluation needs no model call.
- `status` evaluates the **saved notice's registration deadline**. Always return
  `verification_scope="saved_notice"` and
  `current_hiring_status="not_verified"`, including before a deadline and for
  rolling recruitment. Subsequent events/current vacancies remain unverified.
- Acquisition bookkeeping does not veto readable facts: bundle/page status,
  `page.partial`, truncation, errors and absent/failed attachments must not
  suppress a literal available cutoff or recognized readable position rows.
  The same saved dates/table with and without these flags must yield the same
  application rule, deadline and position fields. Diagnostics may make extraction
  `partial`; they must not invalidate the whole result or discard readable jobs.
- Preserve `material_issues` exactly as observed. `material_timing_impact` is only
  `none` or `non_blocking`, **never `blocking`**. `timing_blockers` independently
  records actual temporal uncertainty: conflicting cutoffs, unresolved extensions,
  explicit delegation/other scopes, rolling exceptions and incomplete per-position
  timing coverage. Fetch failure and primary incompleteness are not timing blockers.
- A common notice-wide cutoff survives a missing application form, job table or
  attachment labelled “报名时间表”. A timetable label alone does not establish
  delegation. Explicit prose such as “其他岗位报名时间以附件为准” does establish an
  unresolved other scope. That semantic blocker persists even when acquisition
  warnings are absent, rather than being caused by missing material.
- Paired/duplicate headings before a literal registration date provide valid
  context. A date range supplies its end only with a clear range connector;
  enumeration punctuation between dates is not range syntax.
- Interview and credential-review dates are not registration cutoffs. Separate
  interview dates and post-deadline interview notifications must not invalidate
  an otherwise supported registration cutoff.
- Preserve inclusive day/minute boundaries: expiry is at the next midnight/minute,
  not a guessed closing hour. Uniform notice-wide wording is not per-position scope.
- Incomplete per-position timing remains unknown for the whole notice because a
  position's date is unknown, not because fetching was partial. Literal year
  omission remains unknown even with a publication year. No available registration
  date naturally means unknown; ignoring acquisition flags does not invent dates.
- Unknown job fields remain `{value: null, tag: "unknown"}` without rejecting the
  known title, date or other readable fields. No missing source facts are invented.
- `open_ended` and `until_filled` preserve literal evidence, have unknown calendar
  expiry with `basis="rolling"`, and never verify current hiring. A historic cutoff
  must not silently erase a rolling exception.
- Independently validate rules: fabricated all-scope coverage or removed conflicts
  must not authorize coarse expiry. Evaluation must not mutate the saved rule or
  an earlier evaluated result.

## Bounded offline cases

The module collects **16 cases**, with small fixed counterfactual checks:

1. Failed application form plus common minute-precision deadline.
2. Absent job table plus paired/duplicate application headings.
3. Missing attachment labelled “报名时间表”, without actual delegation.
4. Clear registration range plus separate interview/credential notifications.
5. Full vs partial/incomplete/failed primary statuses and `page.partial`.
6. Full vs acquisition errors, page errors and truncation.
7. Explicit annex delegation for other positions, with/without fetch warnings.
8. Unassociated registration extension.
9. Conflicting common registration cutoffs.
10. One known and one missing per-position deadline.
11. Interview/credential deadlines without a registration cutoff.
12. Literal year omission despite a dated publication block.
13. Two registration dates without range syntax.
14. Open-ended recruitment, alone and mixed with a historic cutoff.
15. Until-filled recruitment, alone and mixed with a historic cutoff.
16. Old-clock reevaluation, immutability and evaluator-sensitive cache identity.

Cases 5–6 also compare complete position outputs from the same readable table,
check source-known title/deadline and unknown absent fields, validate the full
extraction output, and check that an undated readable job remains usable with an
unknown deadline. Warnings need not leave whole-result status identical.

Fixtures are synthetic pinned saved material. Tests exercise deterministic period
extraction/evaluation/validation, cache identity, position extraction/validation
and material diagnostics. There is no model, external fetch, database, notification
or untrusted code execution.

## Verification and existing-test coordination

Run from `server/` using the existing isolated environment, without installation,
resolution or live-environment synchronization:

```sh
export UV_PROJECT_ENVIRONMENT=.artifacts/recipe-redesign-20261023/venv
PYTHONDONTWRITEBYTECODE=1 uv run --frozen --offline --no-sync python -m pytest \
  -q -p no:cacheprovider tests/test_practical_periods.py
uv run --frozen --offline --no-sync ruff check tests/test_practical_periods.py
uv run --frozen --offline --no-sync ruff format --check tests/test_practical_periods.py
```

These acceptance tests may fail until the separate main patch satisfies the
contract. Do not mark expected failures or weaken semantic assertions.
Old expectations that attachment absence or primary truncation suppresses an
available rule need coordination in that separate task; no existing tests,
original redesign guide or AGENTS file are changed here.

Implementation files are being updated concurrently. Results describe only the
workspace snapshot exercised, not a frozen original implementation, full-suite
verification, production rollout or real hiring verification.

Latest targeted snapshot for this strengthened contract: **14 passed, 2 failed**;
Ruff lint/format checks passed. Both failures are the acquisition-flag
counterfactuals: host date evaluation is preserved, but position extraction still
changes known `deadline_on`/`application_rule` into unknown fields. Those strict
position-equality assertions remain for the main patch to satisfy. No full-suite
or production verification was performed.

## Runtime changes and enablement

The host owns registration-purpose and observed-scope checks, the explicit clock
and the `saved_notice` verification boundary. Sources cannot manufacture a deadline
from capture time, omit a real extension or turn interview/credential dates into
registration dates. Day cutoffs include that day; explicit 24:00 is normalized to
the next midnight. Literal long-term/until-filled declarations remain known rules,
not extraction failures; they cannot prove actual vacancies remain.

The rule shape remains `application-period-v2`. New host output records
`verification_scope=saved_notice`, `material_timing_impact=none|non_blocking` and
actual `timing_blockers`. There is deliberately no material-blocking enum. Material
warnings and `material_status` remain visible, while available-position extraction
can pass source audit with partial acquisition. Publication/adoption and explicit
partial-result visibility are separate existing gates, not parsing failures.

`application-evaluator-v3.0`, toolkit identity and candidate/checker bindings
invalidate earlier rule/test receipts. Regenerate rules and re-run the candidate;
do not just relabel old `coverage`/expiry output. No DB schema migration is needed
for this policy update. Preserve old results and perform approved rollout/reaudit
before switching source admission pointers. No paid model is needed to evaluate a
supported fixed rule or advance its clock.
