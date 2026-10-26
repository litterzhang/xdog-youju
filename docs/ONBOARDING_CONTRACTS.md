# Onboarding Implementation Contracts

This is the integration contract for the source pipeline. See `ONBOARDING.md` for
implemented behavior, verification and explicit unsupported cases.

## Material document

`parse_material(body: bytes, *, media_type: str, url: str, material_key: str) -> dict`
returns JSON-compatible data:

```json
{
  "format": "html",
  "status": "ok",
  "title": "",
  "blocks": [{"id": "sha256:b1", "text": "literal text", "locator": "original locator"}],
  "tables": [{"id": "sha256:t1", "locator": "original locator", "rows": [["header"], ["value"]], "cell_evidence": [["sha256:b1"], ["sha256:b2"]]}],
  "links": [{"url": "https://example.org/a", "raw_url": "/a", "label": "label", "is_attachment": false}],
  "errors": [],
  "truncated": false
}
```

Formats: html/pdf/xlsx/xls/docx/doc/xml/unknown. Status: ok/partial/unsupported/failed.
Errors are objects with code and message. Evidence IDs are globally unique within a
bundle by prefixing the material's resource-version identity. The caller appends
url, resource_id, version_id, and sha256 to each document. Every table cell has a
literal block and source locator. HTML links are syntactic candidates, not approval.
The material parser runs in an OS-isolated child, with bounded bytes, CPU, memory,
file size, execution time, decompression, rows/pages and output. No file-format
reader is exposed to source workflows. XML sitemap output uses links, not positions.

## Resource bundle

```json
{
  "id": "content-and-dependency digest",
  "source_id": "source",
  "resource_id": "resource",
  "url": "https://example.org/notice",
  "page": {},
  "attachments": [],
  "status": "complete",
  "errors": [],
  "captured_at": "2026-10-11T00:00:00+00:00"
}
```

The page and each attachment are material documents. A missing/blocked/failed
attachment has url, resource_id when known, status=failed, empty blocks/tables,
and an error. Bundle status is complete only when every material is ok, otherwise
partial. Snapshots and dependency manifests remain immutable. All database writes
and publication decisions belong to the host, not generated scripts.

## Classification

`Classifier(model=None).classify(bundle)` is async and returns an assessment.
Model port: `await model.complete(task, payload, schema, tier="small") -> dict`.
No model means unresolved material becomes unknown, never unrelated by default.
`CLASSIFICATION_SCHEMA` describes model outputs; host validates evidence IDs.
Assessment fields: page_roles (array of notice_list/article/navigation/attachment/
other/unknown), business_type (recruitment/correction/cancellation/result/unrelated/
unknown), revisit (none/daily/weekly), template_key, reason, evidence_ids,
requires_review (bool), method (rules/small/large/unknown). Unknown acquisition or
classification must not cause revisit=none. Return model configuration/version
metadata where available; don't infer model confidence calibration.

## Business toolkit

`check_validity(bundle, now_iso, config=None) -> dict`: status=not_expired/expired/
unknown, deadline (ISO date or null), evidence_ids, reason. Current time is an
explicit input. Returned metadata includes basis, scope (complete_materials or
available_materials), and material_issues. Missing attachments do not erase an
explicit readable-page cutoff; incomplete pages still remain unknown. The scope
must not be omitted or changed by generated code. Date-only deadlines expire after the local calendar day, not at
its beginning. Unknown/rolling/conflicting or per-position periods must not expire
an entire announcement. Arrival/age dates are not application deadlines.

Optional application_period carries an evidenced source declaration independently
of clock expiry; current_hiring_status is not_verified. Known open_ended or
until_filled rules must not be described as missing deadline information. Native
authoring requires these known rules; independent schema/evidence/negative/date
guards remain mandatory. See `APPLICATION_PERIOD.md`.

`extract_positions(bundle, config=None) -> dict`: status=success/partial/review/
cancelled/failed, positions, errors. Each position uses literal title and optional
headcount, education_text, major_text, requirements_text, duties_text,
hiring_unit_text, employer_text, employment_arrangement, deadline_text, deadline_on,
and evidence mapping field names to block-ID arrays. Evidence is checked against
material documents. Unknown fields stay null; employer is not inferred from source.

`validate_result(bundle, result) -> list[dict]` returns errors without mutating the
result. A complete workflow result adds validity to the extraction object.
`select_samples(bundles, limit=3)` returns a dict containing samples (<=3 bundles),
covered_formats, covered_templates, uncovered_formats, uncovered_templates.

## Recipe authoring and execution

`XdogModels.complete(task, payload, schema, tier="small") -> dict` uses actual
xdog-flow agent nodes for classification/extraction, no custom provider client.
Authoring uses the official `xdog-agent` native loop with the complete installed
flow-workflows skill and project contract in its system prompt. The author model
defaults to `gpt-6-astra`. Isolated official filesystem/Bash tools operate on a
persistent source workspace; no material downloader or host access is granted.
Explicit configuration and permission are needed for live model calls. Tests stub
provider boundaries.

