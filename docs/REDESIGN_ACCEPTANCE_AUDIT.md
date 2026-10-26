# Recipe Accuracy Redesign Acceptance Audit

Date: October 24, 2026, Asia/Shanghai. **Offline implementation acceptance audit; not a production or accuracy certification.**
This audit separates implemented contracts, verified mechanisms, optional design
paths and missing independent real-data evidence. Passing tests are not accuracy.

| Requirement | Current evidence | Conclusion |
| --- | --- | --- |
| Audited call chain, actual SDK upgrade and skill API | `RECIPE_ACCURACY_REDESIGN.md`, pinned 2.4.9 lock, SDK compatibility tests | Implemented/verified in an isolated environment; not a production upgrade |
| Region-first structure/intent; mixed/negative/unknown cases | `regions.py`, acquisition metadata, whole-chain rule/local/host tests, real saved HTML replay | Generic regions implemented; independent real accuracy remains unproven |
| Discovery independent of business label and approved-scope limits | Existing acquisition/discovery implementation and bounded scope/depth tests | Preserved; classification metrics do not claim link scheduling recall |
| Optional separately versioned source layout authoring | Design section 4.2 explicitly makes this conditional on demonstrated structural need | Still proposed, not implemented or implicitly claimed; do not build a layout platform for every source without reviewed failure cases |
| One period contract, evidence/scope/version checks and explicit clock | `periods.py`, `relative_periods.py`, `amendments.py`, period boundary tests | Implemented for documented independently checkable grammar; unsupported wording abstains |
| Publication/calendar anchors, reviewed extension/cancellation | Host-only temporal ledgers, literal linkage/supersession, stale-dependency fences, full-source synthetic onboarding | Implemented/verified; no real calendar or association invented |
| Coarse vs full quality and real fallback | Fixed official Flow, typed capability, sandbox execution, full/unknown/detailed/no-model tests | Implemented; tier is a processing contract, not semantic truth |
| Zero-job expired/withdrawn admission and reopening | Archived/cancelled onboarding, reassessment, incremental and new full-audit tests | Verified without full-sample invention, retagging coarse rows or publishing failed output |
| Original rejected output, small summaries and bounded detail pages | Task/material/profile-bound evidence, source-to-sidecar copy, privacy/schema-path tests | Implemented within explicit retention bounds; unavailable/truncated data is reported |
| Frozen cases beyond initial three | Existing native lifecycle plus immutable catalog and official AgentTool case access | Implemented; gold/holdouts are not writable or silently exposed |
| Semantic repair and correct removal of an old false job | Host-reviewed labels, independent receipts, actual audit/repair/adoption tests | Implemented on explicit synthetic assertions; model suggestions are not gold |
| Progress-based stopping, cancellation and persistent native history | Native receipt reuse, confirmed-defect/cycle tests, official snapshots/compaction/sandbox tests | Implemented; recent-state storage is not a lifetime turn/token cap |
| Callback/revision/adoption and rollback boundaries | Existing immutable revisions, source regression audit and publication fences | Preserved; a task callback/sample pass cannot publish a source |
| Additive schema migration and native fresh-session cutover | Copied source DB row-hash preservation; real 2.4.1 history imported as opaque data; independent real-bwrap init/build/repair processes under 2.4.9 | Verified on copies, with no snapshot conversion or production restart |
| Rules/local cost, per-source and whole-chain evaluation | Corrected per-source decision path, role-membership/safety/abstention metrics, source/template/content-disjoint synthetic tests | Evaluator behavior verified; 95% cheap coverage/real accuracy not established |
| Real multi-source accuracy qualification (follow-up, not an onboarding/implementation gate) | Frozen real corpus: 40 cases, one USTC source, zero independent gold; earlier assistant predictions and smoke artifacts inspected | **Not measured. No accuracy or model-promotion claim; no per-source annotation requirement** |
| Automatic unlabeled-source admission | `semantic_labels.assess/current`, `test_archived_source_admission.py`: no labels, archived onboarding, incremental change, reopening, new full audit and public full job | No manual label/semantic receipt required when no independent expectation exists; confirmed defects still quarantine |
| Controlled real saved-material replay | Exact immutable hashes, real HTML sandbox and official Flow, explicit clock, 40-case no-network/no-model replay | Verified; one-source unlabeled replay does not close the previous row |
| Deployment, upload or paid experiments | No authorization under this goal | Not performed and not a completion requirement for this offline implementation |

## Required scenario matrix and reproducible evidence

All paths below are under `server/tests/`. Provider/script doubles are explicit;
actual SDK Flow graphs execute in the branch tests. These checks prove behavior
on their inputs, not precision/recall on unlabeled real notices.

