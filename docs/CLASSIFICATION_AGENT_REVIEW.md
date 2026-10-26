# Batched AI Resource Classification Review

## Responsibility and execution

Rules and the local classifier remain the first path, with a 95% low-cost coverage
**target**, not a claimed result. Existing small/large classification escalation
retains its own budget. A separately enabled read-only high-tier Agent reviews
uncertain current assessments in batches; there is no human approval stage.

```text
saved pages → cheap classification / existing escalation
            → unresolved queue → bounded batch Agent review
            → per-page validation → accepted classification + host scan cadence
                                   or explicit machine-unresolved result
            → Recipe generation / extraction, under their own contracts
```

The runtime owns the queue and decisions. RecipeService still owns only authoring
and repair; reviewers cannot modify recipes, fetch pages, run commands, extract
positions or publish jobs. The existing official xdog adapter executes one native
Flow Agent node per batch, using `gpt-6.1-sol` by default. No new model client or
Agent framework is introduced.

## Batches and independent adoption

Default batch size is eight pages. The batch payload is capped at **128,000 UTF-8 bytes**, with
a hard configurable ceiling of 256,000. The next page starts another batch rather
than exceeding the cap. The SDK separately caps the full Agent request at the
payload limit plus 16 KiB for trusted schema/prompt overhead (always below 1 MB).
An individually oversized/unreadable page stays unknown;
missing attachments alone do not make readable page content unreviewable.

Each page has its own ID and page-scoped evidence namespace. The host checks its
schema, original evidence, intent guards and current bundle/assessment identity.
Valid pages may be adopted even when a sibling is missing, malformed, duplicated
or cites another page. Truncated views cannot establish unrelated certainty.
Cadence remains host-owned. Labels never propagate from one sample to other pages.

Original assessments, model answers and batch inputs have immutable blob references
in `SourcePipeline.summary.classification_agent_review`. Adoption updates the
current assessment and scan policy together only if the original is still current.
This is AI provenance, never human gold, independent accuracy or 95% cheap coverage.

## Budget and recovery

Defaults allow **three batches per source**, at most one batch per worker pass.
This is an independent explicit review budget, not a silent refill of the ordinary
classification budget. Model/profile changes and restarts do not reset it. Raise
`YOUJU_CLASSIFICATION_REVIEW_MAX_BATCHES` explicitly for a larger approved budget.
A batch reservation is committed before dispatch; cancellation/unknown dispatch,
provider errors and inconclusive results are not automatically charged again.

Counters distinguish resolved, invalid, inconclusive, stale, failed, indeterminate
reserved pages, pending AI work, budget-deferred pages and capture/input problems.
SDK usage is retained when reported; dollar cost is not inferred. Budget exhaustion
or abstention never requires a human or prevents the rest of onboarding progressing.
It also does not imply every ambiguous page was successfully classified.

## Enablement and operator command

Enable `YOUJU_CLASSIFICATION_REVIEW_ENABLED=true` in the source worker configuration
for future classification and incremental passes. Other settings are documented in
`server/.env.example`. Enablement/restarting a live worker is a separate operation;
never change classification pins during an in-flight author/build or audit.

From `server/`, inspect the safe default (no provider call or metadata write):

```sh
uv run flask --app youju:create_app source-classification-review SOURCE_ID
```

An explicitly authorized one-batch invocation adds `--enable-models` and explicit
`--database` / `--storage` paths. It requires idle classification state and uses its
recorded classifier key. Repeating the command consumes only never-dispatched
pages and cannot reset the source budget. It does not resume or reclassify a source.

## Verification boundary

Tests use synthetic saved pages and the official SDK's offline stub port. They
cover multi-page adoption, per-page failure isolation, cross-page evidence,
deduplication, stale outputs, cancellation, source budget and input/capture limits.
No paid model experiment or production worker restart is part of this change.
