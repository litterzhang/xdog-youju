# Optional Recipe runtime reverse queries

## Delivered boundaries

The official xdog Agent can optionally read saved source notices and diagnostic
parse results through `list_notices`, `read_notice` and `read_parse_result`.
These are native `AgentTool` registrations, not another Agent framework. No
extension means no reverse-query tools, including after checkpoint restoration.
The frozen `case_tools` remain separate and unchanged; retrieval is not a frozen
test, semantic gold, acceptance, publication or permission to run a workflow.

Components:

- `recipe_service/runtime_extension.py`: local Protocol, immutable host binding,
  bounded read-only HTTP transport. No source runtime/repository/DB imports.
- `recipe_service/runtime_tools.py`: official SDK tools and independent argument,
  response and cancellation checks. Returned material is **untrusted data**, not
  instructions or accepted facts.
- `runtime_queries.py`: source-side Flask endpoint, authoritative source/task
  authorization through an injected control-plane metadata port, and read-only
  **source** SQL/blob access. It never opens sidecar storage or constructs RecipeService,
  acquire source/assignment locks, migrate schemas, fetch, parse, call a model,
  modify records, notify, accept or activate anything.
- `recipe_service/production.py`: optional configured factory wiring only. No
  new source DB dependencies are introduced into the extension/tool path.

This implementation has offline SQL/Flask/transport verification only. It does
not enable a deployment, configure live secrets or prove real HTTP/TLS reachability.
This scoped work does not edit native prompts or the main Recipe artifact contract.

## Host configuration and production wiring

The source application must explicitly enable its endpoint:

| Configuration | Meaning |
| --- | --- |
| `RECIPE_RUNTIME_QUERIES_ENABLED` | Must be boolean `True`; disabled otherwise |
| `RECIPE_RUNTIME_QUERY_TOKEN` | Server-only shared bearer secret |
| `RECIPE_RUNTIME_QUERY_CALLER` | Single authenticated query-service identity; default `recipe-service` |
| `RECIPE_RUNTIME_QUERY_OWNER` | Trusted control-RPC workspace owner delegated to the authenticated reverse-query caller; default `source-server` |
| `RECIPE_RUNTIME_QUERY_AUTHORIZER` | Optional trusted host-injected `TaskStatusAuthorizer` object; metadata still passes central validation |
| `RECIPE_RPC_URL` | Existing control RPC listener; default `http://127.0.0.1:8090` |
| `RECIPE_RPC_TOKEN` | Server-only control RPC secret for the delegated workspace owner (distinct from the reverse-query token) |
| `DATABASE` | Existing source SQLite DB |
| `SOURCE_STORAGE` | Existing source blob root only; no sidecar DB or directory access required |

The Recipe production worker additionally reads:

| Configuration | Meaning |
| --- | --- |
| `RECIPE_RUNTIME_QUERY_URL` | Complete trusted endpoint URL; absent/empty means no extension |
| `RECIPE_RUNTIME_QUERY_TOKEN` | Same server-only secret accepted by the source endpoint |
| `RECIPE_RUNTIME_QUERY_CALLER` | Must match the source endpoint; default `recipe-service` |

An explicitly configured URL without a token raises a fixed configuration error,
not silent fallback to unauthenticated access. A token alone does not enable tools
(the source app may have a token configured before a worker URL is supplied).
Malformed transport configuration fails when constructing the bound extension.
Settings loaded via the Flask environment use the `YOUJU_` prefix, for example
`YOUJU_RECIPE_RUNTIME_QUERY_URL`. No real credentials belong in this document,
profiles, prompts, task input files, model context or checkpoints.

`configured_runtime_extension_factory(config)` returns a synchronous factory or
`None`. Production `main()` constructs it from the **original loaded service
configuration**, not the reduced Recipe Flask app configuration, and passes it to
`run_workers(..., runtime_extension_factory=...)`. Each new `XdogAgentRuntime`
receives that factory and constructs its own assigned binding.

Source-side task authorization uses `TaskStatusAuthorizer.get_status(*, task_id,
workspace_id, owner)`, an asynchronous host port returning `(workspace, task)`
metadata. It does **not** return a boolean grant or task kind: `_task_authorized()`
always validates the metadata centrally, including for host-injected test adapters.
Configure `RECIPE_RUNTIME_QUERY_AUTHORIZER` as an in-process trusted object, not a
client body field or a serialized environment value. Explicit `None` disables
that dependency and fails closed; it does not select another backend.

