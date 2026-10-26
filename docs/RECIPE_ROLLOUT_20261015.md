# Recipe Refactor Migration and Rollout — October 15, 2026

## Approved scope and migration policy

The operator explicitly authorized migration, deployment and WeChat upload. Production
still had flat position records, source-pipeline recipe pointers, a schema-v3 sidecar
and a source-specific local classifier. These artifacts cannot be labelled as valid
position-facts-v2 outputs or immutable reviewed RecipeRevisions by copying metadata.

`server/scripts/migrate-recipe-contract.py` now creates a new database exclusively,
reading the original in read-only mode. It preserves registered sources, organizations,
regions, seeds, discovery, fetch history, captures, resource versions, documents, bundles,
assessments and acquisition slots. Completed downloads are not requeued. It refuses
leased/running acquisition and validates integrity and all foreign keys before publishing
its output. The original database is never changed.

Incompatible positions, recipes, parse/audit results and feedback remain in the archived
original database. They are not exposed through the new current-jobs contract. Sources
require explicit reclassification, Agent generation and a full audit. Incremental monitoring
is disabled until the administrator reviews the rebuilt source and enables it again;
the previous preference is retained in migration metadata.

## Cutover

The three project services were stopped at **14:55:16 Asia/Shanghai** for consistent
backups and restarted at **14:58:17**. No adjacent service or shared Nginx configuration
was changed. The configured source database is now `server/youju-recipe-v4.sqlite3`;
`server/youju.sqlite3` remains intact as the pre-refactor database.

The migration preserved:

- 1 source, organization, region and seed; the official-source approval and region link.
- 5,107 resource records, including unscheduled discovery candidates.
- 1,313 completed acquisition slots and 1,345 versions/documents/bundles/assessments.
- 68,543 discovery edges and 1,532 fetch attempts/captures.

**655 historical positions** are retained in the old database but not published by the
new API. Public `/api/jobs` correctly returns zero until new recipes are generated/audited.
USTC is blocked in classification with `RECIPE_REGENERATION_REQUIRED`; administrator
recovery projection permits explicit saved-material resume. Nothing was automatically
marked onboarded. No paid model task was submitted during migration/deployment.

Old authoring files/history were backed up and retained under
`server/.data/source-blobs/authoring.pre-recipe-v4-20261015/`. A new sidecar schema-v4 DB
and USTC workspace were registered through authenticated RPC, with `waiting_input`,
no accepted revision and no init/build/repair tasks. Old checkpoints were not fabricated
as new-session history. A new callback inbox avoids mixing incompatible notifications.

The old local classifier was archived and its runtime setting cleared. No synthetic
shared classifier was deployed. Rules and bounded LLM fallback remain available after
explicit resume; shared-model quality/95% cheap coverage is not asserted for this rollout.

## API, H5 and upload verification

All three systemd services are active and both daemons passed their actual service-context
sandbox startup probe. Loopback/public API health, auth status and current-job reads return
200; anonymous admin returns 401, invalid job filters 400, and internal Recipe routes remain
publicly 404. Source DB integrity and foreign keys pass. Health's `not_initialized` text is
the existing constant liveness label, not a database inspection result.

The official staged H5 build was published into the existing `client/dist/h5` root and
project-scoped SELinux labels restored. Verified local TLS-vhost and public
`youjuh5-h.942295.xyz` return the exact deployed index. The non-`-h` public gateway still
serves a different page; its external routing was not changed. Dotfiles, H5 `/api` and
missing JavaScript return 404. Nginx syntax validation passes; no reload was needed.

Official Taro CI uploaded **WeChat 0.1.8** at **15:02:03 Asia/Shanghai**, size
**459.4365234375 KB**, AppID `wxc01f9aa6db230b0c`. Description: `Structured positions,
immutable recipe revisions and unified repair`. Bundle inspection confirms native tabs,
HTTPS API origin, position-facts-v2, centralized permission gating and migration recovery
copy. URL checks remain enabled and the private upload key is outside the bundle.
No review or production release was submitted. Experience-version selection and actual
WeChat-device behavior remain unverified.

## Evidence and limitations

Private consistent DB/env/workspace backups, migration counts, HTTP checks, workspace
registration and upload receipt/QR are under
`server/.artifacts/recipe-rollout-20261015/`. Build logs/staged client outputs are under
`client/.artifacts/recipe-rollout-20261015/`. Backups containing credentials have private
permissions and are never served or included in client output.

The refactor's preceding full offline suite passed 445 tests with two explicit real-sandbox
skips. Migration adds two focused synthetic tests; client typecheck and 67 tests pass.
H5/upload compilation passed with the existing H5 entrypoint-size warning. Deployment
verification is actual HTTP/service/upload verification, not a real new USTC Agent loop.
The administrator must resume saved-material onboarding and inspect its audit before
expecting current jobs or enabling monitoring. Human parsing reports still do not run Agents.
