# Recipe repair hardening

Status: implemented and verified locally on October 25, 2026. Five real source
Recipe repair tasks and a final cached 55-case live/SQL replay completed. See
[final verification](REPAIR_HARDENING_VERIFICATION.md) for exact outcomes, unresolved
cases, usage and the H5 incident. No Recipe was activated or backend deployed.

## Agent input budget

The host ceiling is **1,000,000 UTF-8 bytes (decimal MB)**. The prepared extraction
payload is checked before constructing its Agent. Existing SDK stream middleware
also measures the complete logical context (system instructions, messages/history,
structured tools and schemas) before every actual provider dispatch. A caller may
lower the ceiling, never raise it. This is logical finite-JSON context size, not a
provider's HTTP framing/compression measurement or a token estimate.

Over-limit input produces `MODEL_INPUT_LIMIT` without that upstream dispatch.
It is not silently truncated or redirected to an expensive model. Unresolved large
materials defer explicitly; automatic material chunking is not claimed. Receipts
separate maximum attempted input from maximum **sent** input. Source prompts are
bounded static guidance plus exactly one pinned payload interpolation; they cannot
amplify a payload repeatedly or interpolate material into system instructions.

The ceiling applies to extraction Flow/model calls. The persistent coding Agent
uses the SDK's own history/compaction and sandboxed paginated material tools; this
change does not impose an author lifetime or turn limit.

## Preserve facts, never fabricate a successful Flow

A detailed-stage failure still raises `RecipeError`. The host retains a separate
canonical `partial_result` from an already valid script stage when it has usable
positions. Source audit persists those candidates with blocking failure evidence;
model failure does not erase known facts, and does not grant publication.

Raw script/detailed/merged stages and normalized partial facts have separate,
bounded failure components. `inspect_failure` supports `script_draft`,
`detailed_draft`, `merged_draft`, `partial_output` and original `rejected_output`.
Absent/truncated evidence is explicit. An absent model output is not relabeled as
a model's empty answer. External cancellation does not fabricate completion.

SDK JSON submission is followed by canonical semantic review. Invalid condition
references, non-null logic for empty conditions and titleless new positions receive
host feedback in the next provider input and can be resubmitted. SDK Agent/Flow
execution is reused, not replaced. Final host checks still reject invalid output.
Existing source titles cannot be creatively rewritten under stable keys. A merged
identity conflict preserves quarantined script facts instead of deleting a job.
Provider diagnostics retain only allowlisted status/type metadata, not raw prose,
credentials or arbitrary response bodies. Timeout ownership is preserved.

## Empty capture is not a claim about live hiring

A source adapter may declare `coverage.empty_capture:{"table_index":N}` only with
zero positions/issues and supported, complete, non-detailed coverage. The host
checks the actual complete primary material, absence of annexes/capture gaps and
header/blank-serial-row shape. A nonempty table or missing material cannot be
hidden by that declaration. The observation remains source-selected, not proof
that every layout on a site was searched.

Runtime returns `status=empty` and skips inference. A static/unknown acquisition
requires `EMPTY_CAPTURE_NEEDS_RENDER`, owned by acquisition, not a coding Agent.
Synthetic dynamic acquisition metadata is tested separately; no real browser
verification of the two cached USTC empty pages is claimed. A settled empty result
cannot excuse deleting previously known positions in a repair regression.

## Repair ownership and evidence

Audit writes `execution.failure` only from a failure record bound to the audited
source/material/Recipe. Error-code strings authored by a parser/model cannot mint
repair authority. Manual and automatic sample selection use the same diagnosis.
Recipe schema/output defects can repair source adapters or Flow guidance. Provider,
input-budget, infrastructure and acquisition failures retain their owners instead
of triggering code-generation storms. Previously valid partial facts remain
regression-protected, not promoted to independent semantic gold.

Readable complete parents are not fetched/rendered again solely because a model
or source-code execution failed. Unknown empty captures and actual acquisition
problems retain normal acquisition handling. Source scripts/workflows are edited
only by the official RecipeService Agent. The source application owns audits,
regression fences and acceptance; RecipeService does not own its business DB.

## Verification work

The preserved Agent-authored baseline was registered by a real service Agent,
with exact original parser bytes, followed by a real `StartRepair` task using
`gpt-6.1-sol`, the official Flow skill/capability, checkpoint restore and compaction.
Extraction remains `gpt-5.4-nano`. This is private candidate generation, not source
admission or an exercised production callback. The final saved USTC replay is
complete: 51 completed workflows and four explicit timeout/input deferrals, with
377 candidates persisted and six positive/two empty-case checks passed.

Current verification: 1,427 backend tests passed (four opt-in checks skipped);
those four real sandbox checks passed separately. Client typecheck, 119 tests,
and isolated H5/weapp builds passed. The linked final report distinguishes actual
source-replay results from synthetic regression and sandbox checks.

Work log, current test results, source-task IDs and receipts are under
`server/.artifacts/repair-hardening-20261025/`. Model calls are explicit experiments;
default regression tests do not spend on models. The root AGENTS.md and original
source snapshot are preserved.

## H5 build verification incident

An initial verification build accidentally wrote the served `client/dist/h5`.
The mistake was reported; all 117 files were restored to the last recorded public
release using its full manifest and preserved SELinux label. Local Nginx index
bytes match that release. The unintended build is archived. This is not a claim
of an immediate pre-incident backup, unrecorded changes or remote cache recovery.
No Nginx configuration, backend restart or WeChat upload occurred. Subsequent
builds use explicit isolated `YOUJU_CLIENT_OUTPUT_ROOT` paths. See
`h5-recovery.json`; do not describe this run as having never touched the website.
