# Reviewed Temporal Rules and Notice Changes

## Current timing policy override (October 24, 2026)

`application-evaluator-v3.0` replaces the earlier completeness-dependent expiry
policy. Acquisition status, missing attachments, partial/truncated reader output
and capture warnings do not invalidate dates or positions supported by available
text. Missing factual evidence remains unknown; actual purpose/scope conflicts,
negation and unresolved relative anchors remain timing issues. No material state
is used as a generic date veto. Historical receipts below describe their original
behavior and are not silently rewritten. See `APPLICATION_TIMING_REDESIGN.md`.


Status: local implementation, October 23, 2026. Not deployed. No real holiday
calendar or USTC association was invented/registered by this implementation task.

## One rule contract, separate evaluation

`application-period-v2` now carries optional `derivation`, `context_refs` and
`amendment` metadata. The shared evaluator is `application-evaluator-v2.2`.
Adapters still extract rules; only the host computes validity from an injected
aware clock. The result distinguishes `derived_deadline`, `amended_deadline`,
ordinary explicit cutoff, and confirmed cancellation disposition.

A literal primary-page publication marker and original material version can anchor
unambiguous rules such as the nth natural/work day **after** publication or a
stated count starting on publication's next day. The anchor day is excluded; the
computed end day is inclusive. Merely saying "within N days starting on publication"
does not establish this counting convention. Capture time, URL years and machine
current date are never substitutes for publication evidence.

Natural days are deterministic date arithmetic. Workdays require an explicitly
reviewed complete contiguous day map, not a weekday guess. Separate scope headings,
rounds, conflicting publication markers, unsupported qualifiers, missing calendar
coverage and unassociated amendments prevent whole-notice coarse routing. Known
rule text remains visible even if no expiry decision is supported.

## Reviewed calendar input

The host-only `SourceCalendarReview` ledger stores immutable source-specific policy
versions. No model/adapter supplies the trusted calendar. The source attaches it only
when a supported workday expression needs it; ordinary fixed dates do not depend on
calendar changes. A changed review invalidates old derived observations and cached
rules; a new audit generation is required rather than mutating existing rows.

Synthetic example only, **not a claim about real working days**:

```json
{
  "name": "synthetic-reviewed-calendar",
  "timezone": "Asia/Shanghai",
  "days": {"2026-10-20": false, "2026-10-21": true, "2026-10-22": false}
}
```

All dates in the covered interval must be present, including holidays and adjusted
work weekends. Human review and an independent provenance reference are explicit:

```sh
# From server/, with the isolated environment and COPY paths only.
uv run --frozen flask --app youju source-period-calendar SOURCE_ID \
  --calendar /absolute/reviewed-calendar.json --reviewer local-human \
  --reference reviewed-calendar-provenance --confirm-reviewed \
  --database /absolute/copy.sqlite3 --storage /absolute/copied-blobs
```

This command does not download a calendar, queue a model, reparse or publish jobs.
Registration is blocked during leased source work/pending repairs. Unknown calendar
coverage stays unknown; calendar data is not embedded repeatedly in unrelated pages.

## Explicit original/change association

`AnnouncementChangeReview` pins two distinct settled saved bundles of the same
source, literal target URL linkage, effect, reviewer/reference, and explicit
supersession. Similar titles, later timestamps and model confidence cannot establish
association or precedence. Registration currently accepts complete text-only
notice-wide extension/cancellation pages. Mixed job/condition changes, partial
scopes, contradictory/conditional wording and unsupported material remain review.

```sh
uv run --frozen flask --app youju source-associate-change SOURCE_ID \
  ORIGINAL_BUNDLE_ID EVENT_BUNDLE_ID --effect extension \
  --reviewer local-human --reference reviewed-association-provenance \
  --confirm-reviewed --database /absolute/copy.sqlite3 \
  --storage /absolute/copied-blobs
```

Use `--effect cancellation` for a verified whole-notice cancellation. A replacement
review must explicitly name `--supersedes CURRENT_REVIEW_ID`; later timestamps do
not silently replace prior reviews. An extension cannot guess unresolved original
scopes or shorten their known cutoff.

The host attaches original event evidence without renumbering its block IDs and
recomputes scope/date semantics independently. An extension replaces the older rule
only while its target/event versions remain current. Replacement, pending refresh
or stale event material quarantines prior temporal claims until reviewed/re-audited.
Original raw material, old rules and audit history are retained.

Confirmed cancellation has `disposition=cancelled`, `status=unknown` for clock expiry
and `coarse_eligible=false`. It creates no positions and invokes neither a job
parser nor detailed extraction model. Independent full-position expectations are
not misused as completeness obligations for a withdrawn notice. A source containing
only confirmed withdrawn/archived saved notices may finalize without inventing jobs.

Associated change pages remain classified/monitored but are excluded from duplicate
vacancy extraction; source summaries count them separately. Unassociated events stay
in the existing review path. Public/source/incremental projections and final adoption
recheck temporal dependencies instead of using an old passing receipt blindly.

## Persistence, cost and recovery boundaries

`period_context` is assembled by the source host, not parsed from page instructions.
Generated scripts cannot replace the parent Flow's immutable bundle. Context versions
and evidence are included in rule/test material hashes; a clock change only recomputes
time. Calendar or association updates do not silently enable incremental monitoring,
start an author or rewrite historical facts. Run saved-source analysis in a new
generation before publication. Existing SDK/provider/skill checkpoint fences remain.

The explicit additive `source-accuracy-migrate` command includes the two temporal
review tables. Do not run it against production without separate approval. Existing
rows are not relabeled as full; no native 2.4.1 checkpoint is silently restored under
2.4.9. Copy-only migration receipts live in the private artifact directory.

## Evidence and limits

Synthetic tests cover publication-day exclusion, inclusive boundaries, missing/
conflicting anchors, real-vs-capture identity, workday maps with adjusted weekends,
calendar gaps/revisions/cache invalidation, literal association and target linkage,
partial/conditional cancellation rejection, explicit supersession, stale events,
model-free withdrawal, and full onboarding without duplicate event positions.
These are implementation checks, not labeled real-source accuracy measurements.

The parser deliberately supports a small independently checkable grammar rather
than allowing every source adapter to invent time semantics. Unsupported working-day
wording, partial-scope corrections and unknown rules continue full parsing/review.
No selective period model resolver is enabled when its proposed rule cannot pass an
independent host checker; repeated per-page inference is not used to hide this limit.
Independent multi-source semantic measurement and copy-only native cutover remain
in the active redesign goal.

Recorded verification: **511 backend tests passed, two optional sandbox tests
skipped**; final temporal boundary follow-up: **34 passed**. Both real sandbox tests
were separately enabled and passed. Client type checking and **114 tests** pass;
H5/weapp builds use isolated output roots, not deployment. H5 retains the existing
webpack performance warning. The same 40 saved USTC materials replay with zero
network/model calls: 18 complete script/contract checks, three remain unfinished
without a detailed model and 19 are not resolved recruitment articles in this
rules-only run. All material evidence identities are preserved. These remain
unlabeled one-source observations, not accuracy measurements.

The copy-only migration adds the six rule/semantic/temporal tables, preserves exact
row digests in all **30 existing tables**, and is idempotent over two invocations.
No real calendar or association was registered. Detailed receipts:
`server/.artifacts/recipe-redesign-20261023/temporal-verification.json` and
`temporal-migration.json` in the same directory.