| Guide acceptance matrix | Executed evidence |
| --- | --- |
| Lists, recommended detail, mixed pages, misleading words, unreadable/empty pages | `test_region_classification.py`, `test_classification_cost.py`, `test_sourcekit.py`; independent discovery/limits in `test_onboarding.py` and `test_poc.py` |
| Supported cutoff vs interview, rolling/open-ended, multiple positions/rounds, missing material | `test_recipe_accuracy.py`, `test_application_period.py` |
| Day/minute/timezone boundaries; missing publication/counting/calendar | `test_relative_application_periods.py`, `test_recipe_accuracy.py`; Shanghai public-day cases in `test_jobs_api.py` |
| Linked extension, cancellation, partial/conflicting changes, stale event versions | `test_notice_change_associations.py`; full onboarding fixture exercises actual host association handling |
| Honest coarse zero-job path, current full script, unsupported fast -> detailed, disabled model | `test_recipe_accuracy.py::test_actual_flow_coarse_zero_full_script_and_model_fallback`, `test_archived_source_admission.py` |
| HTML/office/PDF rows, merged cells, ALL/ANY, same-title/scope ambiguity | `test_materials.py`, `test_position_facts.py`, `test_semantic_case_ledger.py`, `test_recipe_cases_and_semantics.py` |
| Changed attachments, rule/calendar/association changes, receipt reuse, clock-only evaluation | `test_onboarding.py`, `test_recipe_accuracy.py`, `test_relative_application_periods.py`, `test_notice_change_associations.py`; real-sandbox cache receipt in private `cache-verification/` |
| Same-count wrong field, explained false-job removal, regression/oscillation, unchanged submissions | `test_semantic_case_ledger.py`, `test_recipe_cases_and_semantics.py`, `test_recipe_diagnostics.py` |
| Native restore/compaction, cancelled/persistence-failed tasks, changed files and stale tests | `test_recipe_native_runtime.py`, `test_recipe_runtime.py`, `test_recipe_lifecycle.py`, `test_recipe_cases_and_semantics.py` |
| Immutable candidates/callbacks not publication; source-wide quarantine/adoption | `test_recipe_revision.py`, `test_recipe_callbacks.py`, `test_recipe_binding.py`, `test_auto_repair.py`, `test_onboarding.py` |
| Copied-data migration and old SDK history without restoring old checkpoints | `test_recipe_cutover.py`, actual `sdk-cutover-real-history.json`, `cutover-public-isolation.json`, `temporal-migration.json` |

Reproduce with the isolated environment commands in
`RECIPE_ACCURACY_IMPLEMENTATION.md` and `RECIPE_SDK_CUTOVER.md`. The final automation
receipt is `server/.artifacts/recipe-redesign-20261023/automation-verification.json`;
earlier receipts keep their historical goal statuses and are superseded by the
current audit, not rewritten.

## Automation and optional quality qualification

Ordinary onboarding does **not** require anyone to annotate a source, its notices,
or a second website. The pipeline acquires approved materials, classifies them,
builds/tests a recipe, audits saved notices, admits eligible results and quarantines
individual failures. Incremental repair remains automatic for reproducible,
adapter-attributable defects. Unknown is not expiry or proof of no vacancy.

Semantic labels are optional independent checks for confirmed defects, high-risk
exceptions and quality sampling. With no label, `assess` returns no semantic check
and `current` permits the normal contract/audit path; it never fabricates semantic
truth. With a label, stale or failed checks remain blocking for that resource.
Host review of official-source access, genuine conflicts, ambiguous job matching
and unsupported changes is separate from annotation and is not bypassed.

A reusable global benchmark can later qualify shared rules/models across real
source/template families. Independent provenance and disjoint holdouts are required
**to claim measured accuracy or promote a model on that evidence**, not to onboard
every source or deliver this offline POC. Real multi-source accuracy remains
unmeasured; the 95% inexpensive-path objective is not a proven accuracy result.

The 40-entry `classification-human-review-template.json` under the private artifact
root is an optional **unlabeled** worksheet, not required onboarding input or a
train-ready gold corpus. `classification-blocked-audit.json` records a superseded
acceptance interpretation; it is retained as history, not a current gate.

A separately adopted layout remains conditional on demonstrated structural need.
It is not implemented or claimed delivered; no per-source layout platform is needed
for generic region-based operation.

## Verification scope

Full backend regression, focused model/host and lifecycle tests, real OS sandbox
checks, copy-only data preservation and actual old checkpoint byte preservation
prove specific mechanisms. The cutover verifier uses synthetic provider output with
official native SDK/tools and a saved real sample; it does not prove that an LLM
repaired a real parser or that the full source met semantic expectations. A fresh
native candidate was not accepted and no source/publication pointer was switched.

Crossing midnight during verification exposed UTC-vs-Shanghai-day assumptions in
six test expectations. Those expectations now use the repository's Shanghai audit
calendar; no system clock or publication rule was changed to make tests pass.
Frozen replay clocks intentionally remain October 23 for comparable observations.

The earlier annotation-only completion blocker is withdrawn following the user
correction. Implementation acceptance rests on the functional matrix and its actual
evidence, not on obtaining per-source gold. Do not describe this implementation as
measured 95% accuracy, universal source coverage, a production migration, a WeChat
release or a qualified new model.

## Final offline acceptance result

The implemented mandatory functional matrix above is verified: **515 backend tests
passed, two default opt-in skips**, with **both real-sandbox tests separately
passing**. Ruff, TypeScript, **114 client tests**, and isolated H5/weapp builds pass;
the existing H5 performance warning remains. Fresh real-material replay preserves
all 40 evidence namespaces: 18 contract-full, three explicitly unfinished because
detailed models are disabled, 19 not resolved recruitment articles, 40 unknown
whole-scope expiry. These are not measured semantic accuracy results.

Offline implementation and migration/enablement documentation are delivered.
Production remains on its preserved 2.4.1 environment; activation, deployments,
uploads and paid experiments require separate authorization. The remaining limits
are unmeasured real multi-source accuracy, unsupported temporal/association cases
that safely abstain, and the conditional unimplemented layout-authoring option;
none imposes blanket manual annotation on new sources.
