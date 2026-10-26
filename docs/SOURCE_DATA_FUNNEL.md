# Saved source data funnel (offline reporting)

This is a read-only measurement tool, not a crawler, model replay executor,
recipe migration, activation, accuracy evaluation or deployment. Snapshot labels
are **saved observations**, never represented as new-contract predictions.

## Command

All input paths and the evaluation clock are explicit. Use a consistent,
standalone SQLite backup made by the caller, with the current application schema
and its saved content-addressed blobs. Do not point this at a changing service DB.
Nonempty `-wal` sidecars are rejected: SQLite immutable reads must not silently
ignore pending WAL. The tool uses SQLite `mode=ro&immutable=1`, `query_only`, an
explicit read transaction, no ORM flush/commit, and no storage constructors that
create directories. JSON is emitted to stdout; input files are not updated.

```sh
UV_PROJECT_ENVIRONMENT=/home/zhang/workspace/xdog-youju/server/.artifacts/recipe-redesign-20261023/venv \
  uv --directory server run --frozen --offline python scripts/report_source_funnel.py \
  --snapshot /audit/source-snapshot.sqlite3 --blobs /audit/saved-blobs \
  --source SOURCE_ID --clock '2026-10-24T12:00:00+08:00'
```

Alternatively `--clock-file /audit/clock.txt` reads a plain UTF-8 ISO-8601 instant
with timezone offset. There is no implicit system clock. The command does not
fetch URLs, execute saved recipes, call models, write a report file or initialize
missing storage. An incompatible schema fails instead of being migrated.

## Counting contract

Position counts mean distinct parsed position entries, **not** total advertised
headcount. `not_expired` means the supported registration cutoff has not elapsed;
it does not prove a vacancy remains or that a particular applicant qualifies.

- `denominators.unique_resources`: source-owned resource identities, including
  assets, attachments and resources without scan slots. Not discovery-edge,
  fetch-attempt, historical version or audit-row counts.
- `latest_versions` / `latest_material_documents`: unique non-null current scan
  pointers. These are saved pointer counts, **not** a claim that each blob is
  readable or that every discovered resource has been acquired. `current_bundles`
  is narrower: readable, correctly bound bundles with current dependencies.
- Classification uses only the effective pipeline classifier key and current
  bundle/material. Missing classifications never fall back to older classifiers.
  Notice lists and recruitment articles are **overlapping sets**. Both their
  intersection and the broader notice-list/recruitment-candidate intersection
  are explicit. `recruitment_candidates` is the union of recruitment articles
  and primary recruitment attachments, not their sum. A mixed article/list is
  counted once in that union. These are classifier observations, not gold labels.
- Recruitment candidates have disjoint `not_expired`, `expired`, `unknown`
  buckets. `cancelled` is a fourth, separate disposition. These four counts sum
  to recruitment candidates. Cancellation and correction **pages** are reported
  separately and are not automatically treated as cancelled original notices.
  Classification review remains visible and cannot admit positions.
- Only the latest audit no later than the explicit Shanghai evaluation day for
  the active source recipe and publication generation is selected. No candidate
  recipe, other generation, older bundle or superseded audit inflates counts.
  The bundle manifest also binds the current parsed document, acquisition state,
  errors and dependencies: a same-original-version reparse is not silently current.
  Semantic/temporal receipt changes and recipe toolkit drift invalidate acceptance.
- Validity uses the existing saved public validity evaluator at the explicit
  instant. Unknown/open-ended/unsupported/conflicting rules stay unknown; fetch
  time is not a deadline. A validated independently saved expiry can survive an
  extraction failure; rejected temporal claims and missing/stale results go into
  unknown instead of reusing an
  old decision. This does not establish current hiring or individual eligibility.
- `positions_by_validity_quality` crosses all three validity buckets with
  `coarse`, `full`, `unversioned`. `reported` means rows in the **selected output**,
  including potentially rejected output; it does not mean accepted extraction.
  Cancelled/non-recruitment cases are excluded from this position matrix.
- `accepted` requires current material/classification/recipe/receipt bindings,
  success/partial without blocking errors, a valuable notice parse for the exact
  page version, and agreement between stored position rows and output count.
  Coarse output is not accepted detailed extraction. Accuracy-contract recipes
  additionally require full quality; unversioned is never relabelled full.
  Expired but valid extraction can count as accepted and displayed.
- `public_default` / `public_with_partial` (schema v2) count the actual public
  candidate projection independently of audit acceptance. Both default to all
  materials. Expired, review-needed, cancelled and readable fallback rows remain
  displayed with tags, including when current classifier/blob/recipe checks fail.
  These are not claims of admission, current hiring or eligibility. Public totals
  include all resource buckets; acceptance and its matrix remain audit-bound.