Without an explicit injection, `RPCTaskStatusAuthorizer` uses the **existing**
`RecipeRPCClient`, with `RECIPE_RPC_URL`, `RECIPE_RPC_TOKEN` and the delegated
`RECIPE_RUNTIME_QUERY_OWNER` as its authenticated caller. The existing client
permits only its configured loopback HTTP listener, rejects redirects, ignores
environmental proxies, bounds response bytes and has a 15-second per-call timeout.
There are no automatic retries or new control RPCs. The adapter calls only
`GetWorkspace(workspace_id)` followed by `GetTask(task_id, after=0, limit=1)`;
fetching task last observes status after the workspace read and limits progress
events to one. Responses and credentials never enter query results.

No authorizer/token, an inaccessible/unauthorized control service, invalid
configuration or a missing/incomplete status contract fails closed with a fixed
403/503 response. Source pending summaries are an additional fence, **never** a
substitute for authoritative task status. There is no local sidecar DB fallback,
schema coupling, RecipeService construction or assignment-lock acquisition in
`runtime_queries.py`. The service alone owns and validates its task/input data.

The endpoint remains disabled unless explicitly enabled with boolean `True` and
a reverse-query token. Normal application registration makes no RPC calls and
opens no task storage. Control metadata queries must remain lease-free: a Recipe
worker can hold its assignment lock while issuing this reverse query. The offline
suite exercises that exact locked-worker path through real Flask RPC handlers.

## Constructor injection and bound identity

```python
from youju.recipe_service.native_runtime import XdogAgentRuntime
from youju.recipe_service.runtime_extension import HttpRecipeRuntimeExtension

# Captured only from trusted host configuration.
def extension_for(binding):
    return HttpRecipeRuntimeExtension(
        binding,
        endpoint=configured_runtime_query_endpoint,
        caller_id=configured_recipe_caller,
        token=server_secret,
        timeout=10,
    )

runtime = XdogAgentRuntime(models, runtime_extension_factory=extension_for)
```

Factory type: `Callable[[RuntimeBinding], RecipeRuntimeExtension | None]`.
Alternatively inject `runtime_extension=already_bound_extension`; supplying both
options is an error. A factory can return `None` for a particular assignment.
Do not reuse a bound extension for a different task.

`RuntimeBinding(workspace_id, source_id, task_id, input_revision)` is immutable:

| Field | Host-owned native source |
| --- | --- |
| `workspace_id` | assigned `task["workspace_id"]` |
| `source_id` | frozen `inputs["files"]["payload.json"]` source ID |
| `task_id` | assigned `task["id"]` |
| `input_revision` | assigned `task["input_revision_id"]` |

Native registration checks the source's canonical workspace digest and physical
workspace directory name, then requires the extension's complete binding to
match. Invalid bindings/factory failures produce the fixed
`NATIVE_RUNTIME_EXTENSION_INVALID` code before model dispatch. Tools recheck the
binding on every call. Caller identity comes only from host transport configuration.

The endpoint/token/factory are not added to checkpoint bindings or tool schemas.
Current host configuration, not an old snapshot, determines restored tools.
Authorized retrieved **results** can appear in normal Agent history/checkpoints;
the server must therefore exclude secrets and independent review answers. Do not
serialize extension objects, authentication headers or raw exception chains.

## HTTP wire contract

`POST /internal/runtime/recipe-queries` targets one complete, host-configured URL.
No Agent-supplied path, URL, caller, source, workspace, task or revision is accepted
by the tool schema. The transport never appends an Agent ID to a URL.

```text
X-Recipe-Caller: <configured service identity>
Authorization: Bearer <server secret>
Accept: application/json
Accept-Encoding: identity
```

```json
{
  "operation": "list_notices",
  "source_id": "source",
  "workspace_id": "assigned-workspace",
  "task_id": "assigned-task",
  "input_revision": "assigned-input-revision-id",
  "cursor": 0,
  "limit": 20
}
```

For reads use `operation: read_notice` or `read_parse_result` and additionally
`notice_id`. All keys are mandatory in HTTP requests; tools provide pagination
defaults. Unknown keys/operations are rejected. Reads never cause a new parse.

