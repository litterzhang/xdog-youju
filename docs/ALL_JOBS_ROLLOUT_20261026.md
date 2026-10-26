# Candidate Display Rollout — October 26, 2026

## Production state

The API, source worker and Recipe service now run the locked **xdog 2.4.9**
environment. The old Recipe service was repeatedly failing because 2.4.1 lacked
`xdog.agent.capabilities`; all three services now start successfully. Authoring
is configured for `gpt-6.1-sol`, Flow extraction for `gpt-5.4-nano`. No authoring,
model extraction, new crawl or paid model verification was requested by this rollout.

The public jobs endpoint returns **328 latest candidate rows from 165 notices**.
These are migrated production records, not the separate 55-notice/377-row model
experiment. Full source acquisition and all historical versions are retained.

## Data migration, not re-extraction

A fresh SQLite backup of the configured live `youju-recipe-v4.sqlite3` was copied
to `youju-v3-20261026.sqlite3`. The original database was not replaced. The
migration added two indexed nullable Date projections and missing domain tables.
It preserved all **2952 original v2 position rows**, their original parses,
audits, source artifacts, registrations and acquisition records. It appended
**2952 review-only v3 positions across 1485 migration receipts**. The public
projection selects one latest candidate-bearing audit per resource, not the
entire history. SQL comparisons confirmed every original row is retained,
except the explicitly changed pipeline/incremental control state; integrity and
foreign-key checks passed.

`saved_position_migration.py` is an explicit one-shot operation, not a runtime
legacy parser. It preserves literal business values/provenance and condition
identities/logic. Only unambiguous single atoms use existing source-neutral typed
condition recognition; unresolved atoms remain literal `other` values. Old flat
notice/job dates cannot establish v3 endpoint/year certainty and are **not**
promoted into precise date filters. They remain in the archived original rows.

Migration receipts use `execution.path=migration`, not quick/detailed or a fake
Flow execution. All migrated rows are displayed with migration/review tags.
Existing Recipe revisions and accepted onboarding history were not relabelled as
new-contract acceptance. Future accurate dates and complete typed conditions
require genuine material re-extraction through an upgraded Recipe.

## Monitoring and UI

The legacy Recipe cannot execute under the current contract. Its source is
explicitly blocked with `RECIPE_UPGRADE_REQUIRED`; incremental monitoring is
**disabled**, with its prior state retained in migration metadata. Both worker
services remain healthy and idle for this source. Regenerate and audit the Recipe
before enabling monitoring again; restarting a daemon does not grant admission.
The admin UI exposes the maintenance reason and disables inappropriate re-enable.

H5 was built into an isolated directory, then published to the existing static
root with the previous release retained. SELinux content labels were preserved;
no shared Nginx configuration/reload was needed. WeChat **0.1.13** was uploaded via
the official Taro CLI, including honest migration provenance and maintenance
messages. Upload is not review submission, release or real-device verification.

## Verification and recovery

- Server: **1430 passed, 4 opt-in sandbox tests skipped** in the new live environment.
- Client: **121 tests passed**, TypeScript and isolated builds passed. The existing
  H5 bundle-size warning remains.
- Public list/detail and client DTO parsing were exercised against the live API.
  Anonymous browser verification uses the real published H5/API, not mocked data.
- The Recipe health endpoint is healthy on loopback and remains inaccessible
  through the public ingress. Central authentication/permissions are unchanged.

Private receipts and backups are in
`server/.artifacts/all-jobs-rollout-20261026/`: runtime/Recipe SQLite backups,
authoring workspace copy, previous environment, private configuration, previous
H5 files, migration ID mapping, SQL preservation checks, upload and test logs.

For rollback, stop only the three Youju services before restoring coordinated
configuration, runtime schema/application version and Recipe metadata/workspaces.
Do not point current ORM code directly at the old database: it lacks required
columns. The pre-rollout API was already returning 503; retaining its data is
not a claim that restoring only its configuration restores a working release.
Do not overwrite either original data or the independent verification database.
