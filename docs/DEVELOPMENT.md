# Development Guide

## Scope and structure

This repository is an evidence-based recruitment search project, not an official
recruitment service. The initialized foundation contains:

- `server/src/youju/`: Flask application factory and JSON health route.
- `server/src/youju/models/`: SQLAlchemy source registry and provenance models.
- `server/src/youju/database.py`: explicit SQLite engine and schema setup.
- `server/src/youju/crawlers/`: HTML contracts, shared HTTP access, and static/CDP adapters.
- `server/src/youju/catalog.py` and `cli.py`: candidate source registration.
- `server/src/youju/poc/`: async queue worker, storage, USTC parsing and read queries.
- `server/src/youju/onboarding/`: acquisition-time readers, classification, isolated
  recipe authoring, audit, activation and foreground incremental scheduling.
- `server/src/youju/onboarding/local_classifier.py`: optional CPU-only local model
  with JSON persistence and independently selected confidence thresholds.
- `server/scripts/verify-poc-browser.py`: opt-in real Chromium/synthetic HTTP POC check.
- `server/catalogs/`: version-controlled source definitions with Chinese labels.
- `server/tests/`: local-only pytest tests.
- `client/src/`: shared Taro React pages, SCSS, and platform-adapted HTTP requests.
- `client/tests/`: Node test runner tests for API response and endpoint validation.
- `client/config/`: official Taro webpack configuration.

The bounded acquisition POC can store page versions and extract reviewed-output
position rows; see `POC.md`. Flask does not open a database or start a worker at
startup. The admin frontend can read configured source records; the health endpoint
remains a separate liveness-only contract.
Source onboarding now includes attachment parsing, opt-in model analysis and a
foreground incremental worker. Dedicated zdog-auth login and permission-gated
source administration are implemented; eligibility matching, notifications and an
unattended service remain unimplemented.

## Setup and local development

Use Python 3.13 (supported range: 3.12–3.13), uv, and Node 22–24. Dependencies are
recorded in `server/uv.lock` and `client/package-lock.json`.

```sh
cd server
uv sync --frozen
uv run flask --app 'youju:create_app()' run --host 127.0.0.1 --port 8089
```

To initialize the local source registry (optional for the health endpoint):

```sh
cd server  # From the repository root.
uv run flask --app youju init-db
uv run flask --app youju import-sources catalogs/ustc.json
```

These commands do not fetch URLs or approve sources. Replaying an unchanged
catalog is safe; conflicting changes are rejected. See `DATA_MODEL.md`. Explicit
approval, enqueue, async worker and position-query commands are documented in
`POC.md`; crawling never runs inside a search request.

Browser acquisition uses the Python Playwright dependency with an installed
Chrome/Chromium executable; it does not automatically download browser binaries.
An explicit `executable_path` may be supplied. Linux headed mode requires a
working display. Chromium sandboxing remains enabled; unsafe fallback flags are
not provided. See `CRAWLERS.md` for restricted rendering behavior and verification.

In another terminal:

```sh
cd client
npm ci
npm run dev:h5
```

Open the local H5 development server on port 10089. Its `/api` proxy targets the
Flask service on port 8089. The home page's connection button checks
`GET /api/health`; it does not perform a recruitment search.

For WeChat, copy `client/.env.example` to `client/.env.local`, configure the public
HTTPS API base, and run `npm run dev:weapp`. Import `client/` into WeChat Developer
Tools. The placeholder `touristappid` is not a registered application identity.
Before real-device testing, supply a valid AppID and register the API domain.
Keep URL security checks enabled. No upload or deployment commands are provided.

Production H5 requires a same-origin `/api` reverse proxy or an explicitly
configured public API base with appropriate server-side CORS policy. The local
Flask development server is not a production server.

## Verification