Success is HTTP 200 with exactly:

```json
{"items": [{"notice_id": "opaque-id", "untrusted": true}], "next_cursor": null}
```

No `result`/`data` RPC wrapper is added by the endpoint. The official tool wraps
the page as `{"untrusted": true, "data": <page>}`. Nonterminal `next_cursor` is
an integer strictly greater than the request cursor; terminal pages use null.
All endpoint responses are `Cache-Control: no-store` with `nosniff`. Unknown,
foreign, hidden and stale notice IDs have the same fixed denial response.
Database paths, SQL text, filesystem exceptions and credentials are not exposed.

## Authoritative server authorization on every page

The authenticated caller is a single configured machine identity delegated to the
configured sidecar workspace owner (default `source-server`), not an Agent claim.
Authentication precedes database access. Every query validates the complete tuple
**caller / source_id / workspace_id / task_id / input_revision** against both
ownership domains:

1. Authenticated `GetWorkspace` must return the exact workspace, source, configured
   owner and managed lifecycle. `GetTask` must return the exact task/workspace/input
   revision. Its nested `assignment` metadata must independently agree on source,
   owner and lifecycle. Control RPC authenticates the delegated owner and enforces
   its workspace ownership and source permission; reverse-query caller identity
   comes only from server configuration and authenticated headers.
2. `assignment.active`, `assigned`, `input_revision_valid` and `session_current`
   must each be exactly boolean `True`. RecipeService computes these facts against
   its own current task/workspace/input revision: running, assigned owner, no
   cancellation, supported init/build/repair kind, managed workspace, exact input
   workspace/digest and current session, plus current init ID for init tasks.
   Source validation additionally requires running status, exactly false
   `cancel_requested`, matching integer task/workspace session revisions, no
   finalized result/revision reference and the current init ID when appropriate.
   Queued/waiting, succeeded/candidate-ready, failed, cancelled, blocked and
   interrupted tasks cannot read, even if source pending metadata still says
   running. Older services without the complete `assignment` fields are denied.
3. Source must be active and officially verified. `SourceRecipeBinding` must map
   it to the same workspace; the workspace ID must match the canonical source hash.
4. Init/build requires a running **authoring** pipeline with a live persisted
   request-token association, the explicit corresponding init/build task ID and
   exact input revision. Pending status must be preparing/queued/waiting_init/
   running, not accepted or already associated with a candidate revision. The
   frozen authoring context must refer to the same source.
5. Repair requires an active/audit pipeline and an actual `RepairRequest` in
   **preparing or submitted**, with all workspace/task/input-revision fields
   explicitly matching. Preparing without `task_id` grants nothing. Auditing,
   accepting, rejecting and finalized repair requests grant nothing. Its base
   Recipe must belong to the source, and the active baseline pointer must still
   agree with the frozen repair context. SQL predicates constrain the scope before
   loading repair contexts; unrelated intents cannot authorize a query.
6. Checks run again on every page. Source authorization and source material reads
   share one source read transaction. Control metadata RPCs are separate reads;
   there is no distributed atomic snapshot across the two ownership domains, nor
   a guarantee against cancellation immediately after the final status check.

The tuple is not a per-attempt secret: this wire has no executor nonce. Owner/
session/status checks reject invalid registered assignments, but do not prove OS
process liveness or distinguish two attempts deliberately reusing the same task
ID/input/session identity. A deployment needing stronger attempt fencing must
supply an independently authenticated host lease mechanism. Do not replace these
checks with caller-token-only or source-only filtering.

## Authorized saved dataset and result meaning

Both list and read use the same SQL scope:

- Join `ResourceScan.bundle_id` to the bundle **and** require scan/bundle resource
  IDs to agree. Join `Resource` for actual source ownership: `ResourceBundle` has
  no `source_id` field. The page version must belong to that same resource.
- Include only current done/review scan bundles. Historical, replaced, foreign or
  disconnected bundles are denied; no global-ID fallback is permitted.
- Use the bound authoring/repair classifier when available, otherwise the latest
  saved assessment. Intent is recruitment/correction/cancellation and page form
  is article/attachment. List-only directory pages are not notices; PDF/office
  primary notices and actual correction/cancellation notices remain queryable.
- Exclude independent unseen holdout material according to the existing effective
  split policy: the latest holdout is hidden unless that bundle was already
  recorded as exposed. Never read or return expectation blobs or reviewer values.

