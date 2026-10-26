# Recipe Accuracy Redesign

**Date:** 2026-10-23. **Status:** audited design for approval, not an implemented
business-logic replacement. Read `RECIPE_ACCURACY_REDESIGN_GUIDE.md` first.

Only the xdog dependency upgrade and its SDK integration fixes are implemented in
this change. Classification, application-period routing, recipe validation and
publication still behave as described in the current-state audit below. No live
source has been replayed, migrated, activated or repaired by this work.

### Current implementation status (October 24, 2026)

The audit below preserves the pre-implementation state; its statements about
unimplemented domain behavior are historical, not the current runtime inventory.
See `RECIPE_ACCURACY_IMPLEMENTATION.md` and `REDESIGN_ACCEPTANCE_AUDIT.md` for the
implemented opt-in contracts, offline verification and migration boundaries.
Normal source onboarding requires no manual annotations. Independent reviewed
cases/global benchmarks strengthen quality measurement; missing per-source labels
are not admission or POC delivery blockers. Optional layout-scoped authoring remains
conditional and unimplemented; no real multi-source accuracy is claimed.

## 1. Scope and decisions

Redesign three connected responsibilities, without adding an Agent engine,
workflow executor, model client or general plugin platform:

1. **Classification:** identify material regions and page roles before deciding
   recruitment intent. Preserve mixed/unknown pages and independent discovery.
2. **Application periods:** source adapters extract supported rules; a shared host
   evaluator computes time validity and decides whether coarse extraction is safe.
3. **Recipe lifecycle:** keep a tested Flow skeleton, give the author paginated
   frozen cases, and accept candidates using path-aware and semantic checks rather
   than mandatory fast completeness or monotonically increasing position counts.

The main safety invariant is asymmetric: uncertainty can cost more extraction,
never justify treating a potentially applicable notice as expired. No natural-
language classifier or evaluator is claimed infallible.

### Implemented SDK upgrade, separate from the proposal

`server/pyproject.toml` and the uv-generated lock pin `xdog-agent`, `xdog-ai[openai]`,
`xdog-flow` and `xdog-tui` to **2.4.9**, checked against PyPI JSON on 2026-10-23.
Their release metadata records publication on 2026-10-23. Only these four package
versions changed in the lock. Python 3.13.12 satisfies the packages' >=3.12 minimum.

Two actual integration changes were necessary:

- The official skill is now **`flow`**, at `xdog.flow/skills/flow/SKILL.md`, not
  `flow-workflows`. The native profile, active skill and package-only tests now use
  that slug. The skill must resolve inside the installed `xdog.flow` package;
  workspace/group skills cannot shadow it. SDK skill activation is retained;
  its body is not manually pasted into a second system prompt.
- The public `submit_result` tool now requires `result_schema` / `result_sink` in
  tool context and explicitly rejects `flow_output_schema` / `flow_result_sink`.
  The adapter uses the new keys. Rejection, progress, redaction and cancellation
  tests are retained rather than bypassing submission validation.

The new SDK's actual `AgentConfig` has no author max-turn option. Existing native
compaction, sequential tools, snapshots and persistence barriers remain. The
Flow runner still uses public `parse_workflow`, `execute`, script resolvers and
node events; the proposal below does not invent new SDK methods.

The upgrade was installed/tested in
`server/.artifacts/recipe-redesign-20261023/venv`, **not** the live `server/.venv`.
Ordinary `uv run`/`uv sync` targets the latter unless an environment is selected;
do not use those default commands as an unapproved deployment shortcut.

## 2. Audited current state

The pre-edit SHA256 manifest, source/test copies, package metadata, baseline checks
and diagnostic probes are in `server/.artifacts/recipe-redesign-20261023/`.
There is no Git repository in this checkout, so no commit identity is invented.
The manifest records the supplied guide and the inspected Python source bytes.