- `observations` contains per-resource IDs, binding IDs, states and reason codes,
  not material text or credentials. It makes missing/stale/rejected cases
  actionable. `reason_counts` covers all resources; recruitment `states` covers
  only the recruitment-candidate denominator. No accuracy percentage is inferred.

## Pure normalized new-contract replay interface

Python interfaces in `youju.onboarding.funnel`:

- `aggregate_funnel(FunnelContext, Iterable[FunnelObservation]) -> dict`: pure;
  one observation per resource, including missing/failed/unclassified cases.
  Duplicate resources, inconsistent bindings, invalid counts and public claims
  in offline replay are rejected.
- `snapshot_observations(session, *, blobs, source_id, clock, material_only=False)
  -> (context, rows)`: adapter over an existing read transaction; no executions or
  writes. `material_only=True` supplies complete resource/material baselines
  without reading saved classifications, audits, position rows or admission.
- `report_source_funnel(*, snapshot, blobs, source_id, clock, replay=None) -> dict`:
  read-only standalone entry point.

An offline full-source replay harness can normalize its **new host-validated**
per-case results into `FunnelObservation` objects and call the pure aggregator.
`FunnelContext` must name the new classifier key, recipe/artifact ID, generation,
contract identifier, explicit clock, source ID and `mode="new_contract_replay"`.
`state="accepted"` and `accepted_positions` mean validation accepted the new
extraction, **not** merely that a script returned success. A failure can retain
`reported_positions` but must have zero accepted/public counts. Replay detailed
acceptance requires `quality="full"`. Coarse output can be reported, not admitted
as detailed extraction. Missing dates are explicitly `validity="unknown"`.

The exact per-case normalization API is:

```python
row = FunnelObservation(
    resource_id=resource_id,
    version_id=scan_last_version_id,
    document_id=scan_last_document_id,
    bundle_id=scan_bundle_id,
    classifier_key=new_context.classifier_key,
    recipe_id=new_context.recipe_id,
    generation=new_context.generation,
    material_current=True,  # all three material IDs verified against the snapshot
    classified=True,
    page_roles=tuple(new_assessment["page_roles"]),
    business_type=new_assessment["business_type"],
    classification_review=new_assessment["requires_review"],
    state=host_disposition,
    validity=independently_validated_validity_status,
    cancelled=validated_whole_notice_cancellation,
    quality=new_quality_tier,
    reported_positions=len(new_output.get("positions", [])),
    accepted_positions=host_validated_position_count,
    public_positions=0,
    public_positions_with_partial=0,
    reasons=tuple(diagnostic_codes),
)
```

`host_disposition` is one of `accepted`, `coarse`, `cancelled`, `rejected`,
`failed`, `review`, `missing`, `stale`. Only `accepted` may have a nonzero
`accepted_positions`. `quality` is `full`, `coarse`, or `unversioned`; `validity`
is `not_expired`, `expired`, or `unknown`. For missing materials retain the real
nullable scan IDs, set `material_current=False`, and do not invent a bundle.
For a full-source harness, seed **all** resources from
`snapshot_observations(..., material_only=True)` and replace their bindings with
the new context. Fill classification/output fields only from actual new replay
results. An unexecuted case is not an accepted empty success.

For CLI input, serialize this envelope and pass `--observations /audit/replay.json`:

```python
from dataclasses import asdict
from youju.onboarding.funnel import SCHEMA

envelope = {
    "schema": SCHEMA,
    "context": asdict(new_context),
    "observations": [asdict(case) for case in new_host_validated_cases],
}
```

`replay_observations` validates exact full-source resource coverage and each
version/document/bundle against the explicit snapshot. Its classification,
validity and extraction come only from the new observations, not saved DB labels.
In this mode the snapshot adapter reads material bindings only, skipping saved
classifications, audit outputs, positions and public admission entirely.
The context recipe/generation identify the replay artifacts and need not be the
snapshot's active recipe. Public counters must remain zero: offline validation
has not activated a source. The tool trusts the harness's explicit host-validation
normalization; it cannot prove a claimed artifact was really executed. Existing
`replay_accuracy.py` diagnostics are **not** implicitly accepted as this contract;
they lack the complete material bindings and independent acceptance/public gates.

## Verification boundary

Focused tests use pure observations and synthetic admitted jobs copied into a
fresh temporary schema with valid content-addressed blobs. Immutable resource
versions are inserted, never updated, and immutability triggers are not disabled.
Tests cover denominators, mixed overlap, all validity buckets, cancellation,
unknown, old recipes/generations/classifiers/history, same-version reparse,
rejection, missing parse, missing blobs, full-source replay isolation, CLI clock
files and unchanged input bytes. No real model, paid service, production data,
network, browser or WeChat-device verification is implied.
