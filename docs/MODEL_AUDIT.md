# Independent model audit (standalone, not wired to lifecycle)

`server/src/youju/recipe_service/model_audit.py` provides an independent official
xdog 2.4.9 `Agent` opinion over frozen original text and current rejected/review
output. It does not restore the author, load flow skills/capabilities, run host
commands, access source storage, publish, or write semantic expectations. Default
tests are offline. A separately authorized one-case real audit is recorded
in `SOL_FLOW_INTEGRATION.md`; it is not semantic-accuracy certification.

## Host / CLI API

```python
from youju.recipe_service.model_audit import ModelAuditService, freeze_batch

# models is the application's existing XdogModels adapter. No ambient .env loading
# occurs here, and config.author_model is deliberately NOT the audit model selector.
cases = freeze_batch([
    {
        "case_id": "host-case-reference",
        "documents": [
            {"document_id": "saved-document-version-id", "text": "Original saved text"}
        ],
        "current_output": {"positions": []},  # Original rejected/review JSON, not gold.
        "output_status": "review",            # Exactly "review" or "rejected".
        "issues": [{"code": "HOST_REVIEW_REASON"}],
    }
], max_cases=1)

service = ModelAuditService(
    "/explicit/new-or-consistently-copied-audit.sqlite3",
    models,
    live_opt_in=False,  # True ONLY on an explicitly authorized live operation.
)
try:
    reference = service.reference(cases[0])  # No reservation or model/provider I/O.
    # Save reference in the caller operation receipt BEFORE awaiting run_batch.
    # Live execution also requires models.config.live_enabled=True.
    receipts = await service.run_batch(cases, max_cases=1)
    saved = service.get(reference)
finally:
    service.close()
```

`run_batch` accepts the same list of JSON dictionaries directly; `freeze_batch` is
optional for callers wanting to freeze before dispatch. Lists/tuples must contain
1–3 entries; `max_cases` must be an actual integer from 1 to 3. Entire-batch
validation precedes reservations. Unknown case fields are rejected. Each case
requires 1–100 unique document IDs with nonempty original text, JSON-object output
and a list of issue objects. Inputs are canonical finite JSON snapshots, at most
1,000,000 UTF-8 bytes **per case**, without silent truncation. Host preparation owns
faithful original-text conversion, material completeness and authorized selection;
this module never fetches a URL, parses attachments, or trusts caller labels as gold.
Do not put credentials/private applicant data into inputs. Audit storage retains
frozen text and untrusted AI opinions; apply appropriate filesystem access controls.

Constructor requirements: explicit `.db`/`.sqlite`/`.sqlite3` file, existing parent,
no file symlink, no non-audit tables. Use a new dedicated audit file or a consistent
copy of an existing **audit ledger**, never the source/sidecar/production database.
No tables are attached to the application's model metadata. `reference(case)` is
content-derived (not secret); `get(ref)` returns saved status, model/prompt/material
identity, request count, verdict, usage, response IDs and timestamps. Unknown refs
raise `KeyError`. This is a trusted internal API, not an authenticated public route.

## Limits, opt-in and recovery

- Exact dispatch model: **`gpt-6.1-sol`**, independent of `AUTHOR_MODEL`, managed
  profile names and `config.author_model`. No model fallback or model synchronization.
- New Agent per case, no author/audit history, skills, compaction, web search or
  host execution capabilities. Only host-owned `submit_audit` is exposed as a
  result sink; it cannot mutate recipes/expectations/admission.
- At most **one provider request / one turn per new audit identity**, 4,096 output
  tokens, sequential execution. Invalid/missing submission does not buy another
  request. Timeout defaults to 120 seconds, configurable in `(0, 600]`. These are
  audit bounds, **not** changes to the author Agent's lifetime policy.
- Live execution requires both `live_opt_in=True` and existing
  `models.config.live_enabled=True`. Default construction does not enable calls.
  Tests inject a trusted `stream_factory` and never create a live provider.
- Live protocol fence: installed xdog-agent/xdog-ai must be 2.4.9, the existing
  Copilot provider must locally advertise this exact model (possibly with the
  `copilot/` catalog prefix), tools and effective `openai-responses`. The module
  uses official `stream_fn_from_provider`. SDK 2.4.9's Completions adapter creates
  an OpenAI client with implicit transport retries; it is intentionally refused.
  No replacement client, monkeypatch, CLI executor or provider fallback is used.
  Downstream gateways/providers' internal billing behavior is outside this ledger.