| Area and actual boundary | Finding | Decision |
| --- | --- | --- |
| `materials.py::parse_material`, HTML locators, tables and link inventory | Already saves bounded, parsed material with literal evidence; does not retain a full reusable DOM-region model | Retain acquisition-time parsing and evidence IDs; add bounded structural observations there, not by re-fetching in RecipeService |
| `store.py::discover_document` / acquisition completion | Link discovery precedes business classification and already enforces approved scope, deduplication and limits | Keep this separation; classification must not turn off discovery |
| `classification_features.py::page_shape`, `prose_blocks` | Whole-page link/date counts and a single inferred role can confuse a body with recommendation/list regions | Add region-first signals; allow multiple evidenced roles |
| `classification.py::_rules`, `Classifier.classify`, `shared_classifier.py` | Rules -> local heads -> budgeted small/large inference, followed by host checks, already exist | Reuse; evaluate the whole chain, not only classifier heads |
| `classification.py::_template` | Hashes material formats and initial table rows; not a DOM identity | Keep at most as a diagnostic/selection hint, never a semantic cache key |
| `toolkit.py::check_validity`, `application_period_hint` | Conservative shared checks already preserve many conflicts, rolling rules and per-position uncertainty; precise times/relative rules are limited | Split rule extraction from deterministic evaluation; preserve conservative behavior while extending supported semantics |
| `recipes.py::RecipeRunner.run::validate_validity` | Generated status/date and basis/scope/material metadata must reproduce the old helper; even new period kinds face the old oracle | Replace answer equality with independent layered checks, not removal of validation |
| `RecipeAuthor.validate_candidate` / `sample_errors` | A full/nonempty fast-probe sample pass is mandatory before testing the actual workflow | Validate safety first, then the real selected path; unsupported fast parsing must be a legitimate detailed-extraction route |
| `sampling.py`, `RecipeAuthor._author_input`, `native_runtime.py` | Three-sample ceiling exists in selection, payload preparation, native input and submission, not one constant | Three becomes a bootstrap window; frozen case access and tests must change together |
| `TaskWorkspace`, `lifecycle.py::input_completed` | Flat UTF-8 names, at most 32 files, aggregate 8 MiB; author tools have bounded results | Retain limits; a larger case bank cannot simply be concatenated into `payload.json` |
| `positions/extraction.py::validate_output` | Validates tags/logic structure, notice evidence and recognized row coverage, not general semantic correctness | Retain structural checks; add independent semantic cases and ambiguity reporting |
| `recipes.py` detailed node / `positions/contracts.py::V2_INSTRUCTIONS` | Shared detailed prompt; no separately constrained source-structure notes | Keep the core prompt; allow versioned notes only as bounded task data |
| `repair.py::repair_verdict` | Previous parsed bundles and count floors dominate regression acceptance | Replace sole count gates with matched semantic checks; counts remain diagnostics |
| `audit_feedback`, `select_repair_samples`, `auto_repair.enqueue` | Feedback includes failed/review, but sample selection and automatic dispatch primarily require failed; incidents already deduplicate | Admit attributable semantic/review defects without treating every review as a code bug |
| `auditing.py::current_source_positions`, `source_summary.py::parsing_summary` | Current material classification is selected by newest assessment, not a separately adopted layout key | Before candidate layouts can be replayed in shared storage, isolate them and filter public/statistical readers by the effective adopted key |
| Native sessions, fixed input revisions, RecipeRevision, callbacks, review intents and source bindings | Useful immutable boundaries already exist; accepted candidates are not publication | Retain and extend their metadata; do not create another orchestration service |

### Existing protections, not newly delivered features

Unknown classification already remains reviewable; ordinary recurrence is host
policy. Per-position/extension ambiguity often already yields unknown. Missing
attachments do not universally erase a supported body cutoff. Rejected output is
already retained on `RecipeError.rejected_result`, and audit code avoids some
synthetic-empty false errors. These are starting points, not evidence that all
semantics, scopes or failure paths are solved.

SDK upgrade alone changes none of these domain mechanisms and is not an accuracy
improvement. The synthetic probes are a failure-mechanism baseline, not a labeled
USTC accuracy benchmark or cross-source evaluation.

A read-only SQLite backup and **40 saved real material bundles** were also frozen
from 1,314 current saved bundles. The 2,936,297-byte corpus spans HTML, PDF, DOC,
DOCX, XLS, XLSX and failed/unknown attachments, but **only one source**. Existing
predictions are 31 recruitment, four unrelated, three unknown, one report and one
guide; these are sampling strata, not gold labels. Shared date rules return five
expired and 35 unknown under the frozen clock. The corpus has no independent
source holdout and lacks confirmed extension/cancellation gold. Do not train or
claim accuracy from it without annotation, deduplication and independent cases.

## 3. Target architecture and ownership

```text
approved asynchronous acquisition -> immutable material bundle + dependencies
   |-> bounded link inventory -> scope-controlled discovery (independent)
   `-> region observations + applicable layout profile
        -> role set -> business intent -> classified / unresolved / material failure
              -> recruitment or associated event
                   -> extract application_period rules + evidence/scope
                   -> host validate/normalize/evaluate(explicit clock)
                        |-> coarse_eligible -> model-free coarse extraction
                        `-> otherwise -> supported full script / detailed extraction
                                   -> tier-aware contract + semantic checks
                                   -> audit, quarantine, explicit adoption/publication
```

