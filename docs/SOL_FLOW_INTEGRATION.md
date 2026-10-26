# Sol Authoring, Small-Batch Audits and Flow Capabilities

Status: local implementation; not a production rollout. The live environment and
stored native sessions are not rewritten by changing defaults.

## Agent responsibilities

New author defaults select `gpt-6.1-sol`. Provider/model overrides are explicit;
there is no automatic downgrade when that model is unavailable. Author sessions
retain native compaction, persistence and progress-based stopping, without a
lifetime turn/token/time cap. Ordinary classifiers and detailed extraction keep
their own existing models and budgets; they do not become Sol calls per page.

The independent auditor is a separate, tool-free coding-disabled Agent context.
It sees frozen original material, current rejected/review output and concrete
issues, not the author's conversation. Only `submit_audit` is available. Its
`ai_audited` / `model_suspected` conclusions are opinions, never human-confirmed
gold, publication authority or permission to change expectations. Host validation
checks schema and literal evidence locations; those checks alone do not establish
semantic correctness. Unresolved evidence remains explicitly inconclusive.

## Official Flow capability, not another executor

`SandboxedFlowCapabilities` discovers the official installed application-scoped
provider using `CapabilitySet("application")`, starts it with `ApplicationPort`,
and assembles Agents using `CapabilityAssembly`. The official `flow` tool schema
and packaged `flow` skill are both present. Unknown installed application
integrations fail closed rather than silently acquiring author authority.

The existing sandbox transport replaces only the tool's execution boundary. The
raw parent CLI executor is never published. In the isolated child, the official
capability is started and its real `flow` tool executes the CLI. No FlowTool,
workflow scheduler, model client or workflow engine is reimplemented.

Permitted commands: `validate`, `run`, `generate`, `graph`, `test`, `--help`.
Scheduling, interactive `build` and deployments are not authorized. Even validation
can import generated code: all commands retain the existing networkless namespace,
read-only `/input` and `/history`, absent host credentials and per-tool OS limits.
Cancellations terminate the owned child; interrupted effects are not retried.

The service closes Agents and capability sets after each task. Restoration uses
current capability assembly plus the existing immutable checkpoint fences. The
`flow-application-sandbox-v1` capability identity is included in checkpoint bindings.

## Budget and audit command

From `server/`, use an explicitly selected environment. The following is a dry-run
and makes no model call or audit-ledger write:

```sh
export UV_PROJECT_ENVIRONMENT="$PWD/.artifacts/recipe-redesign-20261023/venv"
uv run --frozen python scripts/run_model_audit.py \
  --input /absolute/frozen-audit-cases.json \
  --ledger /absolute/private/model-audits.sqlite3 --max-cases 1
```

Add `--enable-models --provider copilot` only for an authorized paid batch.
The hard batch ceiling is three cases; the command defaults to one. Each new audit
identity gets at most one provider request, with a bounded response and timeout.
Material/output/prompt/model identities deduplicate across processes and restarts.
Unknown dispatch, provider failure, invalid output or cancellation never buys an
automatic retry. A ledger receipt reports actual SDK usage and leaves dollar cost
unknown when no invoice is available. The ledger is dedicated storage, not the
source or RecipeService publication DB. See `MODEL_AUDIT.md` for case schema.

## Explicit enablement and history preservation

New native profiles use `source-author-flow-v2` and `source-recipe-v2`. Existing
profiles/checkpoints are immutable and are **not** silently granted the new tool
or switched from Astra to Sol. A prior profile is rejected by the new runtime;
preserve it and use the explicit fresh-session procedure in `RECIPE_SDK_CUTOVER.md`.
That procedure archives old files/history as untrusted read-only context.

Before any approved rollout, configure `YOUJU_AUTHOR_MODEL=gpt-6.1-sol` consistently
for source-server and RecipeService, use matching new profiles, commit current
input, init/build, audit saved-source results and explicitly adopt the candidate.
No callback or model-audit receipt activates a recipe. The actual local `.env`,
production `.venv`, service units and publication pointers were not changed.

## Verification receipts

The October 24 targeted verification executes official native Agent restore with
both the Flow tool and skill present. A real-bubblewrap Flow command runs an inline
script, verifies absent host secrets/private files and immutable inputs, and rejects
scheduling. A planted `/work/xdog` package cannot shadow the official CLI because
child Python processes use `PYTHONSAFEPATH=1`. All author/provider test streams are
explicit synthetic fixtures, not paid author experiments.

One separately authorized real audit used one Copilot `gpt-6.1-sol` request on saved
USTC material. It confirmed the observed `2025-10-08` declared submission date but
returned `inconclusive` on whole-scope expiry because material completeness was
unresolved; it did not manufacture a correction. SDK usage: 2,163 input tokens,
1,720 output tokens, 3,883 total. Dollar cost remains unknown; catalog-reported
zero is not treated as a free invoice. Private receipts are under
`server/.artifacts/sol-flow-20261024/`; no source data/admission was changed.

The full saved-source replay also exposed a generic-template capability bug:
`NO_POSITIONS` with otherwise valid empty output was retained as a full-parse
review envelope on the confirmed-expired coarse branch. The trusted template now
declares `extraction=null` for that exact unsupported-no-rows case and retains its
diagnostic. The host's existing coarse-empty route can then archive zero jobs;
current/unknown cases still require detailed fallback. Script failures and all
other review reasons remain unchanged. Real-sandbox regression tests exercise
both branches. Original replay artifacts are retained; current-candidate outputs
must be re-executed rather than retagged.
