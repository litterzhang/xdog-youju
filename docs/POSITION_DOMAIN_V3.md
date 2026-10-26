# Position domain v3 (local implementation)

This change is limited to `server/src/youju/positions`, new focused tests and this
note. Runtime orchestration, RecipeService, onboarding, storage/API/client changes
are separate integration work. No deployment, download or model execution is
implied. Existing v2 records are not migrated or accepted as v3.

## Public interface

- `parsing.extract_position_draft(bundle, config=None) -> dict`
- `drafts.ExtractionDraft` with `as_dict()` / `from_dict()`
- `drafts.DRAFT_SCHEMA`, `drafts.DRAFT_INSTRUCTIONS`
- `drafts.validate_draft(draft) -> list[issue]`
- `drafts.merge_drafts(script, detailed) -> draft` (invalid drafts raise `ValueError`)
- `normalization.normalize_extraction(draft, *, context=None) -> dict`
- `extraction.validate_output(bundle, result) -> list[issue]`

The package also exports draft/normalization helpers. `extract_position_facts` and
`table_row_facts` remain entry-name conveniences, returning only v3 facts; there is
no v2 extraction path and no old-schema aliases. The current contract is v3 only.
There are no imports of onboarding or RecipeService anywhere in the domain.

Canonical draft (all objects/arrays are ordinary JSON):

```json
{
  "schema_version": "position-draft-v1",
  "notice_facts": {"fields": {}, "conditions": [], "condition_logic": null, "dates": {}},
  "positions": [{
    "key": "d0:t0:r1",
    "fields": {"title": {"value": "科研助理", "tag": "source"}},
    "conditions": [], "condition_logic": null, "dates": {}
  }],
  "coverage": {"supported": true, "complete": false, "needs_detailed": false},
  "issues": [{"code": "MISSING_ATTACHMENT", "message": "Linked attachment not captured"}]
}
```

Inner draft facts may be partial. Common `application_rule`, like other business
fields, belongs in `notice_facts.fields`; position overrides belong in its own
`fields`. Missing fields become unknown. The normalizer never fills missing facts
from a host clock, notice-validity result or `context`; context is reserved and
currently ignored. Inputs are not mutated.

Final normalized output has exactly `schema_version: position-facts-v3`, `status`,
`positions`, `errors`, `coverage`. Runtime-added `routing`/`execution` metadata are
not owned or rejected by domain validation. Neither validity nor quality tier is
a domain output. The validator checks types and business invariants, **not**
material validity, notice cancellation evidence, extraction completeness against a
second extractor, or coarse eligibility. The normalizer itself does not infer
notice cancellation/update associations from headlines.

## Coverage and fallback

- `supported`: at least one recognizable position-table layout was found.
- `complete`: available extraction has no declared gaps or diagnostics. Missing
  acquisition alone may make this false while facts remain usable.
- `needs_detailed`: an actual unresolved layout/critical semantic issue exists.
  Unknown education/experience/age clauses remain readable and set this marker.

Do not request expensive detailed extraction merely because `complete` is false.
The bootstrap also reports `documents`, `tables`, `rows`. These are observations,
not independently proved completeness or accuracy. Unknown layouts, missing titles,
partial-width rows, unresolved requirements and capture warnings remain explicit.
Good rows/fields are retained. Unresolved clauses become typed `other: {text}`;
malformed typed condition values become unknown with errors, not fabricated rules.

## Facts, conditions and employment

`PositionFacts.fields` retains the tagged-value interface. New fields are
`staffing_status`, `application_start_on`, `application_dates`. Source means a
parser-declared original/normalized fact, not human verification; per-field quotes
are not mandatory.

`employment_type`: `personnel_agency`, `labor_dispatch`, `labor_contract`, `other`.
`staffing_status`: `established`, `non_established`, `filing_system`, `quota_managed`.
Unknown is null/tag unknown, not a made-up category. A university/public institution
identity and a contract's duration establish neither status. Explicit negation is
handled; contradictory affirmative modes/statuses become unknown with diagnostics.
Named-position declarations do not become blanket notice-wide staffing claims.

Typed condition values (tag unknown always has null value):

| Type | Known value keys |
|---|---|
| education | `level`: primary/junior_secondary/senior_secondary/vocational/associate/bachelor/postgraduate/master/doctorate/unrestricted |
| degree | `level`: bachelor/master/doctorate/unrestricted |
| study_form | `form`: full_time/part_time/unrestricted |
| major | `names: string[]`, `unrestricted: boolean` |
| work_experience | `minimum_years: number or null`, `kind: string or null` |
| age | `minimum`, `maximum`: integer or null; `reference_date`: ISO date or null |
| graduation_status | `status: string` |
| graduation_year | `years: integer[]` |
| political_status | `statuses: string[]` |
| license | `names: string[]` |
| language | `name: string`, `requirement: string` |
| other | `text: string` |

Conditions retain `id`, `type`, `operator`, `scope`, `value`, `tag`. Logic is bounded,
reference-only `all`/`any` (or a single condition reference), covering all conditions.
Common and local IDs are namespaced `n:` and `p:` during normalization. Common and
position-specific requirements both apply; their internal alternatives are retained.
Education is not inferred from degree. Literal 硕士研究生/博士研究生 education can
be normalized without claiming an independently stated degree requirement. Simple
major lists remain one `in` condition, not multiple conjunctive conditions.