- Acquisition owns URLs, redirects, versions, attachments and bounded parsing.
- Shared classification/evaluation owns common semantics and scheduling policies.
- Source adapters locate structures and extract facts/rules; they cannot change
  clocks, evidence, acceptance rules, provider choice or permissions.
- RecipeService owns author tasks, fixed cases, test receipts, immutable candidates
  and feedback. Its Agent can change authorized candidate files, not validators,
  gold labels, case membership or production data.
- xdog-agent owns conversation, skill placement, cancellation, compaction and
  resumable snapshots. xdog-flow owns graph execution, ports, branches and trace.
- Detailed inference is structured, tool-free model work through `XdogModels`,
  never the coding Agent and never a downloader or publisher.

## 4. Classification design

### 4.1 Region observations, not another classifier

Extend the acquisition-time HTML observation with bounded region references:
`region_id`, original block/cell/link IDs, relative structural path, repeated-item
observations and region purpose candidates. Preserve the original material and
parent context. Do not replace source evidence with summarized text.

Start with shared HTML patterns: main/article containers, headings followed by
continuous paragraphs, repeated sibling items, dates paired with distinct links,
attachment clusters and navigation/aside/footer landmarks. Use combinations, not
one link-count or keyword threshold. Non-DOM/PDF/plain-text sources may abstain
and retain their original content features. Table structure and merged-cell
observations stay available to the existing parser.

Existing parsed documents do not contain enough CSS/DOM identity for arbitrary
selectors. When that metadata is absent, reparse **saved raw version bytes** inside
the existing host material sandbox under a new parser key; do not re-fetch or give
the author a raw-material downloader. Add region references without silently
renumbering old evidence. If a new parser changes IDs, retain old documents and
create explicit new material/bundle identities; old receipts are not transferable.

A declared region may not exclude a visible registration exception/correction to
make a page certain. Region checks reject selectors with missing anchors,
ambiguous multiple bodies, collapsed evidence coverage, or unexpected shape.
On mismatch, stop applying that layout and run generic inference/review.

Keep `page_roles` and `business_type`; allow roles such as `article` plus
`notice_list` supported by different region references. `template_key` remains a
hint. Add only `regions`, `layout_digest` and a fixed `diagnostic_code` where needed.
Model self-confidence is not an acceptance criterion. Unknown/blocked/readability
issues are diagnostics, not confident unrelated classifications.

### 4.2 Avoid the classification/three-notice bootstrap cycle

**Extraction recipes do not secretly become classifiers.** A source may need a
layout before three recruitment notices can be reliably selected.

Use generic rules/model abstention initially. When saved examples demonstrate a
structural limitation, create a **layout-scoped build** through the existing
RecipeService build lifecycle. Its input includes list, detail, mixed, negative and
failed-region cases, not only pages already called recruitment. Its output is a
small declarative `layout.json`: applicability predicates and region selectors,
not Python, SQL, business labels or arbitrary imports.

Represent this as an immutable revision artifact with `artifact_kind=layout`;
retain `artifact_kind=extraction` for the existing four canonical recipe files.
`SourceRecipeBinding.active_layout_revision_id` is independent of active extraction
and publication generation. Layout adoption requires its own applicable/mismatch
case receipt plus saved-source replay of changed classification decisions and
updates only the layout pointer. Known gold exclusions fail; explained corrections
to old predictions require a basis rather than treating old labels as gold. It
cannot publish positions or
claim successful extraction. Reuse `StartBuild` / `StartRepair`, input revisions,
reviews and callbacks; do not introduce a second task engine. The artifact kind
must be host-fixed in the task, not selected by the author at submission.

Classification receipts are keyed by the effective **adopted** layout/model/rule
identity. Public queries and `source_summary.py` must select that effective key,
not simply the newest `PageAssessment`: current readers use newest assessments,
which would let an unadopted layout experiment affect jobs/totals after this feature
is introduced. Candidate replay must remain isolated until explicit layout adoption.

This optional path follows region-first shared improvements; do not author a
layout for every source without a demonstrated need. Generic-only sources need no
layout revision. A layout can cover multiple same-CMS sources only after each
source's applicability checks pass; shared structure never copies business intent.

### 4.3 Decision order and unresolved handling

1. Validate resource/material readability and detect error/login/captcha/shell pages.
2. Locate regions with the applicable layout and generic observations.
3. Run shared rules against relevant regions and preserved context.
4. Use the reviewed shared two-head classifier when eligible.
5. Escalate only unresolved/conflicting high-impact cases within the existing
   durable source budget. Cache this material-specific result.
