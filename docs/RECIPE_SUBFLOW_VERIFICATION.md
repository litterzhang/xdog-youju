# Native subflow verification (2026-10-25)

## Delivery and ownership

The current contract is recipe-runtime-v3 / youju-source-recipe-v5. The main Flow
contains native parse/detail/finish subflows. Scripts and model nodes are executed
by the official xdog-flow 2.4.9 kernel; source child graph equality is no longer an
acceptance rule. Conditions select general/undated scripts, quick failures and
optional detailed extraction in the artifact itself. See RECIPE_WORKFLOW.md.

Key implementation files:

- recipe_runtime/workflow.py: default inline graphs and explicit edge conditions.
- recipe_runtime/flow_policy.py and contracts.py: capability, stage-interface and
  canonical draft checks before the official SDK validator. No code/path imports.
- recipe_runtime/runner.py: pinned operation bindings, isolated parser execution,
  host-normalized output, model-permission guard and SDK execution, not a scheduler.
- extraction_service.py: application model-port binding and portable error mapping.
- onboarding/xdog.py: existing provider/capped-stream reuse for native Flow agents;
  official NodeStubs for no-provider tests, with no nested model workflow.
- RecipeService author prompts/guide: editable child graphs, official Flow skill and
  capability, current persistent-session profile and actual-file validation.

Every child uses the SDK's host-bound single-concurrency execution setting. Source
JSON cannot increase execution authority. Budgets span a detailed run, while a new
SDK agent invocation gets fresh submission-ID tracking. Multiple sequential native
agent nodes are supported; no shared source-client or second Agent engine is added.

## Verification boundaries

### Native graph and model boundary

Focused tests execute real subflows, mappings and conditions. They verify child
node/condition edits are accepted and actually change parser choice; quick-route
model spend is denied even for an erroneous edited Flow; malformed model output
keeps its original failure and attempt provenance. Unsafe tools, skills, CLI,
provider/model changes, inline code, paths and weakened draft schemas are rejected.

Explicit offline model tests use the SDK NodeStubs port, so provider discovery is
forbidden. Additional tests exercise the real SdkRunner/Agent/capped-stream path,
replacing only the provider with an in-memory synthetic provider: successful
submission, provider error, token overrun, cancellation/stream cleanup, and two
sequential agents whose provider call IDs repeat across separate Agent contexts.
These are SDK integration tests, **not real LLM calls or accuracy measurements**.

### Authoring in the real Linux sandbox

Four opt-in real bubblewrap scenarios verify official filesystem/Flow tools,
read-only task input, current template execution and native session lifecycle.
The author-provider stream is synthetic. The native coding Agent writes an edited
child Flow through the actual filesystem tool, submits it to RecipeService, and
runs it in the real script sandbox. Its changed conditional edges select the
undated parser even for a fixed-date fixture. This is an execution/capability proof,
not a claim that a paid model independently designed a better source workflow.

### Saved USTC cohort

The previous 55-case cohort is replayed at the same frozen clock,
2026-10-24T13:55:03+08:00. The fixture combines the new default native topology with
unchanged bytes of the prior gpt-6.1-sol-authored source scripts. It is explicitly
**not** a newly Agent-authored/admitted v3 Recipe or an automatic migration.

- 33 workflows complete: success 3, partial 28, review 2.
- 31 nonempty results contain 63 position entries. All 33 completed position arrays
  match the earlier replay exactly; no prior position facts were extraction inputs.
- 22 executions retain MODEL_DISABLED because detailed inference is deliberately
  unavailable; these are unfinished, not successful parses or website blocks.
- Top-level SDK events show parse/detail/finish for completed cases and parse/detail
  for those stopped before normalization. Script calls identify actual OS-sandbox
  general/undated execution. Flow diagrams are rendered by the official SDK.
- Zero network requests, downloads or model calls during replay. No source binding,
  database migration, activation, callbacks, deployment or WeChat upload occurs.

Artifacts live under server/.artifacts/native-subflows-20261025/:
workflow.json, main/parse/detail/finish .txt/.mmd, topology-fixture.json,
ustc-55-native-final.json and matching log, real-sandbox-final-v2.log,
native-model-transport-final.log, and backend-final-v2.log.

The original source snapshot remains SHA256
4feeb8752f4547c769912efdb408259778c50f4eaebd0e37d67d58d3b17f790c.
Root AGENTS.md is unchanged. Existing live SDK environment and source/Recipe
admissions are not silently upgraded. Deployment and live-model verification need
separate operator action; no production or frontend/device verification is claimed.

## Final test receipt

- Backend full suite: **1395 passed, 4 opt-in skipped**.
- The four real Linux sandbox scenarios: **4 passed** when explicitly enabled.
- Focused native-Flow/model-transport checks: **18 passed**, including synthetic
  provider execution of real SDK Agents, budget failures and cancellation cleanup.
  These overlap the full suite; counts are not additive coverage measurements.
- Ruff lint and formatting pass (237 Python files).
- Final SDK package versions are xdog-agent/xdog-ai/xdog-flow **2.4.9**, using the
  existing isolated locked environment, not an update to the live environment.

The consolidated machine-readable receipt is
server/.artifacts/native-subflows-20261025/final-verification.json. It verifies the
current runtime fingerprint, matched completed USTC position arrays, immutable
source snapshot and unchanged root AGENTS.md. No frontend/device build or live
model/source deployment is included in this refactor.