Listing uses SQL offset/limit and one-row lookahead, not loading every bundle or
classification body. Items expose opaque notice/resource/version IDs, saved
material status, business type and the saved classification review flag (null
when unavailable). Reads expose indexed chunks with explicit
notice ID, version and `untrusted: true` on **every** chunk. Reassemble `text` in
`chunk_index` order only when versions agree.

`read_notice` permits only saved page/attachment material, acquisition status,
errors and capture time. It verifies the blob digest, bundle/source/resource/page
version identity, and uses a bounded no-symlink regular-file read. It does not
instantiate `BlobStore`, whose constructor would create a missing storage root.

`read_parse_result` reads the latest **saved diagnostic audit** for that current
bundle whose Recipe belongs to the same source. It preserves independent
`audit_status`, blocking errors, recipe/generation/day and raw saved result status;
a failed audit containing an optimistic result is still explicitly failed. No
accepted Position rows are inferred or rewritten. No saved audit returns explicit
`audit_status: not_available` with `result: null`, not an invented successful empty
parse. Original parsed business/result fields are allowlisted. Semantic answer/
review keys are stripped; semantic-expectation errors retain a generic diagnostic
code without expected values or review references.

These queries intentionally read **current source bundles and their saved
versioned diagnostics**, not the frozen task case catalog. Input revision binds
query authority, not an invented pinned snapshot of every current source result.
Concurrent acquisition can change membership/order between requests. Per-chunk
versions let the caller detect changed material/audits and restart a read; they
do not promise an immutable multi-page source inventory. Use frozen case tools
for digest-bound regression evidence. No read establishes semantic accuracy,
current hiring availability, qualification, full-source acceptance or activation.

## Resource limits and transport behavior

- HTTP request body at most 8 KiB; exact keys only. IDs are nonblank strings up to
  128 characters without surrounding whitespace or ASCII controls.
- Cursor integer `0..2**31-1`, limit integer `1..50`; booleans are not integers.
- Source responses at most **60 KiB** in actual UTF-8 wire bytes, leaving room for
  the client's untrusted envelope under its 64 KiB bound. Flask's default ASCII
  escaping is deliberately not used for these responses. Large reads split into
  2,000-character chunks; the byte budget may shorten a requested page. Nothing
  is silently truncated. Oversized individual results fail explicitly.
- Blob reads and serialized saved results are bounded to **16 MiB**. SQL checks
  audit result/error byte lengths before loading their JSON. Non-regular files,
  symlinks, corrupt hashes, malformed saved data and incompatible schemas fail
  closed with fixed errors, not SQL/provider/filesystem text.
- Source DB connections use SQLite `mode=ro` plus `query_only=ON`; the source
  endpoint performs no schema initialization, service lock creation, migration or
  directory creation. Sidecar DB ownership stays entirely in RecipeService. Its
  existing metadata RPC handlers do not acquire worker assignment locks.
- Client transport uses one trusted endpoint, normal HTTPS certificate verification
  (explicit loopback HTTP also allowed), no redirects or environmental proxies.
  URL userinfo/query/fragment and public plaintext HTTP are rejected.
- Client response reads are streamed and capped at 64 KiB; compressed responses
  are rejected. Per-query deadline defaults to 10 seconds (host maximum 60), not
  an author task lifetime/turn/token cap. No automatic HTTP retries occur.
- Optional internal `transport=` supports offline `httpx.MockTransport`. Each query
  opens/closes its own client and transport; injection must support that lifecycle.
- Normal asyncio cancellation and the SDK cancellation event stop and await
  in-flight reads/watchers. Repeated cancellation does not orphan cleanup. Custom
  Protocol implementations must cooperate and bound their own allocations.

Operators must still protect the internal endpoint, bound connection concurrency/
rate at the ingress as needed, restrict network/DNS and storage permissions, keep
credentials server-only, and redact request headers/material from debug logs.
No public-URL fetching, weak TLS setting or runtime safety bypass is introduced.

## Offline verification