6. Retain unresolved cases as a persistent diagnostic/retry backlog. Budget
   exhaustion is not a negative label and not proof of whole-source completeness.

Discovery still follows approved saved links regardless of unrelated/unknown
business intent. Mixed pages can both discover children and enter body analysis;
work deduplicates by material/dependency identity. Correction/cancellation enters
association handling; it must not be extracted as a new ordinary vacancy.

The 95% inexpensive-path objective is **coverage**, with a stated denominator of
eligible material versions. It is not accuracy or a reason to lower abstention.
Measure rule negatives and their false exclusions as well as model predictions.

## 5. Application periods and deterministic routing

### 5.1 Extend the existing contract, do not add a rival validity schema

Keep `validity.application_period` as the rule representation and `validity.status`
as the host's clock result. Keep `current_hiring_status=not_verified`.
The existing three-field period cannot express the following requirements and
needs an explicit versioned extension, not permissive additional JSON properties.

Proposed `application-period-v2` fields:

| Field | Meaning / validator owner |
| --- | --- |
| `kind` | Existing fixed/open-ended/until-filled/not-stated/unknown, plus scoped/relative/conflicting forms |
| `text`, `evidence_ids`, `material_refs` | Literal quote and original document version/block bindings; IDs must belong to pinned material |
| `scope` | Notice, identified position set or recruitment round; targets must bind to material, not invented author IDs |
| `windows` | Explicit start/end clauses, precision, timezone and end-boundary policy for each relevant scope |
| `derivation` | For relative rules only: amount/unit, trusted base reference and reviewed calendar reference when necessary |
| `coverage` | Whether all relevant registration scopes were identified, with support or unresolved scope diagnostics |
| `conflicts` | Conflicting rules and unresolved event/exception references, not a model confidence score |

Keep this at notice level; do not replace `position-facts-v2` or reintroduce
mandatory original quotations for every published position field. Scope/gold
locators used by independent tests are not a new per-field output obligation.
A future implementation must generate and validate concrete JSON Schemas for these
fields before enabling the new recipe contract.

### 5.2 Extraction -> independent validation -> computation

Try reviewed generic rule extraction, then the applicable source adapter. A
selective structured-model resolver can extract unresolved rules/evidence once
when that can change routing. It receives material, not tools or publication
rights. The source cannot specify the model or make its own expiry decision.

The host validates:

- schema, literal references, material/dependency identity and quote support;
- registration purpose, not interview/arrival/certificate/contract dates;
- scope membership, exceptions, multiple rounds and relevant missing materials;
- trusted publication/calendar anchors and deterministic date transformation;
- conflicts against other evidence and the shared baseline extractor.

An evidence ID existing, or a date appearing in it, is insufficient proof of a
registration cutoff. The old `check_validity()` is a baseline/conflict signal,
not a universal oracle. A new adapter can support a new expression only when the
shared schema and independent semantic checker already support that expression;
it cannot extend the trusted evaluator by uploading code. An unexplained
old/new disagreement cannot authorize coarse routing.

### 5.3 Clock and scope rules

- Require an injected, timezone-aware clock. No machine clock or capture-time
  substitution for publication. If source-default timezone is permitted, record
  that reviewed policy; do not let the model guess it.
- Date-level inclusive deadlines close at the following local midnight. Exact
  times use the explicit timezone and stated boundary. For ambiguous inclusive
  minute/second wording, conservatively retain the entire stated precision unit;
  an explicitly exclusive `before` closes at that instant. Include tests for both.
- Relative calendar days need a trusted publication/base reference. Workdays need
  a reviewed calendar and counting convention; otherwise remain unresolved.
- Evaluate every established registration scope. A later/future/open-ended round
  prevents whole-notice expiry. The earliest position cutoff never closes all
  positions. An incomplete scope enumeration prevents coarse routing.
- Only an independently established association lets an extension/correction
  replace a target rule. A later timestamp or similar title alone is insufficient.
  Cancellation remains a separate event/disposition, not ordinary time expiry.
- Missing attachments do not automatically erase a body rule. Ask whether the
  missing material could change the relevant scope/exception; if unresolved,
  retain the known rule but block whole-notice coarse eligibility.

Compute `coarse_eligible` in the host only: every applicable registration scope
supported by the saved evidence has ended, and no known unresolved material,
association or rule conflict invalidates that conclusion. Use an explicit reviewed
coverage policy, not a requirement to prove that no future web amendment can exist.
Record evidence/check freshness separately. Saved-evidence expiry is not a guarantee
that the latest real-world extension has been obtained.

Unknown, open-ended, until-filled, conflicting or unsupported relative periods
stay on the full-quality path. A future deadline means not expired, not verified
open hiring. No partial-scope downgrade in the first implementation.

