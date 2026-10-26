# Recipe hardening: final cached USTC verification

## Scope and recipe provenance

The final live recipe is service revision **eb253d7e60ac417a9fe908075383e46e**,
artifact digest `868c53dfb71011d0f069a27727fe42457482bd465b7c76c11dac37892c6e00ce`. A real official xdog 2.4.9 persistent Agent
using **gpt-6.1-sol**, packaged Flow skill/capability, checkpoint restore and native
compaction authored all source changes through five standard `StartRepair` tasks.
The host did not hand-edit generated source scripts or replace the Flow engine.
The main graph still has three native subflows: parse, detail and finish.

This replays the same **55 saved USTC cases**, not every source resource. Existing
inventory/classification is reused; there are no new downloads or classifier calls.
Evaluation remains frozen at **October 24, 2026, 13:55:03 +08:00**, for comparison;
execution finished on October 25. It does not certify current live recruitment.
The original snapshot and blob root were read-only. Real AuditLedger and Position
rows were written to a separate private SQLite copy, never the production DB.
No source binding or RecipeService accepted pointer was switched.

## Final funnel

```text
55 saved notices attempted
  ├─ 51 workflows completed
  └─  4 unfinished: 3 experiment timeouts + 1 oversized input

53 notices have normalized candidate positions
377 candidate entries persisted in private SQLite
  ├─ 139 have no blocking audit errors (still not source admission)
  └─ 238 remain quarantined
       ├─ 237 retained from the four failed detail paths
       └─   1 has an untrusted/inexact model capture diagnostic

Audit outcomes: 3 success / 45 partial / 5 review / 2 observed-empty
```

| Cost category | Notices | Flow completed | Model sessions | Candidate entries |
| --- | ---: | ---: | ---: | ---: |
| Fixed deadline / active cost hint | 1 | 1 | 0 | 1 |
| Fixed deadline / expired cost hint | 24 | 24 | 0 | 34 |
| No fixed deadline / high priority | 25 | 21 | 19 | 312 |
| No fixed deadline / low priority | 5 | 5 | 0 | 30 |
| Unknown | 0 | 0 | 0 | 0 |

The 29 quick-path notices made **zero model calls**. Seven detailed-path notices
also avoided inference, including the two observed-empty pages. Nineteen notices
invoked the native extraction Agent. Counts are per material,
not unique real vacancies, total hiring headcount or published jobs.

The source-tagged exact deadline projection is present on 32 entries; 345 remain
unknown. Until-filled/month-only/unstated deadlines were not padded with guessed
days or capture/update timestamps. These counts are not deadline accuracy labels.

## What the acceptance checks actually prove

Six literal source cases were checked independently of the generator's claims:

- Three engineering positions, each headcount one.
- Archaeology professor and associate professor, each headcount one.
- One English-teacher category despite an unavailable application-form attachment.
- One professor position, headcount one, with no fabricated until-filled date.
- One deputy-director category, headcount three (not three duplicate jobs).
- One laboratory deputy-director category, preserving the stated 1–2 range rather
  than inventing an exact integer.

Two other saved pages contain empty position tables. Their observation is checked
against table/capture data and the copied completed acquisition receipt, not a
claim that the live site has no hiring. No new browser session ran in this task.
`target-verification.json` verifies these cases plus all 55 structural/input/SQL
invariants. It is **not** independent semantic gold for every condition or field.

Script-only comparison to the preserved baseline found 48 unchanged notices and
seven additive/changed cases, with zero per-notice candidate-count decreases.
These seven deltas still require semantic review before any production admission.
The earlier model-enabled result of 110 entries dropped failed enrichment facts;
the new private persistence deliberately retains them, not silently publishes them.

## Input budget and failure ownership

The hard ceiling is **1,000,000 UTF-8 bytes of logical Agent context**, including
system messages, history and structured tool schemas, checked before every actual
provider dispatch. Prepared material is checked before constructing its Agent.
A caller can lower but cannot raise the ceiling. Prompt amplification/system data
interpolation is rejected. No silent truncation or expensive-model fallback exists.

