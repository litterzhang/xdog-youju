# Full Saved-Source Runtime Replay

`server/scripts/replay_source_accuracy.py` retains its filename but now performs a
**runtime replay, not an accuracy measurement**. It uses `recipe-runtime-v1`,
`position-draft-v1` and `position-facts-v3`. It neither adopts a Recipe nor publishes
jobs. No production/source migration, downloads, model calls or deployment occur.

## Execution and interfaces

The CLI requires explicit `--snapshot`, `--blobs`, `--source-id`, `--now` and
`--output`. The clock must include a UTC offset. The snapshot must be an approved,
settled SQLite **backup copy**, not a live database with uncheckpointed WAL.

- Every resource remains in the funnel denominator. Every resource with saved
  bundles gets one selected bundle: its valid scan pointer, otherwise the newest
  `(created_at, id)` with a fallback warning. History is not another denominator;
  there is no 40-case/default sample limit.
- Classification uses current shared rules, optional `--local-classifier` JSON,
  and host validation, with no model fallback. Saved DB labels are not reused.
- Recruitment articles get a fresh **cost-only** route. Unresolved classification
  remains visible but does not execute a Recipe. Non-recruitment gets no route.
- Resolved articles execute the official Flow graph and real isolated `Sandbox`.
  `--concurrency` is 1–4, default 2. There is no unsafe in-process script fallback.
- `--candidate` accepts canonical JSON `{workflow, parser_py, config}` and validates
  the approved graph and runtime/position contract markers before execution.
  Without it, the candidate is the **host template, not an Agent-authored source
  Recipe**. Supplied generation provenance is not independently authenticated.

The current shared runtime has no detailed extractor injected in this tool.
Supported scripts can complete on either route without a model. A detailed path
that actually needs fallback remains unfinished with `MODEL_DISABLED`.

Do not run the full USTC inventory until main freezes the implementation and
approves the snapshot/clock/output. After that gate, the command shape from the
repository root is:

```sh
UV_PROJECT_ENVIRONMENT=/home/zhang/workspace/xdog-youju/server/.artifacts/recipe-redesign-20261023/venv \
UV_OFFLINE=1 uv run --directory server --frozen python scripts/replay_source_accuracy.py \
  --snapshot "$SNAPSHOT" --blobs "$BLOBS" --source-id "$SOURCE_ID" \
  --now "$CLOCK" --output "$NEW_REPORT" --concurrency 4
```

This document is not a receipt for a new full-source replay.

## Report and interpretation

The report schema is `source-runtime-replay-v1`. Candidate provenance names the
runtime/position contracts and digest. `runtime_fingerprint` comes from
`youju.recipe_runtime.versioning`, separately from `source_audit_policy` and
`routing_policy`. Each actual execution captures `executed_candidate_digest` and
`executed_runtime_fingerprint`; upstream-only records leave both null. Execution
bindings and the start/end runtime fingerprints must agree, otherwise replay
refuses to publish. Routing/source policy do not enter the job-facts identity.
Inventory includes resources, original
versions, all saved bundle versions, selected bundles, missing bundles/versions/
scans, scan states, selection methods, bundle statuses and optional review-table
presence/absence. Classification counts include missing classifications, review,
methods, business types and overlapping page roles. Errors are not silently
excluded from denominators.

Each selected-bundle record carries:

- Material/selection provenance and current classification.
- `routing`: fresh cost metadata, never job dates or notice expiry.
- `execution_status`: completed, unfinished, error or upstream-only.
- `result`: the official completed runtime result, never a diagnostic replacement.
  `execution` preserves the official success or failure receipt when provided;
  absent failure metadata stays null rather than inventing a successful/model-free run.
- `raw_outputs`, actual `rejected_result`/origin and, where available,
  `rejected_draft` plus `diagnostic_normalization`. Diagnostic rows from an
  unfinished/rejected run are **never accepted**, even if normalization succeeds.
- Independent `position_calendar` for completed results, and separately labelled
  `diagnostic_position_calendar` for unfinished diagnostics. Comparisons use only
  `job_validity` over typed position facts and their own field issues, including
  explicit year/time precision. Routing cannot supply or veto those dates.

Top-level accepted counts mean **runtime-validated rows, not source admission**.
Completed, unfinished-diagnostic and combined extracted counts are separate. The
funnel additionally checks current resource/version/document/bundle bindings;
replay public counts remain zero. Its `cost_routing` and
`position_calendar_status` metrics stay separate. Generic funnel compatibility
fields still exist: notice validity is left unknown, and the internal `full`
aggregation label on completed results is not a Recipe `quality_tier` or a claim
of complete material/semantic accuracy.

Reviewed calendar/change tables are inventoried only. This tool does not apply an
old whole-notice period evaluator or invent a replacement association authority.
Shared-domain unsupported conditions, unavailable detailed extraction and missing
acquisition remain explicit limitations, not accuracy failures repaired by this
script.

## Provenance and immutable output

Snapshot connections use SQLite `mode=ro&immutable=1` plus `query_only`. Nonempty
WAL is rejected before and after execution; the main-file digest must stay fixed.
Blob reads require a regular non-symlink file, a SHA-256 filename matching its
bytes, and a 16 MiB limit. HTML metadata upgrades retain evidence IDs, block text,
locators and tables; changed evidence retains the saved view with a warning.

`--reuse-classification-from` accepts **only a compatible current report** with
matching snapshot digest, source, blob root, clock, classifier implementation and
local-model identity, explicit prior runtime fingerprint, zero-model/network metadata, full resource coverage, current
scan bindings and selected bundle digests/ownership. Every cached classification,
including non-executed pages, must validate against saved evidence. Only that
classification is returned from the cache. Routes, dates, positions, drafts,
results, errors and parser observations are rebuilt, never passed as a prior
runtime result. A changed, well-formed prior runtime fingerprint permits classification-only
reuse, not reused execution bindings; missing fingerprints fail explicitly. This
provenance validation is not authentication or gold labels.

Old schemas/contracts fail explicitly. To recompute, omit the cache flag and
choose another new output path; never rewrite/relabel an old cache.

Output must be a **new explicit path**, outside blob storage and distinct from
all inputs/snapshot sidecars. Existing files/symlinks are rejected. A bounded
512 MiB temporary report is published atomically with an exclusive hard link, so
a racing writer cannot be overwritten; failed publication removes the temporary
file. Baselines and source artifacts are never replaced.