### 5.4 Extraction cache and clock evaluation are different

Persist a rule receipt keyed by material/attachment dependency digest, associated
event digest, layout/adapter digest and shared extraction/semantic-rule versions.
If a model participates, also record prompt/profile/provider/model configuration
and any actual revision supplied by the provider; do not invent a revision for an
alias. Credentials never enter keys or persisted diagnostics.

Cache normalized supported rules, not a permanent expired boolean. Clock changes
recompute validity without model calls. Material replacement, new associated
changes or relevant rule/adapter changes invalidate extraction. Unknown due to
missing anchors can remain unknown until evidence/rules change while full
extraction proceeds. Budget/provider-blocked resolution gets an explicit retry
policy, not either an infinite per-poll model loop or permanent negative caching.

## 6. A fixed Flow skeleton and two quality targets

### 6.1 Host skeleton, source adapters

Use official script nodes and conditional edges already supported by xdog-flow:

```text
period_adapter -> host_validate_and_evaluate
  coarse_eligible -> coarse_script -> host_finalize_coarse
  otherwise       -> full_script_probe
                        supported + host checks -> host_finalize_full
                        unsupported/insufficient -> detailed -> host_finalize_full
                        model unavailable        -> explicit review/blocked result
```

The diagram is domain design, not a claim of new SDK node APIs. `execute()` and
its real node events must demonstrate each branch. Both source scripts still run
through the OS sandbox with approved module/function references. Keep the canonical
`validity.py`, `fast_parse.py`, `config.json`, `workflow.json`; the v2 validity
adapter extracts rules rather than authoritative status. The host builds/checks
the workflow skeleton instead of asking each author to invent arbitrary graphs.
Changes to its interfaces require a new recipe-contract version and updated
`SCRIPT_PORTS`, signature checks and templates together.

A bounded `structure_notes` config field may describe layout/terminology to detailed
extraction as **untrusted domain data** under the unchanged core system prompt.
It cannot grant tools, select providers/models, replace schemas, change clocks or
relax checks. Hash/version/test the notes with the candidate. Do not add a prompt DSL.

### 6.2 Minimal additional result information

Add host-owned `quality_tier = coarse | full` to the extraction/audit contract.
Keep material completeness independent. A successful path means it met its declared
tier, not automatic semantic accuracy or business publication. A complete-material
coarse result must not be labeled "完整解析" by status-only consumers.

The source script node returns a typed capability envelope:
`capability = supported | unsupported | uncertain`, extraction when present, and
bounded diagnostics. This is parsing ability, not material `partial` and not
quality authorization. Only the host decides the final tier and route.

- **Coarse:** only host-confirmed expired saved scopes; basic contract/evidence/
  no-invention checks, not full row/condition completeness. Zero identifiable jobs
  is allowed. Script/infrastructure crashes are not successful coarse output.
- **Full:** supported script output must pass its declared structural support,
  row coverage and host checks; unsupported output reaches detailed extraction.
  Available-material incompleteness remains explicit. Model-disabled/budget-blocked
  work cannot be relabeled completed full extraction or downgraded to coarse.

Store coarse results for archival/diagnostics. All public position queries,
activation checks and API projections must explicitly require `quality_tier=full`
where full-quality records are promised. Summaries count coarse separately and do
not add its records to verified/full extraction totals. Reopened or changed notices
must run full extraction; old coarse rows never become full by changing a label.

### 6.3 Replace, rather than weaken, fast-probe acceptance

Retain isolated script execution, allowed imports, output schemas and resource
limits. Stop requiring a complete/nonempty fast result for every development case.
Run the real skeleton with fixed material/clock and verify the expected branch:

- expired complex notice: safe honest coarse output may be empty;
- current/unknown supported structure: test full script completeness;
- current/unknown unsupported structure: test real detailed fallback;
- no detailed budget/provider: explicit unfinished/review outcome;
- no proof of expiry: assert that coarse is never visited.

Do not accept `capability=supported` as proof of semantic correctness. Retain known
row coverage and independent semantic tests. Ordinary test/model budgets and
per-tool resource limits remain; do not reintroduce author lifetime turns/tokens/
time caps.

## 7. Frozen cases and diagnosable tests

### 7.1 Three samples become a starting window

Prepare a frozen catalog containing material IDs, bundle/dependency digests,
formats, layout-family hints, existing failures and split labels. Initially show
up to three diverse cases plus this index, not every attachment in the prompt.
Store bodies in RecipeService's existing immutable blob storage, exported from
approved saved source data. The author has no production DB/network access.

