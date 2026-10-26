# Reviewed position fact changes

## Interface (shared domain)

```python
from youju.positions.changes import DEPENDENCY_CONTRACT, apply_fact_changes

# Signature: apply_fact_changes(draft: dict, context: dict | None) -> dict
result = apply_fact_changes(draft, context)
# Exactly:
# {"draft": <new canonical position-draft-v1 dict>,
#  "disposition": "cancelled" | None,
#  "dependencies": <receipt envelope> | None}
```

The draft must have the canonical `ExtractionDraft` envelope; malformed envelopes
raise `ValueError`. Both inputs remain unchanged, including nested values. No
routing argument, IO, clock, SDK, model, onboarding import, period extractor or
notice-validity oracle is involved. This module is not runtime/source wiring.

The trusted source adapter (never model output) supplies:

```json
{
  "bundle_id": "original",
  "change": {
    "review_id": "review-1",
    "effect": "extension",
    "target_bundle_id": "original",
    "event_bundle_id": "event",
    "status": "current",
    "supersedes": null
  },
  "change_documents": [{
    "change_review_id": "review-1",
    "event_bundle_id": "event",
    "version_id": "immutable-event-version",
    "blocks": [{"id": "paragraph-1", "text": "本次招聘报名截止时间延长至2026-11-30。"}],
    "tables": []
  }]
}
```

`supersedes` is null or a prior review ID, never the current review ID. The domain
does not resolve review chains, authenticate reviews, discover associations or
verify that a version is actually immutable. Those remain source adapter duties.
Exactly one document must match **both** review and event IDs; a nonempty version
ID, current review and matching target/bundle IDs are required. No matching of
legacy extractor results is performed.

No context, or an object without `change`, yields an unchanged deep copy with
null disposition/dependencies. An explicitly supplied malformed change (including
null), stale review or unusable association yields an unresolved receipt.

## Literal semantic boundary

Only explicit scope phrases `本次招聘`, `本招聘`, `原招聘公告`, `所有岗位`,
`全部岗位` authorize whole-notice action. The scope and action must occur together
in exactly one complete assertion, not separate borrowed headings. Examples:

- `本次招聘报名截止时间延长至2026-11-30。`
- `全部岗位报名统一延期到2026年11月30日。`
- `本招聘取消。` / `现决定取消本次招聘。`

Extension dates are full literal calendar dates (`yyyy-mm-dd`, or explicit
`yyyy年m月d日`), parsed by domain `dates_from_text(..., application_context=True)`.
No relative rule, contextual year, clock or publication-date inference is used.
Invalid dates, extra dates, interview/other-phase text, rolling rules, partial,
conditional, tentative, negated, other substantive changes and conflicting
effects abstain. Extension cannot shorten a known end or precede a known start.
The grammar is deliberately narrow, not a general natural-language recognizer.

Tables are not interpreted. Up to 128 supplied documents, 512 blocks in the
selected document, 8,192 characters/block and 65,536 body characters are allowed.
Explicit navigation/recommendation blocks are excluded; other readable blocks
are checked together for exceptions/conflicts. Missing/truncated/error capture
bookkeeping does **not** veto readable literal semantics. Completeness flags are
not required and cannot establish the effect.

## Effects and uncertainty

Applied extension replaces `dates.end` in common facts **and every position**,
including explicit null and per-position overrides. It sets each
`fields.application_rule` to source `fixed_deadline`. The new date has source tag
and explicit year certainty. Starts, jobs, conditions, other fields, issues and
coverage are retained; absent jobs are never fabricated.

Cancellation and extension are idempotent when the runtime applies the same
context to a base draft and again before final normalization. Literal cancellation
returns disposition `cancelled`, empties positions and sets
`coverage.needs_detailed=false`. It preserves common facts, supported/complete
coverage, other coverage diagnostics and issues. It never promotes incomplete
coverage to complete. Metadata alone never cancels. Runtime must respect the
cancellation disposition and avoid a detail-model call.

Known but unresolved changes preserve all good jobs, starts, rules and other
fields, but explicitly set common and all position ends to null (conservative
whole-notice invalidation, including malformed/mismatched associations). They add
an idempotent issue with `code=ASSOCIATED_CHANGE_UNRESOLVED`,
`owner=position_domain`, `message`, `review_id`, `effect`, and `path`; set
`coverage.complete=false`, `coverage.needs_detailed=false`; and preserve
`coverage.supported`. This is source-review/domain uncertainty, **not** a source
recipe issue or request to repair with a model. Partial changes are not applied
or mapped to guessed job IDs; the conservative invalidation retains the jobs.

Normalization subsequently projects structured dates; this helper never writes
flat deadline projections or decides whether a notice is currently valid.

## Dependency receipt

```json
{
  "contract": "position-fact-dependencies-v1",
  "change": {
    "review_id": "review-1",
    "effect": "extension",
    "target_bundle_id": "original",
    "event_bundle_id": "event",
    "status": "current",
    "event_version_id": "immutable-event-version",
    "outcome": "applied"
  }
}
```

The module exports `DEPENDENCY_CONTRACT = "position-fact-dependencies-v1"`.
Outcomes are `applied`, `cancelled`, `unresolved`. Unknown/malformed identifier
fields are null. A unique matching document supplies its version even if stale
or semantically unsupported; absent/ambiguous matching documents yield a null
version. Receipts do not grant authority; callers own persistence, dependency
invalidation, trusted context assembly, runtime kwargs, and source audits.

## Verification boundary

`server/tests/test_position_fact_changes.py` uses synthetic offline drafts and
parsed documents. Tests cover literal application, cancellation, stale/malformed
association, partial/conditional/negated/conflicting semantics, date safety,
coverage bookkeeping independence, normalization and deep-copy purity. No live
source, real model, paid API, network, deployment or browser/device verification
is part of this sidecar.

### Recorded offline checks

Use the isolated environment, not the live/default environment:

```sh
export UV_PROJECT_ENVIRONMENT=/home/zhang/workspace/xdog-youju/server/.artifacts/recipe-redesign-20261023/venv
export UV_OFFLINE=1
uv run --directory server --frozen pytest \
  tests/test_position_fact_changes.py \
  tests/test_position_domain_v3_contract.py \
  tests/test_position_domain_v3_parsing.py \
  tests/test_position_pipeline.py -q
uv run --directory server --frozen ruff check \
  src/youju/positions/changes.py tests/test_position_fact_changes.py
uv run --directory server --frozen ruff format --check \
  src/youju/positions/changes.py tests/test_position_fact_changes.py
```

Result: **111 passed** for the new change tests alone, **178 passed** for the
selected four-file suite; targeted Ruff lint and format checks passed. An earlier wider exploratory run also included
`test_position_facts.py`: its three tests fail during legacy fixture construction
because degree is a string (`"博士"`) rather than the current v3 typed object.
They do not reach this change helper and were not modified. This is not a claim
that the full repository suite is green. Source association persistence/audits and
runtime integration remain separate main-task verification responsibilities.
