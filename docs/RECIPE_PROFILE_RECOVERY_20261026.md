# Recipe Profile Recovery — October 26, 2026

## Root cause

After classification finished, `CreateWorkspace` rejected the source's existing
managed workspace. It pinned `gpt-6-astra`, `source-author-v1`,
`source-recipe-v1`, and `xdog-flow:flow-workflows`; the current trusted caller
requests `gpt-6.1-sol`, `source-author-flow-v3`, `source-recipe-v5`, and `flow`.
Registration correctly refused an implicit identity/profile rewrite, but RPC
collapsed its descriptive exception into `REQUEST_REJECTED`. No new Agent had
started, and this was not a provider/model failure.

## Code correction

Profile conflicts now use the fixed, secret-free
`WORKSPACE_PROFILE_MIGRATION_REQUIRED` code and HTTP 409. Unknown/free-form
service errors remain redacted. Registration is still idempotent only for an
identical owner/profile and never silently changes a workspace or restores an
incompatible checkpoint.

## Explicit operational recovery

With the source and Recipe workers stopped, the operator backed up the runtime
database and archived the old authoring store outside the live storage root.
The existing `prepare_fresh_session` operation created the current-profile
workspace at its original canonical path. It preserved 297 prior-context files
(21,200,374 bytes), including opaque conversation/checkpoint history; it did not
reinterpret the old checkpoint as a new SDK session or activate an old Recipe.

`PipelineStore.resume` resumed the blocked **authoring** phase. All 1314 page
classifications were retained, acquisition was not repeated, and incremental
collection was not enabled. The workers were then restarted. Generation and
source-wide audit are separate asynchronous stages, not a cutover success claim.

## Verification

29 focused service, RPC and fresh-session cutover tests passed. Ruff lint and
format checks passed for the modified modules/tests. Private operational evidence
and backups are under `server/.artifacts/recipe-profile-debug-20261026/`.

## Observed resumed execution

At 10:37 Asia/Shanghai, native init completed successfully and the new build
`b70de4d8e9544826aa70cfa19fc382bc` was running with real Agent tool events.
The previous registration rejection is resolved. This does not establish a
finished Recipe, successful source-wide parsing, or admission.