Retain the flat manifest/8 MiB prompt-input boundary. Add a catalog reference to
its fixed input revision, with bodies outside the inline prompt manifest. Extend
input validation and snapshot bindings to cover the catalog digest. Do not claim
that changing `select_samples(limit)` alone makes more cases accessible.

Register only the necessary host-owned domain tools through existing `AgentTool`:

| Tool contract (Youju-owned, not an invented SDK method) | Bounds and authority |
| --- | --- |
| `list_cases(cursor, filter)` | Task catalog only; bounded page/count; no arbitrary source/path |
| `read_case(case_id, section, cursor)` | Original pinned parsed material, paginated; no fetching or writable labels |
| `test_recipe(case_ids, cursor)` | Host snapshots actual canonical files and tests that digest only; model budgets/profile remain host-owned |
| `inspect_failure(test_id, cursor)` | Bounded rejected output and stage/path differences from that test; cannot request another task's data |

Reuse safe Bash/filesystem access for files already exposed by that task rather
than duplicate tool surfaces. Gold and hidden case membership are never mounted
writable. Host test snapshots cannot change while an Agent edits `/work`.

### 7.2 Test receipt and issue shape

Persist receipts against `(candidate_digest, input_revision, case_catalog_digest,
checker_versions, frozen_clock, model_profile_digest, case_versions)`.
Changing any binding invalidates reuse. A successful pre-edit test cannot authorize
a later candidate. Repeated identical failed submissions return the same receipt;
they do not buy another expensive extraction without a new reason.

Keep separate outcomes for execution, contract/safety, semantic checks, candidate
acceptance and source publication. A compact issue includes:

```text
case_id + material_version + stage + code
position/field/condition/scope path
actual value + expected value or explicitly unknown expectation
basis: human-confirmed | independent-rule | suspected-model-output
original locator refs + bounded rejected-output ref
candidate/checker/clock bindings
```

A model-suspected error is a diagnostic, not gold. Preserve the original rejected
output separately from published output and from synthetic failure placeholders.
Feedback is paginated; a summary reports omitted detail counts/references rather
than silently dropping errors. Group material, classification, validity, script
capability, semantic, provider, budget and sandbox failures separately.

### 7.3 Independent semantic cases and repair

Development cases are repeatable and author-visible. Regression cases preserve
confirmed bugs and correct negative behavior. Frozen acceptance cases are host-run
and their expected values cannot be changed by the author. Once their details are
exposed for repair, record exposure and treat them as regression/development, not
independent holdout. Replenish independent cases or acknowledge the limited evidence.

Match positions using explicit position codes and material scope/row anchors when
available, then carefully compare employer/unit, fields, tags, conditions and
ALL/ANY structure. Titles alone are not identity. If matching is ambiguous, report
it rather than fabricate correspondence. Missing/unknown expected fields do not
become assertions of absence. Published fields still need no mandatory quotations.

Repair acceptance checks selected defects and healthy semantic cases, not only
position counts. Retain count deltas as diagnostics. Removing an old false job is
allowed when a confirmed case/basis supports the correction; same-count field or
scope degradation can fail. Old output is a comparison baseline, never automatic gold.

Review and semantic issues can enter diagnosis, but require an adapter-attributable
problem and a reproducible case before code repair. Missing material, acquisition
permissions, model service failure and unsupported shared semantics are not source
regex repair targets. Reuse existing incident deduplication and source/base fences.
If attempts do not improve the defect set, oscillate across cases or meet an external
blocker, persist the diagnosis and stop this task without acceptance. This is
progress-based termination, not a restored fixed author-turn/token limit.

## 8. Concrete case walkthroughs

These observations use frozen synthetic material with an explicit clock; they are
not measured production accuracy. `baseline-241-probes.json` and
`unchanged-domain-249-probes.json` record actual results of the current code.