`server/tests/test_runtime_queries.py` uses real temporary source and sidecar
schemas with **separate storage roots**. The source sees only a metadata port;
its production adapter reaches actual authenticated Flask control RPC handlers
through `httpx.MockTransport`. Host-injected metadata doubles also pass central
validation. Source SQL connections remain enforced read-only, and a spy rejects
any source endpoint attempt to open another database. It covers authentication,
every tuple dimension,
stale/finalized/cancelled tasks, actual repair statuses, current source and
holdout isolation, diagnostic failure preservation, gold exclusion, UTF-8 chunk
bounds, missing source storage, safe errors, unchanged source DB snapshots,
control-adapter defaults/failures, missing or forged assignment metadata,
lock-cycle avoidance, per-page revocation and production factory/main wiring.
No provider, network service, OS sandbox or notification is used to exercise
these tests.

`server/tests/test_recipe_runtime_extension.py` separately covers the Protocol,
HTTP transport, SDK tools, optional native registration/restoration, argument and
response bounds, errors, deadlines, cancellation and checkpoint secret exclusion.
The earlier extension-only receipt (October 24, 2026) was 98 passing focused tests;
its then-existing build/case baseline failures were documented during the main
Recipe contract transition, not fixed by changing these query tools.

```sh
UV_PROJECT_ENVIRONMENT=/home/zhang/workspace/xdog-youju/server/.artifacts/recipe-redesign-20261023/venv \
  UV_OFFLINE=true uv run --directory server --frozen pytest \
  tests/test_runtime_queries.py tests/test_recipe_runtime_extension.py \
  tests/test_production.py tests/test_module_boundaries.py -q
UV_PROJECT_ENVIRONMENT=/home/zhang/workspace/xdog-youju/server/.artifacts/recipe-redesign-20261023/venv \
  UV_OFFLINE=true uv run --directory server --frozen ruff check \
  src/youju/runtime_queries.py tests/test_runtime_queries.py
```

Real deployment HTTP authentication, control RPC configuration/reachability,
network/TLS restrictions, ingress rate limits and any stronger per-attempt fencing
remain deployment verification work. No rollout, live model or device verification
is claimed by this scoped implementation.

### Historical endpoint/wiring verification receipt — October 24, 2026

This receipt predates the task-status port replacement; it is not a claim that
the previous direct sidecar SQL authorization remains supported.

Using the existing isolated Python 3.13 / xdog 2.4.9 environment at
`server/.artifacts/recipe-redesign-20261023/venv`, via `uv --offline --no-sync`:

- New `test_runtime_queries.py`: **123 passed**.
- Combined endpoint, existing runtime-extension and production suites:
  **223 passed** (`test_runtime_queries.py`, `test_recipe_runtime_extension.py`,
  `test_production.py`).
- Ruff checks and formatting checks for this task's three owned Python files:
  passed.

These are synthetic temporary-database, Flask/HTTP transport-double and startup
wiring tests, not production HTTP, live model, browser or device validation.
Only `runtime_queries.py`, `recipe_service/production.py`, the new endpoint test
and this document were edited in the endpoint-hardening task. Native prompts,
artifacts and existing extension/tool implementations were not edited.


### Task-status ownership correction receipt — October 25, 2026

This local correction replaces source-side reads of the private sidecar database
with the injected metadata port and existing authenticated control RPC. Only
`server/src/youju/runtime_queries.py`, `server/tests/test_runtime_queries.py` and
this document were edited by this scoped task. The coordinating task supplied the
safe nested `assignment` attestation in RecipeService's existing `status/GetTask`;
that service change is a required dependency, not an endpoint-owned modification.
Older or incomplete attestations fail closed.

Using the specified isolated environment with `UV_OFFLINE=true uv run --directory
server --frozen`:

- Final combined endpoint, runtime-extension, production and module-boundary
  regression suites: **276 passed in 37.30s**.
- Ruff lint and formatting checks for the two owned Python files: passed.
- Actual Flask control RPC over `httpx.MockTransport` verifies authenticated
  caller/source ownership, current input/session/assignment checks and reads while
  a worker holds the assignment lease. No live listener is contacted.
- The source DB remains `mode=ro` / `query_only=ON`; scope, pagination, original
  material bounds and withheld semantic gold protections remain enforced.

These results are offline synthetic database/transport and static checks, not a
production HTTP, real-model, browser or WeChat-device receipt. No live `.venv`,
deployment, download, provider call or model execution was used. Separate control
RPC and source reads do not provide distributed atomicity or stronger per-attempt
lease identities than the existing control contract.
