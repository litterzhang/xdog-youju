# Recruitment Acquisition POC

## What is implemented

The USTC pilot now connects a durable SQLite queue to asynchronous page acquisition,
content-addressed HTML storage, bounded link discovery, and deterministic position
extraction. It does not call a model, send notifications, submit applications, or
run crawls inside Flask HTTP requests. The frontend is not connected to these POC
records; the existing health endpoint is not an inventory of this database.

## Run the loop

From the repository root:

```sh
cd server
uv sync --frozen
uv run flask --app youju init-db
uv run flask --app youju import-sources catalogs/ustc.json
uv run flask --app youju crawl-approve ustc-employment \
  --root-domain ustc.edu.cn \
  --evidence 'Reviewed the user-selected official USTC recruitment portal for this bounded POC.'
uv run flask --app youju crawl-enqueue ustc-employment
uv run flask --app youju crawl-work ustc-employment --limit 6
uv run flask --app youju crawl-positions ustc-employment
```

Review the source before running `crawl-approve`: it is an explicit local operator
action, not a claim supplied by a webpage. Approval activates candidate seeds and
approves their same-origin URLs; it does not override rejected resources or paused
seeds. All commands accept `--database PATH`; `crawl-work` also accepts
`--storage PATH`, defaulting to `.data/blobs` relative to the current directory.
Parent directories for the SQLite file must already exist.

`crawl-enqueue` performs no network access. `crawl-work` runs one bounded async
pass in its own process/terminal and returns. It is not a daemon and does not
install a scheduled task. To continue queued work, run it again. Use
`crawl-enqueue SOURCE --revisit` to requeue completed resources, not review items.
The default pass limit is six resources, with up to two asynchronous tasks.
Same-source adapters share their pacing and access lock; concurrency does not
remove host rate limits. SQLite work and HTML parsing run in short-lived sessions
on worker threads, outside network operations and the event loop.

## Deferred static-to-dynamic escalation

```text
ready/static -> leased/running
  useful HTML                    -> done
  fetch failure or no useful data -> ready/dynamic, available after retry delay
  network-policy denial           -> review (never bypass restrictions)

ready/dynamic -> leased/running
  useful rendered HTML            -> done
  still unsuccessful              -> review
```

The retry delay defaults to 60 seconds. A pass remembers every claimed URL and
never retries it in that same pass, even with `--retry-delay 0`. A later invocation
reads the persisted mode. There is no implicit headed-browser escalation.
Browser retries wait for the USTC profile's content table/list selector, not just
the initial HTML shell. Unsupported layouts may still require parser work.

Jobs use lease tokens and expiry times. A later pass can reclaim an expired lease;
a late result from the old token cannot replace the new owner's state. Cancellation
leaves a recoverable lease. This POC targets one worker process per database, not
a distributed queue; its short SQLite transactions are serialized within that
process. Unexpected parser bugs retain the captured version and are surfaced,
not swallowed as successful extraction.

## Discovery boundary

`OrganizationScope.root_domain` is an explicitly configured registrable domain,
such as `ustc.edu.cn`, not `.cn` or `edu.cn`. Validation uses tldextract's bundled
Public Suffix List with network updates disabled. Update dependencies deliberately
when a newer suffix snapshot is needed.

Discovered HTTP(S) links must have a host equal to the root or end with
`.ROOT_DOMAIN`. Similar-looking hosts and root names embedded in another domain
are rejected. Every candidate is checked after resolving the document base and
normalizing its URL; query parameters are preserved. Discovery is bounded to 500
unique links per page.

Within that boundary, only recognized USTC notice/list URLs on the already-approved
source origin are automatically queued. Other in-domain subdomains are recorded
as unassigned candidates and are not fetched automatically. Attachments are
recorded but not queued by this HTML POC. Existing rejected resources and source
ownership are not overwritten. Browser subresources retain the crawler's stricter
origin/IP policy, rather than inheriting a broad domain wildcard.

## Persistence and evidence

The existing tables are retained unchanged. `init-db` can create these additional
tables without rebuilding the previous schema:

| Table | Purpose |
| --- | --- |
| `organization_scopes` | The publisher's reviewed discovery root. |
| `crawl_jobs` | Durable per-resource state, next mode, availability and lease. |
| `captures` | Fetch-attempt-to-version link, acquisition mode, HTML representation and original response reference. |
| `parse_results` | Version/parser-key result, including unsupported, empty and cancelled outcomes. |
| `positions` | Extracted table rows with original-snapshot evidence and review status. |

`CrawlJob.available_at` is the POC schedule authority; the earlier prototype
`Resource.next_fetch_at` is not used by this worker. There is no second schedule
being maintained. Existing-table changes still require a real migration; this
addition does not make `create_all` a general migration mechanism.

Every completed attempt is recorded, including failures. HTML observations are
stored as immutable UTF-8 snapshots referenced by `ResourceVersion`; available
original response bytes are stored separately. SQLite contains metadata, references
and parsed fields; body bytes live in the local blob directory. Digests and blob
references are verified on reads. An unchanged snapshot/parser key reuses its parse
and positions while a new fetch/capture records freshness and acquisition mode.
Bump `PARSER_KEY` whenever parsing semantics change; otherwise an unchanged
snapshot deliberately keeps its previous parsed result.
A crash or failed transaction may leave unreferenced blobs; automatic garbage
collection is not implemented.

Evidence contains parser-generated CSS paths and whitespace-normalized text from
the exact stored snapshot. No model generates locations. The current-position
query requires a completed, useful, successful capture with matching version and
parse references. Queued, running, failed or review jobs do not silently expose
old positions as current; historical versions and positions remain stored.

## Parsing scope and limitations

The first parser supports the USTC recruitment site's `#oInfoContent` and simple
position tables with an explicit job-title column. It extracts literal titles,
headcounts, education/major text, duties/requirements, and explicit application
deadlines. Arrival dates are not deadlines. Missing or conflicting values remain
unknown, and every position is marked `needs_review`.

Labor-dispatch arrangements, contract-party names and hiring-unit names are copied
only from explicit notice text, with evidence. The source publisher is not assumed
to be the employer. Establishment status remains unknown; this is not an eligibility
matcher or a guarantee that a role is open or has establishment status.

Merged tables, research-demand matrices without explicit position-title columns,
attachments, and cross-announcement conditions/corrections are not fully supported.
Explicit cancellation of a revisited page withdraws its automated current output;
new cancellation/correction URLs still require association and human review.
All unmatched fields and rows must not be invented to increase the position count.

## Focused verification

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Three POC tests cover persistence/evidence/domain filtering and cancellation,
restart-safe deferred escalation/leases, and unknown/conflicting conditions plus
policy denials. They use synthetic data without network calls.

For an opt-in real headless-browser check, with synthetic HTTP fixtures:

```sh
uv run python scripts/verify-poc-browser.py
```

This separately verifies a static shell, a persisted deferred job, actual
JavaScript-generated table content captured through CDP, and a SQLite position.
It needs an installed sandbox-capable Chromium and does not contact an external
site or open a visible desktop window. Browser-dependent checks are not part of
the default tests.

## Recorded public-data POC

On October 11, 2026, the bounded USTC run produced:

- Five static attempts in the first pass and one dynamic attempt in a later pass.
- Six stored captures/versions, 208 in-domain resource records, and 411 discovery edges.
- Two current position rows: one management-assistant role in each of the finance
  office and synchrotron laboratory notices. Each explicitly listed one person,
  labor dispatch and an application deadline of October 31, 2026.
- One complex research-demand page remained in review after the dynamic retry.
- Zero out-of-domain resources; all 20 current field-evidence entries resolved
  back to the stored HTML. There were 160 remaining queued resources, not yet fetched.

Local artifacts are under `server/.artifacts/poc-20261011/`: `poc.sqlite3`, `blobs/`,
`positions.json` and `report.json`. They are not source-code fixtures. This proves
a bounded POC loop, not full-site coverage, the planned 20–30-material evaluation,
frontend integration, deployment, or reliable parsing of every recruitment format.