| Case | Current mechanism / target behavior |
| --- | --- |
| Real repeated list | Shared list detection may work; region list retains child links, and each child is independently classified, not automatically a notice |
| Vacancy detail with recommendation links | The semantic `aside` probe stays article. An unmarked recommendation container yields shape=notice_list, but final rules recover article in this probe; this is not an observed final false negative. Target region separation avoids reliance on such recovery |
| Recruitment meeting report | Recruitment words do not establish offers; retain report/unresolved behavior unless an independent vacancy region exists |
| Mixed home / empty list / dynamic shell | Mixed roles are legitimate; empty or unrendered content must not become a confident unrelated decision or stop discovery |
| Extension | Preserve current conflict guard until target association and a supported new period are established; then the evaluator uses the extended window, not the earliest date |
| Per-position A ended, B still applicable | Current unknown is safer than whole expiry; target scoped evaluation keeps the full path because B prevents whole-notice downgrade |
| Exact 14:00 cutoff | Current helper deliberately leaves unsupported exact-time syntax unknown; target validated timezone/boundary support handles it deterministically, not a prompt-only answer change |
| Relative working days without publication/calendar | Remains unknown; neither capture date nor a normal-day approximation supplies the missing facts |
| Fast unsupported, detailed succeeds | The probe reproduces a successful actual Flow with one job while author validation rejects it before testing that workflow; target removes this contradictory gate |
| Same count, wrong field | `repair_verdict` receives only counts and cannot detect the changed field; target semantic case rejects it if independently confirmed |
| Correct removal of a false job | Current count floor rejects a 2 -> 1 correction; target allows an explained correction supported by gold, without automatically trusting a model's proposed removal |

Additional real saved-material anchors (predictions/observations, not gold):

- `case-004`, digest prefix `0ccbe8639162`, a 新闻动态 page predicted as a list:
  inspect its saved link regions before treating children as notices.
- `case-003`, `b7073c8c5783`, a management-school labor-dispatch notice containing
  “提交报名材料，截止日期为 2025 年 10 月 8 日”: the shared baseline marks saved
  evidence expired; new scope/material checks still need independent acceptance.
- `case-017`, `5d620ee29f47`, a carbon-neutrality institute notice with row-specific
  cutoff evidence: the current baseline returns unknown/per_position, which the
  redesign must not turn into whole-notice expiry by choosing the earliest row.
- `case-029`, `26906822f2e8`, a synchrotron support-position notice: the saved rule
  is open-ended and clock expiry remains unknown, not proof of remaining jobs.
- `case-038`, `1e0290aa0951`, an unreadable portal capture: improve acquisition or
  report the blockage; rewriting a position parser does not restore missing input.

The full hashes, URLs, original bytes and baseline observations live in the private
`offline-corpus/manifest.json`. Contact details from public notices are not copied
into this design. Fixed synthetic extension/false-job/fallback probes supplement
this material; they are not falsely presented as independently labeled real cases.

The SDK upgrade must leave these domain observations unchanged. Baseline parity
is useful evidence of upgrade isolation, not evidence that this redesign is implemented.

## 9. Delivery sequence, migration and rollback

| Stage | Concrete changes | Enablement gate |
| --- | --- | --- |
| 0: audit/baseline (this design) | Freeze source/guide/dependency identity; synthetic mechanism probes; preserve real saved material without labeling it gold | No quality/accuracy claim from test counts |
| 1: period and tier contracts | `toolkit.py`, `positions/contracts.py`, `positions/extraction.py`, templates and `recipes.py`; rule validation/evaluator, two tiers, actual fallback tests | Shared expiry/clock/negative-scope cases; no false-expired downgrade in the agreed labeled set |
| 2: region classification | `materials.py`, `classification_features.py`, `classification.py`, `classification_policy.py`, corpus/evaluation; optional independently reviewed layout revisions | Whole-chain positive/negative/unknown evaluation; existing discovery limits unchanged |
| 3: case access and validation | `sampling.py`, `author_workspace.py`, `recipe_bridge.py`, RecipeService lifecycle/input/native runtime; catalog tools and digest-bound test receipts | Cross-process/session/cancel/changed-file tests; author cannot write gold |
| 4: semantic repair/adoption | `repair.py`, `auto_repair.py`, `auditing.py`, `source_summary.py`, recipe/version bindings and publication queries | Target fixes, healthy semantic cases and explicit acceptance/publication fences |
| 5: controlled replay | Same immutable real materials and fixed clock; source/template/content holdouts | Separate real-model authorization and recorded cost; explicit source adoption only |

Keep existing immutable revisions, snapshots, outbox and source baselines. Version
new contract artifacts and add the minimum period/tier/test/catalog/layout metadata
through an explicit migration. Archive old results; absent quality tags do not
silently become full quality. Do not update historical expiry on new rules by
rewriting old audit rows. Re-audit under a new semantic key/publication generation.

For rollback, preserve old recipe, model/artifact versions, old data path/backups
and service environment; switch bindings/config only through host review. A layout
rollback invalidates dependent classification/rule caches. A recipe rollback must
not bypass newer known cancellation/change evidence or republish quarantined jobs.
No automatic activation on callbacks, candidate tests or author prose.

### SDK deployment is a separate migration boundary

Existing native checkpoints bind SDK version, skill digest and profile digest.
`allow_guidance_update` only permits the guide hash to change. Therefore existing
2.4.1/`flow-workflows` sessions will fail the new bindings; a same-SDK synthetic
restore pass does not prove cross-version restoration.