## Application dates

`dates` is a partial object with `start` / `end` endpoints. Omitted position
endpoints inherit common facts; explicit null blocks inheritance. Each known
endpoint has the following structure:

```json
{
  "date": "2026-11-14", "year": 2026, "month": 11, "day": 14,
  "year_status": "explicit", "tag": "source", "time": "24:00"
}
```

- Explicit year: `year_status=explicit`, `tag=source`.
- A same-year syntactic range reuses its written year as source normalization.
- A model/context hypothesis keeps `year_status=inferred`, `tag=ai_inferred`.
- An omitted cross-year endpoint year stays unresolved rather than borrowing a host year.
- Missing year: `year=null`, `date=null`, `year_status=unknown`, `tag=unknown`;
  retain month/day. There is **no host-year substitution**.
- Missing time stays null. `24:00` stays the explicit end-of-day notation, not an
  invented next-day source date. The final flat projection is day precision only.

`deadline_on` / `application_start_on` / `application_dates` are projections
exclusively of extracted structured dates, never an oracle comparison against a
host extractor. Flat official dates alone cannot establish an official deadline.
Common application prose, paired short application headings and recognizable
common/per-position date tables are supported. Split-year whitespace, 报名时限,
`至…止`, and later interview/notification dates have synthetic regressions. Simple
named-position headings attach only to named jobs. Round-specific prose such as
本轮 is retained as a scope issue, not promoted to a universal job deadline.
Conflicting observations clear only disputed dates, not unrelated job facts.
Until-filled/open-ended row overrides block a common deadline for that row.

The optional official `position_code` is a source-neutral tagged string; leading
zeroes already present in parsed material are retained. Codes are not position titles
or globally unique vacancy identities.

`condition_logic=null` can accompany nonempty typed atoms and explicitly means the
relationship is unknown. Consumers must not treat it as AND, OR or unrestricted.

## Detailed fallback merging

Every draft position has a stable, material-local `key`. The bootstrap uses
`d{document}:t{table}:r{row}`. Detailed extraction must preserve existing keys and
typed condition IDs when refining known rows; opaque `other` clauses may be split
into new typed atoms. **Titles are never merge identities**;
duplicate-title jobs and script rows omitted by the model are retained.

Known source fields cannot be silently downgraded by inference; contradictory
source values become unknown with candidate diagnostics. Unknown values may be
filled. Opaque bootstrap clauses and capability warnings are not semantic gold:
detailed extraction may structure them and resolve syntax/scope limitations. The
runtime retains the original gap under `execution.script_issues`, not as a veto on
resolved fields. If known typed interpretations disagree, all known atoms survive
with `condition_logic=null` and explicit review diagnostics; neither the previous
AND nor the new OR is imposed as proven truth. Narratives are retained rather than
silently dropped. Capture incompleteness remains independently recomputed.

## Verification (October 24, 2026)

All commands used the frozen isolated environment, with `--offline` (no downloads):

```sh
UV_PROJECT_ENVIRONMENT=/home/zhang/workspace/xdog-youju/server/.artifacts/recipe-redesign-20261023/venv \
  uv --directory server --offline run --frozen pytest -q \
  tests/test_position_domain_v3_contract.py tests/test_position_domain_v3_parsing.py
```

- 63 focused synthetic tests passed; Ruff check/format passed for the domain and
  those tests. These are model-free unit checks, not browser/device verification.
- Read-only parsing replay used the **183** records marked
  `temporal_evaluation=evaluated` in
  `server/.artifacts/period-redesign-20261024/source-accuracy-v3-final.json` and their
  raw saved `bundle_body_ref` JSON blobs. No model, network, deployment, DB mutation,
  acquisition/reparse, inherited host validity or expected-answer comparison ran.
- Observed 327 available positions; 166 bundles had recognizable layouts; 23 were
  quick-capable and 160 requested detailed semantics/layout handling.
- Common end dates: 142 explicit-year, two missing-year. Final position deadlines:
  171 source-tagged dates, 156 unknown. Zero draft/final validation errors and zero
  uncaught exceptions in that replay.

These are **coverage observations, not date/position accuracy or admission evidence**.
There are no independent labels for the saved replay. The counts include duplicate
positions across distinct saved notices and do not establish current vacancies.

## Remaining limitations

The bootstrap is intentionally not a universal prose/table/attachment parser.
Complex qualifications, exceptions, nested/layout tables, missing titles, multiple
rounds and unresolved date scope need a source adapter/detailed extraction or review.
Literal employment negation is not a general natural-language theorem prover.
`relative_dates.py` now derives supported publication-anchored calendar/workday
rules using literal anchors and explicit reviewed day maps, never a capture clock
or guessed holidays. `changes.py` applies narrowly supported, source-associated
whole-notice extensions/cancellations. Stale or unsupported dependencies are explicit;
automatic cross-notice association is not claimed. Complex qualifications and graph
conflicts retain uncertainty rather than inventing eligibility. See the consolidated
module verification for actual Flow, service, SQL/API/client and saved-source results.