Candidate object: workflow (canonical flow JSON object), validity_py (defines
`check(ctx, bundle, now_iso, config)`), fast_parse_py (defines
`parse(ctx, bundle, config)`), config (JSON object). RecipeRunner validates the
artifact and uses real xdog-flow execution. Only reviewed business toolkit nodes
or candidate validity/fast_parse references are allowed; arbitrary inline code,
tool references, CLI backends, skill paths, imports in host, subflows and extra
capabilities are forbidden. Candidate module import AND execution happen only in
a bounded OS sandbox through host-provided callbacks. The parent flow engine
never imports candidate modules. Generated outputs are schema/evidence checked.

Main-owned sandbox API:
`await sandbox.run_script(files: dict[str,str], module: str, function: str, inputs: dict) -> object`.
Inputs include ctx-independent named ports; sandbox constructs a small ctx.
It supports only candidate modules validity/fast_parse and a read-only installed
business toolkit. It has no network, user home, secrets or database.

Authoring takes <=3 samples, a fixed toolkit guide, a working template, and schemas.
There is no author maximum-turn setting, fixed candidate count, run token budget
or whole-run timeout. Per-tool safety limits and ordinary model budgets remain.
The server first requests a workspace, prepares immutable input and enqueues a job.
The independent RecipeService worker reports progress, validates samples and returns
`candidate_ready`; only the source server may import, fully audit and activate it.
See `RECIPE_SERVICE.md` for the process/ownership and cancellation contracts.
Detailed model calls also default to 30 turns and no host token/time quotas.
Optional explicit experiment budgets remain distinguishable in call accounting.
`compact_bundle` preserves all source text and table cells, shortens evidence IDs
and omits only locator strings/classification metadata from the model view;
`restore_extraction` rejects unknown aliases before restoring original IDs.
The Agent reads `/history/status.json`, edits `/work/{workflow.json,config.json,
validity.py,fast_parse.py}`, and calls host-gated `submit_result` with
`{"result":{"ready":true}}`. The host validates actual files, records every verdict,
and returns errors for further turns. Cancellation, a reported blocker or provider
failure preserves work but is not success. Explicit resume reuses the directory.
Agent progress reports are untrusted; audits process all notices, not only samples.

The fast adapter is probed before a detailed-model call. Nested layout tables and
leading display counts are excluded from position-row accounting only with exact
structural and literal evidence; unknown rows and omitted actual positions still
require review. Source originals are never deleted or rewritten by these rules.

## Source admission and quarantine

`audit.admission.policy` is `quarantine-v1`. Activation requires a validated source
recipe, completed inventory/classification/audit work, at least one successful
evidence-backed announcement extraction, and no source-level execution failures.
The summary records `blocking_reasons`, `successful_announcements`,
`quarantined_announcements` (bundle IDs) and `source_errors_by_code`.

Assessed unknown pages, unavailable attachments and failed/partial announcement
outputs stay recorded and excluded from current positions, without blocking other
validated results or incremental discovery. Unassessed pages remain pending work,
not implicitly reviewed exclusions. Generated errors are retained alongside host
validation errors. `position_count` counts accepted audit positions;
`candidate_position_count` also includes partial candidates, not published jobs.

Static parse failures may queue a later dynamic attempt after source activation;
that retry does not reopen onboarding. Workflow/configuration/sandbox failures
still block, and a source with no successful extraction cannot activate. Neither
schema/evidence checks nor the authoring turn bound is bypassed.

Readable missing-attachment samples may pass source authoring as partial with
nonempty evidence-valid rows and only the exact known material warnings. Runtime
partial results stay in audit JSON; current-position publication checks remain
separate. Position-extraction failure preserves already-validated script validity.
`source-reanalyze` starts a new analysis generation from saved materials without
changing acquisition state or fetching resources.

## Audit disposition versus extraction output

The `available-material-v1` audit policy stores blocking errors in
`AuditResult.errors` and exposes known input warnings separately. Exact matching
warnings on an intact readable page can produce audit status `partial` with
`extraction_status=parsed` and `material_status=partial`; they do not automatically
set `review_required`. Field/schema/classification failures still need review or
repair. Graph result schemas and their original error arrays are preserved.

Parsed available-material positions are stored with `review_status=material_incomplete`.
Only explicit `include_partial=True` queries expose them with their warnings;
normal complete-material, freshness, dependency and evidence restrictions remain.
`source-reassess` creates a new audit generation from same-day saved outputs, with
no model or fetch work. Rejected extraction fields may be retained in failed audit
JSON for diagnostics, never in accepted position rows.
