# USTC Live Verification — October 11, 2026

This document preserves earlier failed attempts and the later three-sample replay.
For the subsequent source-wide baseline, activation, revised classifier and real
incremental verification, see `USTC_FULL_POC.md`. Historical state statements below
are not the current source status.

## Historical result: the full onboarding loop did not pass

The server now uses the official PyPI releases of xdog-flow, xdog-agent, xdog-ai
and xdog-tui, pinned to 2.2.0. Installed distributions have no local/editable
`direct_url.json`; the uv lockfile contains PyPI registry artifacts and hashes.
`xdog-ai[openai]` and `httpx[socks]` supply the upstream optional protocol/proxy
requirements. No xdog package source or neighboring repository was modified.

The live run uses the registered USTC employment portal, not synthetic website
fixtures. Webpage traversal stayed on the approved HTTPS origin. Same-host legacy
HTTP attachments were explicitly admitted separately; page traversal, robots,
public-address validation, redirect checks and pacing were not disabled.

Artifacts: `server/.artifacts/ustc-live-20261011/`.

## What was actually observed

- The approved inventory processed 1,338 resource tasks and retained two material
  failures. It includes 1,312 webpage observations plus attachment processing;
  this is not a claim that every URL or subdomain of USTC was visited.
- The URL registry contains 5,106 discovered resources, including unapproved
  in-domain candidates and structural assets/form references. Those records are
  not all fetched pages. Twenty-one non-page references are reported separately.
- The two outstanding acquisition outcomes are a missing historical DOC (HTTP
  404) and a PDF exceeding the reader's configured input limit. Other material
  issues include scanned/blank PDF text, legacy Word structure, and unapproved
  attachment origins. They were not silently waived.
- Real Copilot calls succeeded with `gpt-5.4-mini` and `gpt-5.5` through published
  xdog SDKs. A Chat Completions configuration using `gpt-5.4` was not successful
  in this environment; the run did not pretend it had worked.
- Before stopping additional classification expense, 595 page assessments were
  persisted: 238 recruitment-related, 276 unrelated and 81 unknown. A total of
  246 assessments retain review requirements. Recruitment-related does not mean
  that all 238 pages are standalone vacancy announcements.
- The classification call ledger records 526 successful calls and four cancelled
  in-flight calls. Its reported-token subtotal is a lower bound, not a complete
  billing statement or proof of classification accuracy. No model responses or
  credentials are included in that ledger.

Classification remains incomplete. The worker was explicitly stopped with its
persisted progress intact once the existing activation blockers were clear; no
unattended model worker is left running.

## Real authoring preflight

A separate, clearly labeled preflight supplied three complete, already-classified
USTC department recruitment resources to the real xdog authoring Agent. It did
not mutate the source into a later onboarding stage or replace the source-wide
audit with a sample test.

The initial candidate plus one repair both failed validation. The first round's
detailed extraction shortened source-cell text and inferred an employer without
sufficient contract evidence. The repaired workflow failed to publish the required
result: its explicit entry could not activate an independent fast-parser node.
The complete candidates, errors and model accounting are preserved in
`authoring-preflight.json`. No third internal repair was run and neither candidate
was activated.

Consequently, source-wide parsing audit, source activation and real incremental
execution were **not completed**. The source database remains in classification,
not active. `verification-report.json` records these limits explicitly.

## Fixes supported by the real run

- Published-package installation and optional HTTPX/OpenAI protocol dependencies.
- Explicit submission schemas in model instructions, strict stream/budget failure
  handling, and local termination after an actually accepted structured result.
- Layout-table/spacing-row handling with original DOM evidence retained.
- Structural URL kinds, so CSS/JS/images and form actions are not treated as pages.
- Literal body headlines, application-date context and conservative source fields.
- Sparse spreadsheet bounds: formatting-only cells no longer create a false
  column-limit error; actual out-of-bound values/formulas remain rejected.
- Bounded robots caching without increasing the request rate or reusing expired
  permissions after a failed refresh.
- Offline reader replay preserving original fetch timestamps and versions.

The default offline suite and the separate synthetic HTTP + real Chromium + real
bubblewrap + real flow smoke remain useful regression checks. They are not evidence
that this real USTC source passed its onboarding gates.

## Remaining work

1. Apply the subsequently approved per-resource quarantine policy during the next
   real audit; do not weaken evidence or access controls to manufacture a pass.
2. Finish classification of the remaining resources with an explicit model budget.
3. Improve the source-specific authoring recipe and workflow feedback, then run the
   actual whole-source audit rather than only the three-sample preflight.
4. Activate only under the chosen documented admission policy, then verify a real
   incremental pass. No deployment, notification or public publication was performed.

## Follow-up: per-resource quarantine policy

After this experiment, the operator approved source activation without requiring
100% of pages to parse. The implementation now separates source readiness from
individual material/output failures. A validated recipe and completed audit with
at least one successful evidence-backed announcement can activate while individual
failures remain quarantined. Unfinished work, no successful extraction, and
workflow/runtime/configuration failures remain blockers. Offline regression checks
cover mixed success, later-pass dynamic retry, incomplete audits and runtime failure.

