# Source and Provenance Models

## Scope

The server uses SQLAlchemy 2.0 and SQLite, without Flask-specific ORM wrappers.
Models are usable from CLI commands, workers, or Flask with explicit
`Session` transactions. Local CLI commands initialize SQLite and import a
candidate source catalog. Static and CDP browser adapters
are connected by the bounded POC worker described in `POC.md`. Additional tables
record discovery scope, leased jobs, capture provenance, parse results and
position rows. There is no public CRUD API or automatic schema creation at
application startup.

Field names, IDs and enums use English; names, region labels, descriptions and
original material can contain Chinese. IDs are stable strings; omitted IDs are
generated UUID hex strings at insertion. Region codes must come from a consistently
maintained dictionary; this project does not yet ship an official region dataset.

## Entities

| Model | Responsibility and important fields |
| --- | --- |
| `Region` | Geographic dictionary: `code`, `name`, `parent_code`. |
| `Organization` | Institution identity: `name`, `short_name`, `organization_type`, `parent_id`, `location_region_code`. |
| `Source` | Publication channel: `publisher_organization_id`, `base_url`, `region_tags`, `status`, `authority_evidence`, `verified_at`. |
| `Seed` | Discovery entry: `source_id`, `resource_id`, `kind`, `discovery_rule`, `refresh_interval_seconds`, `status`. |
| `Resource` | URL identity: `original_url`, derived `normalized_url`, optional `source_id`, `kind`, `review_status`, observation and scheduling timestamps. |
| `Discovery` | A link from `parent_version_id` to `target_resource_id`, with `method`, `raw_url`, `link_text`, `discovered_at`. |
| `FetchAttempt` | A completed request: times, `outcome`, HTTP status, optional version, final URL and error code. |
| `ResourceVersion` | A body snapshot: resource, SHA-256, opaque `body_ref`, byte length, media type and capture time. |

`source_regions` is the many-to-many association table, not a separate business
entity. `Organization.location` and `Source.region_tags` have different meanings:
tags support source grouping, not verified job locations or coverage guarantees.
A publisher is not necessarily the hiring institution or employment-contract party.

## Important constraints

- Every connection created by `create_sqlite_engine` enables SQLite foreign keys.
- Organization/region parents must exist and cannot directly reference themselves.
  Multi-hop cycle detection is not implemented; add service validation before
  exposing hierarchy editing to untrusted users.
- Sources and seeds default to `candidate`; resources default to `pending` review.
  An active source requires verification time and nonblank authority evidence.
  These fields record a review; they cannot prove that evidence is authentic.
- A seed references a resource assigned to that same source. Its URL is not copied
  into the seed. Register a new resource for a URL migration; old URLs are immutable.
- URL normalization preserves query parameters, query order and path case. It
  removes fragments and normalizes the scheme/host/default port. Original links
  remain available. Titles are not identity keys. Resource identity currently
  assumes public, unauthenticated URL-based requests, not arbitrary POST bodies.
- Discovered targets may have no source until reviewed. Discovery records are
  syntactic links, not verified correction, cancellation or eligibility relations.
  Repeated links are deduplicated per parent version, target, method and raw URL.
- Distinct bodies are unique by resource and SHA-256. The same bytes at different
  URLs remain separate evidence. Fetch attempts can reuse an existing version.
- Successful downloads require a 2xx status and a version belonging to that resource.
  `not_modified` requires 304 and a known version. Failures have an error code and
  no version. Missing or unusable cached bodies must not be claimed as verified.
- SQLite triggers prohibit snapshot updates and deletes, including direct SQL.
  `body_ref` points to externally stored bytes; storage integrity and hash checking
  remain the responsibility of a future storage/ingestion layer.
- Datetimes must be timezone-aware; they are stored and returned as UTC. Fetch or
  discovery times never substitute for an announcement publication date.

The model layer does not fetch URLs or enforce source allowlists, DNS/IP policy,
redirect validation, attachment limits, or robots rules. URL syntax validation is
not SSRF protection. No status in these tables alone authorizes a network request.
The POC worker maintains its durable `CrawlJob` queue; see `POC.md`.
`discovery_rule` is only an identifier for a future registered parser, not code to
execute or an instruction to a model.

