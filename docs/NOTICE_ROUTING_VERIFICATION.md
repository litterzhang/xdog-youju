# Notice-routing and undated-Recipe verification (2026-10-25)

## Implemented scope

- Cost policy v2 recognizes Office-split years, adjacent endpoint heading/date
  blocks, month-bounded application ranges and bounded rounds within rolling hiring.
- Unlabeled page/signature dates and observed HTML/HTTP date metadata approximate
  age only. HTTP Date, capture clocks and synthetic browser modification clocks do
  not stand in for publication. `materials-v4-date-hints` distinguishes the reader;
  saved-original replay can retain an original FetchAttempt Last-Modified without
  downloading or asserting a new observation.
- Recipe runtime v2 requires a distinct `undated_parser.py`. Workspace preparation,
  schemas, native/direct author prompts, source sampling, validation, sandbox module
  allowlists, execution receipts and repair guidance all use the new contract.
- Precise job-date normalization is unchanged. Routing date_hints are removed before
  either script/model sees extraction material. UI/production bindings are unchanged.

## Fixed cohort: the previous 55 undated-high USTC records

For comparability, evaluation stays frozen at **2026-10-24T13:55:03+08:00**. This is
not a new website observation or a claim that the cohort is all source notices.

| New category | Count |
| --- | ---: |
| Fixed expired, quick | 24 |
| Fixed active, detailed-eligible | 1 |
| Undated low priority, quick | 5 |
| Undated high priority, detailed-eligible | 25 |

Thus 29 records now take a model-free quick path. The 25 undated-high records have
23 missing-age fallback reasons and two recent-page-date reasons; high priority
means detail eligibility, not proof of importance/recent publication and not an
unconditional model call. Old cached USTC attempts have no saved Last-Modified, so
no header is invented to improve these counts.

Inspection: `server/.artifacts/notice-routing-20261025/55-routing.json`.
Current-code receipt confirms all 55 decisions still match the saved comparison.

## Actual RecipeService generation, not a host-written source repair

A new private sidecar workspace used native **xdog 2.4.9**, **gpt-6.1-sol**, official
Flow skill/capability and persisted init/build sessions. Init succeeded and build
became `candidate_ready`, revision `c73f983af7c24109a3162ec9f3c2288c`.
The Agent wrote both source scripts; the host-approved Flow remains unchanged.
No old live session, source registry/binding, or accepted Recipe was modified.

Three real saved samples passed host execution validation, with no extraction-model
call on the final samples:

- Until-filled Earth/Space Sciences researcher: one position, unknown calendar
  deadline, explicit until-filled rule.
- Microfabrication engineer: source deadline **2025-04-21**, despite split-year HTML.
- Sports teacher: source deadline **2026-03-15**, despite a separate heading/date.

These are inspected examples, not independently measured source-wide accuracy.
Generation itself made real author-model calls; the offline replay below did not.

## Agent candidate replay on all 55

- General script executed 25 times; dedicated undated script executed 30 times.
- 33 workflows completed: 31 nonempty results with **63 position entries**;
  two completed review results have no positions. Result statuses: success 3,
  partial 28, review 2. Partial capture/field issues remain visible.
- 22 detail-eligible undated executions retain drafts and `MODEL_DISABLED`: their
  script coverage needs detailed extraction, deliberately disabled for this replay.
  These are not website blocks or 22 successful parses. Sixteen are department
  requirement pages; the remaining six are recruitment/aggregate layouts.
- Dedicated-script completed outputs contain 33 of the 63 position entries. Entries
  are not unique real vacancies, headcount, admitted SQL rows or published jobs.
- Zero replay model calls, network requests, downloads, database migration,
  source activation, deployment, callback delivery or WeChat uploads.

Candidate and receipts: `agent-candidate.json`, `generation-status.json`,
`55-agent-replay.json`, `final-receipt-check.json` under the artifact directory above.
Existing source snapshot SHA256 remains
`4feeb8752f4547c769912efdb408259778c50f4eaebd0e37d67d58d3b17f790c`;
root AGENTS.md is unchanged.

## Validation

Default backend tests use only synthetic/offline model boundaries: **1376 passed,
4 opt-in skipped**. All four real Linux sandbox checks pass separately. Final
routing follow-up passes 75 tests; acquisition/reader follow-up passes 44 tests
(overlapping checks, not additive coverage counts). Ruff lint/format checks pass. Acquisition/reader follow-up,
notice-routing regressions and Ruff checks have separate logs under the artifact
root; the final full-suite log is `backend-tests-final.log`. No frontend was changed,
and no browser/WeChat-device or production verification is claimed.