This policy change does not retroactively validate either real candidate or activate
the USTC database. Candidate files were generated; their validation failed. Graph
reachability feedback now identifies the second candidate's missing activation edge
before execution. Field evidence requirements remain unchanged. No additional live
authoring call, completed classification, whole-source audit or real incremental
pass is claimed by this follow-up.

## Follow-up: persistent coding Agent and tool-call debugging

Authoring now uses the official `xdog-agent` native loop with `gpt-6-astra` and the
complete installed `flow-workflows/SKILL.md` injected into the system prompt.
Bash/filesystem tools execute in isolated children. Each source retains work,
read-only input and host-written history directories. The experiments below used
**30 model turns per run**, without a second candidate-count, run-token or run-time
budget. The later RecipeService implementation removed the author turn cap; see
`RECIPE_SERVICE.md`. Tool-level safety limits remain independent.

The first diagnostic coding run used the three saved USTC samples and the earlier
rejected candidates. It stopped after five model turns without a submission. A
second diagnostic captured the missing tool feedback: all nine Bash/filesystem
calls were rejected as `invalid_author_tool_call_id`; the Agent correctly reported
that it could not inspect or modify files. That second run exhausted its then-
configured 12 turns. These are historical settings, not current author limits.
Reports: `.artifacts/ustc-author-agent-20261011/` and
`.artifacts/ustc-author-agent-20261011-resume/`.

The defect was a host adapter assumption, not evidence that the skill prompt was
missing. The SDK Responses adapter combines a wire call ID with an opaque item ID;
the isolated-tool bridge incorrectly required the combined ID to fit 256 characters.
The child now receives a fixed-size local hash reference while the original SDK ID
is preserved for provider tool-result correlation. Regression checks cover long IDs
through both official tools and a real bubblewrap boundary. A prose-only reply also
continues the same Agent and turn counter rather than being treated as completion.

Offline verification with explicit real-sandbox opt-in passed 39 tests. Ruff and
lock checks passed. The separate synthetic HTTP/Chromium/source-pipeline smoke at
`.artifacts/onboarding-coding-agent-20261011-final/` produced three current synthetic
positions; it is not a real-source admission result.

### Post-fix 30-turn replay

The next run reused the same source workspace and actually exhausted 30 model
turns. Its SDK tool IDs measured 458 characters (29 for the wire call ID), confirming
why the old 256-character check was invalid. It read `/history/status.json`, executed
19 Bash calls, changed `workflow.json` and `fast_parse.py`, and submitted files twice.
There were no further `invalid_author_tool_call_id` failures.

This does **not** constitute a validated recipe or full-source onboarding. Host
submission tests still hit the existing detailed-extraction model's token/time
limits. A separate, model-free replay of the resulting fast parser returned partial
results with 6, 12 and 2 position candidates, retaining `UNCHECKED_ROWS` and
`FAST_PROBE_INCOMPLETE`. These candidates are not published positions. The authoring
Agent's own run had only the 30-turn limit; the reported model errors belonged to
separate `gpt-5.5` extraction calls made by host validation.

Artifacts: `.artifacts/ustc-author-agent-20261011-turns30/report.json`, its two
submission snapshots and `fast-check-summary.txt`. The persistent directory's
`history/runs/` retains the previous failures and the new run outcome. Source state
remains `classification`, not active. All diagnostic/model processes have exited;
no unattended worker was installed or left running.

## Successful sample replay after model-call debugging

The next investigation traced the failures to application configuration, not an
xdog-ai transport defect: the host supplied a 24,000-total-token budget and a
90-second deadline while some complete responses reported 31,083–39,888 tokens.
Default model/recipe time and token quotas have now been removed; runs retain their
turn bounds and usage accounting. Explicit experimental budgets are labeled as
host limits. No installed xdog SDK source or neighboring repository was modified.

Verbose DOM locator strings and repeated long evidence IDs also inflated requests.
The new model view preserves every literal block/table cell, uses reversible short
IDs, and validates restored output references against the original bundle. A
separate completeness defect treated a parent layout table and its leading 6/12/2
serial-count rows as unsupported positions. Structural/literal checks now recognize
those cases without dropping unknown rows or allowing omitted real positions.

Artifacts: `.artifacts/ustc-author-agent-20261011-fixed-ai/`.

- The real `gpt-6-astra` Agent resumed the existing directory and completed in
  **5 model turns**, with its first submission accepted.
- All three actual generated-workflow executions and fast-adapter probes passed.
- Three real `gpt-5.5` extraction calls succeeded, each using one provider request.
  Model input sizes were 30,493 / 32,377 / 19,435 bytes; reported total tokens were
  12,446 / 13,938 / 8,211. These are observed run values, not a general benchmark.
- Independent SQLite audits stored **6 + 12 + 2 = 20 position records**, with three
  successful audit outcomes and zero audit errors. Results were reused from the
  observed successful executions rather than making another model call for audit.
- All validity states remain `unknown`; successful extraction is not evidence that
  applications are open or that a particular applicant qualifies.

`accepted-candidate.json`, `attempt-1.json`, `report.json` and
`verification.sqlite3` hold the proof. The verification database is a snapshot
which uses the original blob storage path recorded in the report. The original
source remains in classification; full-site onboarding and real incremental
execution are still not completed. This closes the sample authoring → real model
execution → evidence validation → database-storage loop, not the whole source-wide
baseline. All live diagnostic processes exited after the run.