Keep the old runtime serving existing work until an approved drain/export decision.
Either verify an explicit, allowlisted snapshot/profile migration on copies with
native SDK decode/restore and no replayed side effects, or create an explicitly new
workspace/session/input version under 2.4.9 while preserving old history and files
as untrusted read-only context. SDK upgrade must not silently discard history or
relax `NATIVE_CHECKPOINT_MISMATCH`. Current hard-coded managed workspace/profile
lookup may require a new sidecar data path for that cutover; design that command
before deployment rather than editing the production DB by hand.

## 10. Evaluation and unresolved limits

Seed 30–50 reviewed real cases if practical; this is a starting dataset, not a
cap or assurance. Group near duplicates and split by source and layout family as
well as content. Include failed/unknown pages, not only successful notices.
Keep human-confirmed or independently verified gold separate from model suggestions.

Report denominators and abstention separately:

- classification: missed notices, missed list candidate links, news entering
  extraction; whole rules/local/model chain and conditional per-stage results;
- periods: false-expired/coarse downgrades of still-applicable or unresolved scopes,
  rule/scope/association correctness and unknown rate;
- full extraction: position matching, precision/recall where gold supports them,
  field/tag/condition/scope accuracy and unresolved correspondence;
- coarse extraction: no invention, correct eligibility and honest material status,
  not full-quality completeness scores;
- cost: actual calls/usage for classification, period resolution, extraction and
  author repair separately; cache hits and reasoned repeated tests, not an assumed
  fixed savings percentage;
- recovery: accepted files changing after tests, cancelled dispatch/persistence
  failure, cross-process native restore and stale receipt rejection.

Default tests use synthetic data or explicit provider doubles. Real structured
extraction and author runs are separately authorized, with fixed material and
usage receipts. Another model cannot certify accuracy by rating its own output.
No SDK feature removes the need for data, independent semantic expectations and
manual resolution of ambiguous recruitment conditions.

The design is more reliable than "three samples + regex + nondecreasing counts"
because it expands observation without expanding permissions, separates scope
extraction from time computation, tests the actual fallback path, permits honest
coarse incompleteness only after a supported expiry decision, and tests confirmed
semantic differences. It controls cost through reusable rules, material-specific
caching, deterministic clock recomputation and selective unresolved inference—not
by excluding ambiguous pages or asking an Agent about every page.

## 11. Verification receipt and reproducible commands

Run from `server/`, explicitly selecting the non-production environment:

```sh
export UV_PROJECT_ENVIRONMENT="$PWD/.artifacts/recipe-redesign-20261023/venv"
uv sync --frozen --group dev
uv run --frozen ruff check src tests
uv run --frozen pytest -q
YOUJU_TEST_AUTHOR_SANDBOX=1 uv run --frozen pytest -q \
  tests/test_author_workspace.py::test_real_official_tools_persist_with_readonly_inputs_and_killed_children \
  tests/test_recipe_native_runtime.py::test_native_official_tools_and_frozen_mount_in_real_bwrap
```

Recorded results:

- Old 2.4.1 focused baseline, before SDK compatibility edits: **67 passed, one
  opt-in sandbox test skipped**.
- 2.4.9 full server suite after compatibility edits: **451 passed, two skipped**.
- Both skipped real-Linux sandbox tests separately enabled: **two passed**, using
  trusted synthetic tools/provider streams, not a paid model.
- Ruff and frozen-lock checks passed. The lock diff contains only the four xdog
  package version updates. uv also reported the already-locked Soup Sieve 3.0.1
  yank warning concerning Python below 3.11.5; this project requires >=3.12 and the
  verified interpreter is 3.13.12. Unrelated dependency versions were not changed.
- Seven synthetic classification observations, seven validity cases and the
  fallback/count-repair mechanism probes match across the old/new SDKs. A probe
  exposing a design flaw is an observation, not a passing semantic acceptance case.
- The guide and root AGENTS.md hashes are unchanged. All three live service PIDs
  and active states are unchanged; the production `.venv` still reports 2.4.1.

Artifacts: `sdk-api.json`, `pypi-versions.json`, `audit-manifest.json`,
`baseline-241-probes.json`, `unchanged-domain-249-probes.json`, `offline-corpus/`,
`sdk-249-final-tests.log`, `real-sandbox-tests.log` and `verification.json` under
`server/.artifacts/recipe-redesign-20261023/`. Raw material is private local audit
input, not a new public dataset. No provider calls, production migration, service
restart, source activation, client deployment or WeChat upload were performed.
