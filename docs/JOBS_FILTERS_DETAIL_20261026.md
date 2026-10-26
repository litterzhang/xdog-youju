# Job Filters and Structured Detail — October 26, 2026

## Client

The inline-selector description below records version 0.1.15. Version 0.1.16
supersedes it with a transactional bottom sheet and grouped condition logic;
see [the subsequent refinement](JOBS_MINI_FILTER_SHEET.md).

The mobile list keeps three primary facets (deadline, education and employment),
with degree, staffing, work experience and material scope behind “more filters”.
Selections appear as individually removable chips; reset clears committed filters.
Search remains explicit, and filtering restarts the accumulating mobile feed.

The result heading reports the API's matching total, never the loaded row count.
Loading/error states do not claim zero results. Loaded rows are counted separately
at the footer. Infinite scrolling and native pull-to-refresh remain enabled.

Details group the overview, named recruitment conditions, business fields,
verification dimensions and official-source records. Conditions have their own
card: label, value and separate provenance/scope badges. No numbered conditions
or inline `[source]` suffixes appear. Known ALL/ANY relations use condition names;
unknown relations remain explicit. Original requirements, diagnostics and raw
record identifiers are behind disclosures rather than mixed into business values.

“Needs review” is labeled **信息待核验**, independently of the job's deadline.
An expired source deadline stays expired even if conditions or Recipe admission
need review. A read-only 50-row live sample found 23 expired rows, all with no
admission receipt and 21 with field issues. This is a diagnostic sample, not an
accuracy measurement or a reason to erase uncertainty tags.

## Query contract

New server facets: `deadline`, `degree`, `staffing`, `experience`. Existing
education gains associate, postgraduate and explicitly unrestricted values.
Facets match saved typed facts only, combined with search/material selection
before computing total and pagination. “No experience required” needs an explicit
zero-year value; missing requirements do not match. Education is not degree;
staffing is not employment mechanism. “Deadline not passed” never means verified
current recruitment or eligibility.

## Recipe generation boundary

Each generated parser must emit at most one `other` condition per final position,
containing the complete applicable remaining requirement text. Notice-wide/local
scope, ALL/ANY meaning and provenance must be retained by the generated artifacts.
Both parser scripts and Flow refinement must obey this rule. Sample validation
returns `OTHER_CONDITION_CARDINALITY` to the Recipe Agent for correction, including
on quick outputs. Neither host normalization nor client presentation concatenates
or rewrites split conditions. Existing saved jobs are not silently re-extracted.

Updated service prompts/guidance are code changes, not evidence of a repaired
source Recipe. Current source files are not hand-edited. A subsequent approved
Recipe task/deployment must load the new guidance, observe existing native
checkpoint guidance-update fences and re-audit its actual outputs.

## Verification and rollout boundary

Tests use synthetic/offline data, plus isolated H5 browser checks with a synthetic
DTO cohort. WeChat upload is an official Taro development upload, not review,
release or device verification. Builds are staged outside the served H5 root;
the served H5 is not published and the Recipe process is not restarted. The
API-linked source-worker restart is recorded in the delivery receipt below. Exact receipts
are retained under `server/.artifacts/jobs-filters-detail-20261026/`.

## Delivery receipt

Official Taro upload **0.1.15** succeeded at **11:29:02 Asia/Shanghai** on October
26, 2026 (567.4091796875 KB). Client tests: 128 passed; focused server tests: 134
passed. Typecheck, Ruff, staged H5 build and upload/weapp build passed. H5 browser
checks verified picker callbacks, matching totals, 20→40→45 scrolling, named
conditions, separate tags, collapsed records and empty results. Picker callbacks
were dispatched as browser events; this is not native WeChat device verification.

The API was restarted and its new facets verified through public HTTPS. The
source worker is configured with `PartOf=youju-api.service`, so systemd also
restarted it; the Recipe service was not restarted. The source subsequently
reported active/ready, 183 processed, zero remaining and no blocking error.
Do not describe this rollout as an API-only process restart. The served H5
manifest is unchanged. No paid model call, new Recipe task or saved-job rewrite
was initiated for this UI/filter change. New Recipe guidance still requires an
approved process reload/task and must respect native session guidance fences.
