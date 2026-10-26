# Module Redesign Verification

Verified October 25, 2026 (Asia/Shanghai). This is a local implementation and
saved-material POC, not deployment, source activation, semantic accuracy measurement
or confirmed hiring availability. The comparison clock is deliberately frozen at
**2026-10-24T13:55:03+08:00**, not the current verification time.

## Delivered modules

See `MODULE_RESPONSIBILITY_REDESIGN.md` for the ownership table and dependency graph.
The source-facing Recipe facade now delegates to shared extraction and service-owned
authoring. Only `recipe-runtime-v1` / `position-draft-v1` / `position-facts-v3` are
accepted. Old validity adapters/Flow templates are retired, not fallback paths.

Cost routing, typed facts, precise date projection, common/position scope, opaque
condition refinement, explicitly unknown logical relationships, employment versus
staffing, reviewed changes/calendars, managed sessions and reverse-query tools are
integrated. Quick rows use the same SQL/admission/query gates as detailed rows.
Capture diagnostics neither erase facts nor exclude readable authoring examples.

## Tests and builds

| Verification | Actual result |
| --- | --- |
| Full backend suite | **1,360 passed, 4 opt-in skipped** |
| The four opt-in real Linux/bwrap scenarios, separately enabled | **4 passed** |
| Client suite | **119 passed** |
| TypeScript | Passed |
| H5 private build | Passed; existing webpack performance warning |
| WeChat private build | Passed |
| Ruff lint and format over source/tests/scripts | Passed |

Real sandbox tests exercise official filesystem/Bash/Flow capability transport,
read-only mounts, process cleanup, native init/build, SDK skills and frozen inputs.
Provider streams and HTTP transports in ordinary tests are explicit offline doubles.
No browser or WeChat-device validation is claimed. Builds were not uploaded.

## Real USTC saved-source replay

Input snapshot SHA-256:
`4feeb8752f4547c769912efdb408259778c50f4eaebd0e37d67d58d3b17f790c`.
All **1,314** current saved views were reparsed with unchanged evidence text/IDs and
classified again using current rules (no old assessments/results reused). Inventory:
**1,335 resources**, **21 without saved bundles**, **165 list pages**, **183 recruitment
article candidates**, with one list/article overlap. These are observed labels, not gold.

### Cost categories — never job expiry assertions

| Category | Notice candidates |
| --- | ---: |
| Fixed-end hint, active | 4 |
| Fixed-end hint, expired | 124 |
| No fixed end, low priority | 0 |
| No fixed end, high priority | 55 |
| Unknown | 0 |
| Total | 183 |

All five categories are implemented/tested; this particular inventory uses three.
The absence of a usable fixed end routes to importance policy, not a fabricated date.

### Actual execution and positions

- **182** resolved candidates executed current official Flow; **one** classification
  review was not executed.
- **130** executions completed: **125** produced **164** normalized position entries;
  **five** produced no positions and retain scope/field review diagnostics.
- **52** executions remain `MODEL_DISABLED`: detailed semantic/layout extraction was
  required, but deliberately not enabled in this no-paid-model verification.
- Those unfinished drafts retain **163** unaccepted diagnostic entries. Together
  there are **327** observed entries, not 327 completed/public jobs or headcount.
- Among completed entries, calendar results are **145 expired**, **1 not expired**,
  **18 unknown**. Diagnostic-only entries have 22 expired, 3 not expired, 138 unknown.
  These job-date calculations are independent of the notice cost categories.

There were zero model/network calls, downloads, temporal/candidate result reuse,
source activation or publication. The Recipe is the current **host template**, not
an Agent-newly-authored USTC Recipe. Native authoring integration is verified separately
with the actual SDK and explicit provider doubles. Real semantic accuracy remains
unmeasured; enabling detailed extraction is a later authorized experiment.

## SQLite copy verification

A separate verifier copied the immutable source backup into a **new private output**,
explicitly added only the current Date-projection schema to that copy and audited
fresh runtime results without changing its active source binding.

- **182** new audit rows: 125 partial, 5 review, 52 failed/unfinished.
- The classification-review candidate is separately recorded as **not executed**,
  never manufactured into a parser failure.
- **164** new typed position rows stored; **146** real SQL Date values exactly match
  their resolved source-tagged JSON facts; **18** unknown/inferred projections are null.
- Source Date range: **2024-06-10 to 2026-11-14**.
- Fetch-attempt count, old rows, active Recipe ID/generation and original snapshot
  hash remain unchanged. New diagnostics live in a private overlay blob directory.

This is an execution/storage POC, not an acceptance or production migration command.
Incomplete captures caused no generic date/fact veto. Remaining work is attributed to
actual unsupported semantic/layout cases, not to missing annexes by themselves.

## Reproducibility and receipts

All commands used the existing isolated locked 2.4.9 environment:

```sh
export UV_PROJECT_ENVIRONMENT="$PWD/server/.artifacts/recipe-redesign-20261023/venv"
uv run --directory server --frozen pytest -q
uv run --directory server --frozen ruff check src tests scripts
uv run --directory server --frozen ruff format --check src tests scripts
```

`server/scripts/replay_source_accuracy.py` requires explicit snapshot/blob/source/
clock and a new output path; see `SOURCE_RUNTIME_REPLAY.md`. The private-storage
verifier is `server/scripts/verify_runtime_storage.py`; it rejects existing/outside-
artifact output directories and stale runtime fingerprints.

Private receipts/logs under `server/.artifacts/module-redesign-20261024/`:
`backend-final.log`, `real-sandbox-final-v2.log`, client type/test/build logs,
`ustc-final-runtime.json`, `ustc-storage-final-v2/verification.json`,
`final-consolidated-verification.json`, `final-artifact-sha256.txt`.

The root AGENTS.md and supplied redesign guide hashes are unchanged. The live
`server/.venv` remains **xdog-agent 2.4.1**; isolated verification used **2.4.9**.
No live DB/config migration, service restart, deployment, notification or upload occurred.