```sh
cd server
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

```sh
cd client
npm run typecheck
npm test
npm run build:h5
npm run build:weapp
```

Build artifacts go to `client/dist/h5/` and `client/dist/weapp/`. Python uses
four-space indentation and Ruff; TypeScript/SCSS use two spaces and strict
TypeScript checking. Name Python tests `test_*.py` and client tests `*.test.ts`.
There is no coverage threshold or Git commit convention established yet.

Tests use synthetic values and no model quota or notifications. Future business
tests must cover evidence locations, announcement association, deadlines,
unknown/conflicting conditions, corrections, cancellation, and false exclusions.
A successful build is not browser or WeChat real-device verification.

## XDOG and security boundaries

The installed official PyPI packages `xdog-ai`, `xdog-agent`, `xdog-flow` and
`xdog-tui` are pinned to 2.4.1 with registry hashes in `server/uv.lock`. Source
onboarding uses their public APIs; no model client or workflow engine is copied.
The health route still needs no model access. Live calls require explicit opt-in
and provider/model configuration. Authoring uses `gpt-6-astra` and the full installed
xdog-flow skill in its system prompt, without an application-level turn/token/run-time
cap. Source steps prepare/enqueue work; a separate `recipe-worker` executes it.
See `RECIPE_SERVICE.md` for status, cancellation, recovery and unbounded-cost implications. The managed
control-plane contracts and disabled-by-default RPC are in `RECIPE_CONTROL_PLANE.md`;
native sessions, skills and auto compaction use the official 2.4.1 Runtime adapter.
Existing source jobs retain a compatibility adapter and are not automatically migrated.

Analyze new or changed material outside search requests. Preserve original versions
and parser-assigned evidence locations. Source acquisition enforces registration,
root-domain and origin admission, public-IP/redirect checks, robots and bounded
attachment parsing. Generated scripts and authoring tools run in isolated Linux
bubblewrap children. No arbitrary URL-fetching HTTP endpoint is exposed.

Never expose credentials through `TARO_APP_*` variables, logs, fixtures, or Git.
Identity and admin grants come from trusted server validation, not client claims.
Future personal-data ownership must use that identity, never body-supplied IDs. Pull requests should explain scope, list actual checks and limitations,
and include screenshots when changing UI.

## Initialization environment limitations

Initial installation was blocked by the execution sandbox network policy.
Network access has since been enabled, dependencies are installed in
`server/.venv`, and server checks now use this project environment. The ORM
dependency and lockfile are managed by uv; no neighboring environment is needed.
The model layer adds three focused SQLite integration tests, without a coverage
threshold or new test framework.

npm regenerated the client lockfile from existing public package resolution.
For offline verification, public third-party `node_modules` were copied into
this workspace, not linked to or changed in the neighboring project. A clean
`npm ci` has not been verified. No deployment, upload, paid model call, or
notification was performed. The workspace's read-only `.git` placeholder does
not provide a usable Git repository or commit history.

### Verification recorded during initialization

- Five Flask tests passed using uv with the existing public Python tool environment.
- Ruff lint and formatting checks passed.
- TypeScript checking and five Node contract tests passed.
- Official Taro H5 and WeChat builds passed; the H5 HTML entry was verified.
- H5 emitted a webpack entrypoint-size warning (282 KiB versus its 244 KiB
  recommendation). This is a warning, not a measured performance result.
- Chromium browser verification was attempted but blocked at startup by the
  restricted environment (`setsockopt: Operation not permitted`). UI rendering,
  interactive service checks, and WeChat real-device behavior remain unverified.

## Source onboarding development

See `ONBOARDING.md` for the five-phase source lifecycle, CLI commands and opt-in
integration check. New general document readers run at acquisition time; source
workflows receive normalized bundles, not file-reading or network tools.

The source-pipeline verification adds a small set of grouped tests for material
formats, conservative classification/fields, xdog recipe admission, and complete
onboarding/incremental behavior. Default tests do not call models or launch
Chromium. `scripts/verify-source-onboarding.py` separately exercises real bwrap,
HTTP policy transport and headless Chromium with synthetic data and official flow
model stubs. Keep its output directory separate from live-source artifacts.

`server/pyproject.toml` pins the four xdog dependencies to official PyPI release
2.2.0. The generated lockfile contains registry URLs and artifact hashes, not local
source paths. `httpx[socks]` supports the user's existing model-provider proxy;
source crawlers retain their own restricted transport. No system dependency
installer or service deployment is invoked by default tests or application startup.

## Cost-aware classification and workflow tracing

See `PAGE_CLASSIFICATION.md` for the rules/local/LLM routing, durable dispatch
budget, training provenance and the distinction between coverage and accuracy.
Use the same `--local-classifier` and model settings throughout a source run.
Default tests never train against a live service or consume model quota.

`WORKFLOW_EXPIRY_DEBUG.md` records real flow/sandbox traces: a generated script
computes expiry, expired pages use the fast parser without a model call, and
unknown expiry stays unknown. `scripts/debug-source-workflow.py` can replay those
branches without modifying the original source recipe or materials.

## First-version admin application

See `ADMIN_V1.md` for dedicated zdog-auth configuration, verified-subject admin grants,
registry/approval/onboarding UI and foreground source/recipe workers. The API never
creates a database or starts a paid Agent at startup. Normal-user public job search
and real WeChat account/device verification remain outside this version.