A SQLite primary key covers original documents, output, issues/status, fixed
prompt/schema, exact model, provider, SDK and live/synthetic mode. Case labels
cannot bypass deduplication. An atomic committed insert wins the reservation;
competing processes/instances return its existing receipt. Before official
provider entry a committed compare-and-swap moves `reserved` to
`dispatch_unknown` and sets request count to one. SQLite uses synchronous FULL.
There is no lease expiry, automatic retry, reclaim, resume or force flag:

| Status | Meaning |
| --- | --- |
| `reserved` | Reserved; a lost host may have stopped before dispatch. Never reclaimed. |
| `dispatch_unknown` | Provider entry authorized; completion/charge may be unknown. Never redispatched. |
| `completed` | Valid AI submission saved, including possibly `inconclusive`. |
| `failed` | Invalid evidence/submission, timeout, provider/cleanup/storage-observation failure. |
| `cancelled` | Caller cancellation saved; cancellation propagates. |

All saved identities remain deduplicated, including failures with zero known
requests. Conservative reservations may consume the opportunity without charging;
this is preferable to duplicate charges after an ambiguous interruption.
Cancellation preserves the ref/status before awaiting cleanup. If persistence itself
is unavailable, the previously committed reservation/dispatch marker remains the
fail-closed recovery boundary, not an invitation to retry.

Reopen the **same durable ledger**, or restore a consistent copy containing its
reservations. Never restore a snapshot predating dispatch, delete rows or fork two
writable copies expecting global deduplication: no local SQLite implementation can
coordinate lost history or independent ledgers. Changes to material/output/issues,
model/provider or prompt identity are new audits and require fresh host selection
and authorization. This module does not automatically select them.

## Verdicts and accounting

The submission schema permits `consistent`, `model_suspected` or `inconclusive`,
a summary and up to 32 field diagnostics. Field paths use JSON pointers relative
to `current_output`; absent fields may be diagnosed. Supported/suspected findings
require literal evidence with original `document_id`, Python character `[start,end)`
offsets and exact quote. Unknown IDs, invalid offsets/mismatched quotes and
unsupported conclusive verdicts fail the audit; they never become accepted
opinions. An inconclusive diagnostic can explicitly omit evidence. Exact quote
validation proves location only, **not semantic correctness or material completeness**.
`consistent` applies only to the cited fields, never global extraction accuracy.

Host-added result fields are `provenance=["ai_audited"]` (also
`"model_suspected"` when a diagnostic suspects an error),
`basis="ai_opinion_not_gold"`, `human_confirmed=False`, `accuracy_verified=False`.
The model cannot submit arbitrary provenance or gold fields. Results never enter
`semantics.check_expected` as human-confirmed/independent-rule expectations.

Receipts retain SDK `UsageEvent` counters (including cache and reported totals)
without recomputing them and available provider response IDs. All-zero/default
usage remains `usage_status="unknown"`; a failed/partial run is not assumed free.
`sdk_usage[].cost` is the SDK's reported/catalog-derived value, which can even be a
premium multiplier, **not a verified provider invoice**. `cost_usd=None` and
`cost_status="unknown"` deliberately avoid fabricated dollar costs. `requests=1`
means dispatch was authorized, not proof that a provider billed exactly once.
Exceptions are reduced to fixed host codes, not persisted provider exception text;
stream exceptions are redacted before reaching SDK error logging.

## Offline verification

From the repository root (existing isolated environment, frozen dependencies):

```sh
export UV_PROJECT_ENVIRONMENT="$PWD/server/.artifacts/recipe-redesign-20261023/venv"
uv run --frozen --offline --project server pytest server/tests/test_recipe_model_audit.py -q
uv run --frozen --offline --project server ruff check \
  server/src/youju/recipe_service/model_audit.py server/tests/test_recipe_model_audit.py
```

Tests use real official Agent execution with synthetic streams, including a
mocked provider through the official adapter. They cover process races, reopened
and copied-ledger deduplication, max-batch and whole-batch prevalidation,
independent contexts/submit-only tool exposure, invalid evidence/provenance,
unknown reservations, provider failures, cancellation, timeout, protocol/model
fences, actual SDK usage propagation and unknown costs. No paid/network/deploy,
production data/venv migration, real model quality, browser or device verification
is part of these tests. CLI wiring and review selection remain host integration work.