The largest actually sent final context was **255,064 bytes**.
The roughly 1.95 MB input was rejected **before** an extraction model call while
198 valid script candidates were persisted with blocking evidence. Automatic
large-material chunking is not claimed; unresolved over-limit enrichment defers.
Coding-Agent lifetime/turn limits were not added; its SDK history/compaction is
separate from per-extraction-node input budgeting.

A detailed failure still fails the Flow. Host-normalized script facts, original
model rejection and raw script/detail/merge stages remain separate. Canonical
feedback catches bad condition IDs, empty-array/non-null logic and title rewriting.
A residual merge identity loss raises `REFINEMENT_IDENTITY_CONFLICT` and preserves
script candidates for review rather than reporting a successful zero-row result.
Only bound audit failure provenance can authorize source-code repair. Automatic,
manual and retry sample selection share that diagnosis; provider/budget/acquisition/
shared-domain failures are not automatically converted into paid code-generation.
Readable parents are not downloaded again just because refinement/code failed.

## Remaining limitations, not hidden successes

- Three notices hit the explicit 600-second **experiment** timeout. Their 39 script
  candidates persist; no production author lifetime cap was introduced.
- The oversized notice retains 198 candidates, including unresolved title/semantic
  coverage diagnostics and classification review. It is not fully parsed/admitted.
- A deputy-director notice retains its job but has an additional model-authored
  INCOMPLETE_CAPTURE diagnostic that does not exactly match trusted acquisition
  warnings. It remains quarantined instead of being silently downgraded.
- Unknown fields/condition relationships, incomplete materials and source/AI tags
  remain visible. No 100% semantic accuracy, eligibility guarantee or source-wide
  accuracy percentage is claimed. No production callback/deployment was exercised.

## Real usage and completeness limits

Final extraction: **19 sessions, 30 provider-stream
dispatches, 926,207 reported tokens across 18 sessions**.
One session lacks reported usage; aborted streams/internal HTTP retries can also
make this incomplete. The source author's aggregate SDK reports and prior runs
are separately recorded in `usage-ledger-final.json`; cached input/compaction
accounting is not converted into an invented money cost.

An initial live verifier mistakenly reused an offline audit generation. It was
cancelled, preserved and excluded from final results. Its three in-flight sessions
lack complete usage; the new verifier checks generation conflicts before inference.
This limitation prevents any claim of a complete project-wide bill.

## Verification and delivery

Backend semantic implementation: **1,427 passed; 4 opt-in checks skipped**, with four
real sandbox checks separately passed. Client typecheck, 119 tests and isolated
H5/weapp builds passed. Default tests do not call paid models. Final lint, packaging
and completion receipts are in `server/.artifacts/repair-hardening-20261025/`.

After this successful live run, one overlong diagnostic string was line-wrapped for
Ruff without changing its value. The complete Python module AST is identical;
`runtime-style-equivalence.json` records both actual byte fingerprints. Live receipts
retain their original fingerprint **284f92c8f54b033331d4074947ceb5b67a71a6e07de3a6bed13447194fcb3e1c**. A fresh offline
all 55 replay and full tests revalidate the formatted delivery; this is explicitly
not relabelled as another live model run. Source recipe files remain Agent-authored.

Full final data: `cohort-repair-v5-live-g1/funnel.json`; target checks in that directory;
SQL in `verification-source.sqlite3`; immutable service revisions in `recipe-service/`.

## H5 verification incident

An early build mistakenly overwrote served H5 output. It was disclosed and restored
to the previous **recorded** published release: all 117 files matched its manifest,
SELinux labels were preserved and local TLS index bytes matched. The mistaken
build was archived, not deleted. This is not an immediate pre-incident snapshot or
proof about unrecorded edits/browser caches. No Nginx configuration, backend restart
or WeChat upload occurred. Subsequent builds used isolated output paths. Do not
state that the website was never touched. See `h5-recovery.json`.
