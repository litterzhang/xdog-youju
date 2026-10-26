# Candidate Job Display Policy

## Persistence and visibility

All structurally valid `position-facts-v3` candidates remain stored and are
shown by default for registered active sources. A passing source audit is no
longer a prerequisite for public reads. Missing attachments, unresolved fields,
failed refinement, unaccepted recipes and elapsed application deadlines do not
hide readable candidates. Invalid envelopes, missing titles and empty captures
are not fabricated into jobs. Recipe activation and repair acceptance retain
their independent audit requirements.

The public projection selects the latest candidate-bearing audit per notice
resource, across recipe versions. Historical rows remain immutable, but are not
shown as duplicate current jobs. A newer failed attempt with no usable positions
retains the previous rows and adds risk tags. Paused sources and cross-source
recipe ownership violations remain excluded.

## Tags and filters

`display_tags` distinguish deadline state, cancellation, incomplete parsing,
review requirements, incomplete/stale materials, pending acquisition, AI inference
and unknown fields. Tags are diagnostics, not verification of current hiring or
eligibility. A reviewed cancellation remains visible with its cancellation tag.
Original/AI-inferred/unknown field provenance is unchanged. Notice cost routing
never supplies job dates or controls visibility.

`GET /api/jobs` and job details default to `include_partial=true`. Users can
explicitly select complete materials only. Education, employment and literal
search filters remain conjunctive. Expired jobs are included and labelled;
list/detail/feedback use the same read policy. Missing admission IDs and successful
check timestamps are nullable rather than invented.

The WeChat list and detail pages share monochrome tags, preserve the styled list
layout and native back navigation, and default to all materials. Runtime warning
text remains literal text, never executable HTML.

## Delivery scope — October 26, 2026

Verification uses isolated builds and the locked private 2.4.9 environment.
Saved USTC SQL is opened read-only; no model calls or new downloads are required.
The public endpoint returned `503 DATABASE_UNAVAILABLE` before this change.
Uploading client code does not migrate the live schema, activate a recipe,
replace the production database, deploy H5 or constitute WeChat review/release.
See `server/.artifacts/all-jobs-20261026/` for verification and upload receipts.

### Verified client delivery

- Official Taro upload succeeded for **0.1.12** on October 26, 2026 at
  **08:32:56 Asia/Shanghai**, reported size **554.7255859375 KB**. This is a
  development upload, not a review submission or release.
- TypeScript, 120 client tests and isolated H5/weapp builds passed. H5 emits its
  existing bundle-size warning. An isolated Chromium check using saved real API
  DTOs verified risk tags on list/detail and native back, with no page errors;
  WeChat device behavior was not verified.
- The read-only saved USTC projection returned **377 candidates / 53 notices**.
  The SQL checksum was unchanged. No new extraction, downloads or paid models
  were run. All 117 served H5 files still match the recorded published manifest.
- Read-only live schema inspection found missing `positions.deadline_on` and
  `positions.application_start_on` columns. No production migration or service
  restart was performed; client upload alone cannot resolve the live API's 503.

- Final server regression: **1427 passed, 4 opt-in sandbox tests skipped**.
  Ruff lint/format passed. Final receipts are in `verification-summary.json` and
  `backend-delivery.log`; early failing logs record the replaced visibility
  expectations, not the final delivery result.

## Subsequent approved production rollout

The migration/deployment was subsequently approved and completed on October 26.
See [the rollout record](ALL_JOBS_ROLLOUT_20261026.md) for live counts, preserved
history, migration-only provenance, paused incremental monitoring and WeChat
0.1.13. The earlier no-deployment statements above describe the preceding task.
