# Native Recipe subflows

## Runtime graph

The main artifact uses exactly three **native xdog-flow `subflow` nodes**. Each
child is an inline workflow in workflow.json, versioned with the source scripts.
The SDK, not a Python scheduler, executes conditions, data edges and child graphs.

```text
$in -> parse [subflow] -> detail [subflow] -> finish [subflow] -> $output
```

### Parse subflow

```text
review (independently reviewed change/cancellation context)
  |-- cancelled ------------------------------------> child output
  |-- non-cancelled + undated category -> undated_parser.py
  |-- non-cancelled + other category   -> parser.py
                                             |
                            failed + quick --+--> explicit failure node
                            otherwise -------+--> child output
```

The two source-script nodes have fixed isolated capabilities `recipe:parse` and
`recipe:undated`; both execute Agent-authored parse(ctx, bundle, config). Their
Python adapter reports success/failure and a draft; it never chooses a branch.
Separate error nodes share one error operation, avoiding a join of mutually
exclusive failure paths in the installed SDK. No SDK patch or Python scheduling
workaround is used.

### Detail subflow

```text
inspect (validate draft, expose position_count)
  |
  |-- cancelled / quick / supported nonempty draft --> child output
  |
  |-- detailed AND not cancelled AND
      (unsupported OR needs_detailed OR zero positions)
        -> prepare pinned model input
        -> extract [native agent node]
        -> merge and validate drafts
        -> child output
```

These decisions are actual edge `when` expressions (`and/or/not/equals`), not an
if/else block inside a script resolver. The extraction Agent is directly inside
this child graph; it does **not** launch XdogModels.complete's second workflow.
Only structured submission is available, with no browsing, filesystem, skills,
CLI backend or arbitrary tools. Provider/model are supplied by the host's existing
configured xdog transport and request accounting, never by source JSON.

### Model configuration

Native extraction nodes use the host's independent `flow_model`, defaulting to
`gpt-5.4-nano`. Set `YOUJU_FLOW_MODEL` for managed workers or `--flow-model` for
foreground source/Recipe workers. This does not change classifier `small_model` /
`large_model` or the Recipe author Agent's `author_model` (`gpt-6.1-sol` default).
The portable `runtime:large` node identifier remains a host-bound symbolic alias,
not permission for a Recipe to select the classifier's large model.

Model identity and the `flow` tier are recorded per native extraction session.
A missing/unavailable requested model is an error, not a reason to substitute a
more expensive model. Live execution still requires explicit `--enable-models`
or `WORKERS_ENABLE_MODELS=true`; application startup and default tests never opt in.

### Finish subflow

```text
normalize -> child output
```

The normalization operation is a shared domain boundary: independent exact-date
resolution, reviewed changes/cancellation, capture diagnostics, typed normalization
and result validation. These are business computations, not workflow routing.
It produces position-facts-v3 and runtime-owned script/model-use receipts. Source
code cannot replace this operation with its own publication or validation code.
Storage, admission and publication occur after the main Flow in the runtime app.

## Authoring and edit policy

RecipeService's persistent coding Agent uses the official Flow skill/capability
and writes workflow.json, parser.py, undated_parser.py, config.json. The contract is
recipe-runtime-v3 / youju-source-recipe-v5; position-draft-v1 and position-facts-v3
are unchanged. No old artifact is silently upgraded or admitted.

**The template is a starting point, not an equality whitelist.** The Agent may edit
Flow names, child IDs, prompts, conditions, edges and nested inline subflows when
source cases justify it. Main stages and packet/result interfaces remain explicit.
Graphs use approved leaf references and finite edges, with bounded node/depth size.
The SDK executes children with a host-bound concurrency of one; source JSON cannot
raise it. Multiple sequential model nodes share the configured run budget, while
submission-ID tracking resets for each SDK Agent context.
No inline Python, external subflow paths, arbitrary imports, model/provider
selection, tools, scheduling or CLI execution authority can be added. Validation
checks capabilities/signatures first, then invokes the official SDK validator.

Runtime guards are permission/data backstops, not a second orchestrator: inputs
stay pinned, quick routes cannot spend model budget even if an edited graph is
wrong, and only a host-normalized result can leave the run. An invalid graph fails
explicitly; host code does not quietly replace it with the default graph.

The existing model-client wrapper supplies a lazy native-Agent stream. Explicit
no-network tests use the official NodeStubs boundary; real subflows/conditions and
scripts still run, while no provider discovery or SDK inference Agent is created.
This is a model substitution, not a fake Flow engine.

Use the sandboxed official Flow tools for structural validation/graph inspection,
and python -m youju.onboarding.author_check for execution with project operation
bindings. Submission validates actual files through the same shared runtime.
Source audit/review/repair remains separate from generation and sample validation.