## Initialize and register the USTC pilot

From `server/`:

```sh
uv run flask --app youju init-db
uv run flask --app youju import-sources catalogs/ustc.json
```

Both commands accept `--database /path/to/youju.sqlite3`; by default they use
`youju.sqlite3` in the current directory. The parent directory must already
exist. SQLite files are ignored by Git. No database is opened on normal Flask
startup, and `init-db` is not a migration mechanism for existing tables.

`catalogs/ustc.json` is a UTF-8 source definition containing Chinese labels and
the selected recruitment entry URL. `anhui` is an internal dictionary key, not
an official administrative division code. Importing it does not verify a site,
authorize fetching, populate job data or change the health response.

The catalog contains `regions`, `organizations`, and `sources` arrays. Sources
contain `region_codes` and nested `seeds`; each seed specifies a URL that is
resolved to a `Resource`. Region and organization parents, when supplied, must
already exist or precede their children in the file. New sources/seeds remain
candidates; new resources remain pending review.

Import rules are deliberately conservative:

- Exact replays do not create duplicates or reset review state and crawl history.
- Changed names, URLs, relationships or other catalog-owned fields are reported
  as conflicts, not silently overwritten. General update/migration tooling is
  not implemented yet; do not delete historical records to bypass a conflict.
- Source approval fields (`status`, `verified_at`, `authority_evidence`) and
  resource ownership/review overrides are not accepted from catalog input.
- Duplicate IDs, unknown fields and broken references reject the import. All
  changes from that invocation are rolled back together.
- A URL already assigned to another source, or awaiting source assignment, is
  not automatically adopted. Such cases need explicit review.

Catalog definitions remain version-controlled configuration; the database
records imported identities, review decisions and runtime state. Use
`Session(engine)` with `create_sqlite_engine(path)` for programmatic access.
Passing no path to the engine helper creates an in-memory database.

## Verification

Run `uv run pytest tests/test_models.py` for the three focused SQLite integration
tests: Chinese registry round-tripping, rejected invalid relationships, and
preserved discovery/fetch/version history. `uv run pytest tests/test_catalog.py`
checks repeatable CLI imports and atomic rejection in two focused tests. Run
`uv run pytest` and Ruff for the complete server checks. Tests use temporary databases and synthetic content;
they never crawl the example institution or invoke a model.

## Source onboarding additions

The source pipeline adds tables without rebuilding the registry or POC tables:

| Model/table | Responsibility |
| --- | --- |
| `SourcePipeline` / `source_pipelines` | Five-stage lifecycle, configuration, lease, active/candidate recipe and summary. |
| `ResourceScan` / `resource_scans` | Per-resource acquisition state and latest version/document/bundle references. |
| `MaterialDocument` / `material_documents` | Versioned general parsing result; reader artifacts live in content-addressed blobs. |
| `ResourceBundle` / `resource_bundles` | A frozen page plus attachment dependency manifest and normalized payload. |
| `PageAssessment` / `page_assessments` | Evidence-backed resource roles, business category and classifier provenance. |
| `ScanPolicy` / `scan_policies` | Periodic `none/daily/weekly` decisions and next scan time. |
| `SourceRecipe` / `source_recipes` | Generated workflow/scripts, sample coverage, candidate history and activation state. |
| `AuditRun` / `source_audits` | Onboarding/incremental audit progress and aggregate errors. |
| `AuditResult` / `announcement_audits` | Bundle/recipe/evaluation-day/recovery-generation outcomes and links to existing parse/position rows. |

Acquisition reuses `Resource`, immutable `ResourceVersion`, `FetchAttempt`,
`Discovery`, and HTML `Capture`. Successful evidence-checked extraction reuses
`ParseResult` and `Position`; a distinct query excludes unactivated recipes,
failed/currently changing dependencies and expired results by default. The legacy
POC query is not the source-pipeline query. See `ONBOARDING.md` for commands,
ownership, cache keys, safety boundaries and verification limits.
