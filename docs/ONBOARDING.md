# Source Onboarding and Incremental Collection

## Scope and prerequisites

The real USTC source-wide POC has completed baseline audit, activation and
incremental verification; see `USTC_FULL_POC.md` for final metrics and retained
failures. The subsequent validity/partial-material repair is recorded in
`USTC_VALIDITY_FOLLOWUP.md`. This does not claim complete website coverage or
production readiness.

This pipeline implements bounded source onboarding, not a claim that every URL on
a website is discoverable. A source must first have a verified publisher, approved
origin and registered organization root domain. New same-root origins are recorded
as candidates, not automatically fetched. Robots, public-IP checks, DNS pinning,
redirect checks and rate limits apply to pages and attachments alike.

Use Linux with working user namespaces and `bwrap` for material parsing and generated
scripts. There is no unsafe fallback when isolation is unavailable. Chromium is
needed for dynamic retries; `antiword` is needed for legacy DOC. Default tests use
synthetic fixtures and model stubs; they do not install these system programs or
call paid models.

The server installs the official PyPI distributions `xdog-flow`, `xdog-agent`,
`xdog-ai`, and `xdog-tui`, pinned to the verified published version 2.4.1.
`server/uv.lock` records registry artifacts and hashes; there are no editable xdog
paths or sibling-checkout requirements. The earlier local 2.3.0 development build
was not a published release. No xdog provider, Agent runtime or workflow executor
is copied here. HTTPX's SOCKS extra supports the configured model-provider proxy;
crawler transport continues to ignore ambient proxy credentials and settings.

## Run the stages

From `server/`, use a new SQLite database for the new pipeline when the old POC
still has queued work:

```sh
uv sync --frozen
uv run flask --app youju init-db --database source.sqlite3
uv run flask --app youju import-sources catalogs/ustc.json --database source.sqlite3
uv run flask --app youju crawl-approve ustc-employment \
  --root-domain ustc.edu.cn --evidence 'Describe your actual source review here.' \
  --database source.sqlite3
uv run flask --app youju source-onboard ustc-employment \
  --database source.sqlite3 --storage .data/source-blobs --max-resources 1000
uv run flask --app youju source-step ustc-employment \
  --database source.sqlite3 --storage .data/source-blobs --limit 50
uv run flask --app youju source-status ustc-employment \
  --database source.sqlite3 --storage .data/source-blobs
```

Approval is an operator action; the example evidence must be replaced by real
review evidence. `source-onboard` queues work without fetching or calling models.
`source-step` advances one bounded phase; repeat it while work remains. Keep the
same database and blob directory together. Existing POC queue ownership cannot
be silently transferred: finish that queue or use a new database. Once a source
is onboarded, old `crawl-work`/`crawl-enqueue` commands refuse to operate on it.

For live classification, authoring and detailed extraction, explicitly enable
models and configure the provider and extraction tiers. Authoring defaults to
`gpt-6-astra` without author turn, total-token or whole-run time caps. Existing
extraction/classifier budgets and per-tool safety limits remain. The normal path
uses two terminals; `source-step` no longer runs the author inline.

**Terminal 1 — advance to authoring and enqueue:**

```sh
uv run flask --app youju source-step ustc-employment \
  --database source.sqlite3 --storage .data/source-blobs --enable-models \
  --provider "$YOUJU_PROVIDER" --small-model "$YOUJU_SMALL_MODEL" \
  --large-model "$YOUJU_LARGE_MODEL" --author-model gpt-6-astra
```

At authoring the server first requests a persistent workspace, prepares saved
materials/instructions there and submits a RecipeService job. Read its ID from
`summary.authoring_job.id`. The command returns without waiting for generation.

**Terminal 2 — process that job with the same model settings:**

```sh
uv run flask --app youju recipe-worker --job-id "$JOB_ID" \
  --storage .data/source-blobs --enable-models \
  --provider "$YOUJU_PROVIDER" --small-model "$YOUJU_SMALL_MODEL" \
  --large-model "$YOUJU_LARGE_MODEL" --author-model gpt-6-astra
```

Use `recipe-job-status JOB --storage .data/source-blobs --after 0` for progress.
Once `candidate_ready`, repeat the terminal-1 command to import the candidate,
then repeat bounded full-audit passes until active or blocked. Sample validation
is not activation. The foreground worker has no source database access or
`--database` option; no HTTP service or worker is automatically started. See
`RECIPE_SERVICE.md` for polling, cancellation and explicit crash recovery.

To reuse a calibrated local model, add the following options to every **source**
classification, authoring, audit and incremental command, not `recipe-worker`.
The classifier profile includes the model digest, rule version and LLM settings;
silently changing those settings does not reuse old assessments as if they came
from the same classifier.

```sh
--local-classifier .artifacts/source-classifier/model.json \
--classification-llm-fraction 0.05
```

Train only from explicitly reviewed saved labels with separate train/calibration/test
content groups. `scripts/train-page-classifier.py` can bootstrap from historical
accepted xdog decisions, but those are **silver labels**, not human ground truth.
The JSON model is inert data; no pickle/joblib model is loaded. Missing attachments
can leave page type certain while still making extraction incomplete; audit checks
are independent and cannot be waived by classifier confidence.

Provider credentials remain in xdog's server-side configuration, never in a
workflow, source record or sample. Without opt-in, rules still run and unresolved
classifications remain unknown. Authoring stops rather than silently choosing a
provider. After addressing a failure, use `source-resume SOURCE`; inventory limits
can be explicitly increased with `--max-resources` and `--max-depth`. A running
RecipeService author is never reclaimed by elapsed time: cancel it, or explicitly
recover an abandoned job after its OS-lock owner has exited. Then collect that
terminal outcome with `source-step` before resuming. Resume and the next step
prepare a new job in the same persistent directory; the worker is still separate.
Audit recovery advances a recorded evaluation generation, retaining previous failed results
rather than overwriting them. Successfully extracted unchanged material remains
reusable.

## Five stages and their admission gates

1. **Inventory:** traverse the source base URL, active seeds, discovered navigation,
   pagination and registered sitemap links. Collect page snapshots and their
   attachments asynchronously. General readers run during acquisition, before
   classification. Persist originals, normalized content, evidence and failures.
   Budget exhaustion is `incomplete`, never successful full-site coverage.
2. **Classification:** classify saved bundles by page role, business type and
   template, with `none`, `daily` or `weekly` recurrence. High-precision rules come
   first, then an optional local character-TF-IDF/logistic-regression model, then
   explicitly budgeted small/large LLM escalation for uncertain or invalid answers.
   The default durable LLM dispatch budget is 5% of observed page versions, shared
   across restarts, classifier revisions and both tiers. Review/unknown outcomes never count toward the
   95% rules/local coverage target. See `PAGE_CLASSIFICATION.md`. Decisions cite existing content blocks. URL patterns and
   self-reported confidence are not sufficient. Unknown/truncated material cannot
   become an unrelated page with no future scan.
3. **Authoring:** select at most three announcement bundles to cover different
   templates and material formats. Report uncovered formats/templates. The server
   requests a RecipeService workspace, prepares input and enqueues a job. A separate
   xdog coding-Agent worker writes canonical `workflow.json`, `config.json`,
   `validity.py` and `fast_parse.py` in that persistent directory. The complete
   installed xdog-flow skill and project contract are injected into its **system
   prompt**. Isolated
   official Bash/filesystem tools allow inspecting parsed samples and testing code.
   There is no author turn, candidate-count, total-token or whole-run time limit;
   cancellation and provider/tool failures still apply. The worker reports progress
   and a candidate; the server imports it before full audit. Each host submission
   verdict is persisted before further repairs. Material downloading is not exposed. The fast
   script is tested even when every selected sample is still within its deadline.
4. **Audit:** replay every identified announcement, including unsampled resources.
   Record errors, partial results, suspicious empty extractions, evidence failures,
   formats, templates and observed model use. Unknown optional qualifications are
   not invented. Unsupported rows, incomplete attachments and unresolved
   correction/cancellation associations cannot be disguised as complete results.
5. **Activation:** atomically select a validated recipe after all identified
   announcements have audit outcomes and at least one announcement has successful,
   evidence-validated position extraction. Individual acquisition, classification
   or parsing failures are quarantined; they do not require 100% page success.
   Unfinished inventory/classification/audit work, zero successful extractions,
   and workflow, sandbox or provider/configuration failures still block admission.
   Registry `Source.status=active` grants acquisition permission; it does not mean
   onboarding has passed.

The `audit.admission` summary names the `quarantine-v1` policy, blocking reasons,
successful announcement count, quarantined bundle IDs and source execution errors.
An assessed `unknown` page is different from an unassessed page: the former stays
scheduled for review, while the latter is unfinished classification work. There is
no invented success-percentage threshold. The audit still attempts every identified
announcement within the admitted inventory; authoring samples must still validate.
`position_count` counts positions from accepted audits (including expired history);
`candidate_position_count` also includes quarantined candidates and is not a count
of published positions. Current-position queries keep all existing evidence,
dependency, classification, freshness and validity checks.

The classification denominator and acquisition failures remain visible alongside
announcement audit counts. Auditing identified announcements cannot prove that a
classifier found every announcement. Real-source recall needs separately reviewed
positive and excluded-page samples; no classification accuracy is claimed here.

## Persistent coding-Agent workspace

RecipeService stores job/workspace/progress records in
`<storage>/authoring/service.sqlite3`, separately from the source DB. Each source
keeps its existing `<storage>/authoring/<source-identity-digest>/` directory;
`recipe-workspace-create SOURCE --storage BLOBS` returns its exact paths without
starting work. It contains:

- `workspace/` → `/work`: writable parser/workflow files and local scratch work.
- `input/` → `/input`: read-only current samples, schemas, guide and skill.
- `history/` → `/history`: read-only host verdicts, run outcomes and `status.json`.

The Agent is instructed to read `/history/status.json` first and repair existing
files rather than start over. Official `xdog-agent` executes the conversation;
its official Bash/filesystem tools execute inside a separate bubblewrap child,
not on the credential-bearing host. Tool commands retain CPU, memory, output,
file-size and per-command time limits. These are safety boundaries, not additional
Agent-run budgets. No network, host home, database or writable repository is mounted.

`python -m youju.onboarding.author_check` gives local graph/fast-parser feedback
inside that child. It does not call a model or grant activation. `submit_result`
with `{"result":{"ready":true}}` asks the host to load the four actual files and
independently test all samples. Rejections return actionable errors and remain in
history; the Agent can edit and resubmit without a host turn cap. Cancellation,
blocker reports and failures preserve files/status but are not success. A later
explicit source retry queues a new worker job in the same directory. A prose-only
reply without an accepted submission continues the same Agent; it is not acceptance.
Run history records tool errors and a bounded, explicitly untrusted final Agent note.
Provider/model failures never select an
unrequested fallback model. The generated extraction workflow itself still has no
Bash/filesystem tools, and per-resource quarantine policy remains unchanged.

The author Agent has no application-level turn, total-token, output-token or
whole-run wall-clock quota; usage is still recorded. This can incur unbounded model
cost until acceptance, failure, a blocker report or explicit cancellation. Ordinary
extraction calls, including sample validation, retain their request budgets and
configured limits; source-wide classification budgeting is unchanged. Transport
and tool safety boundaries and the provider's own model limits remain in effect.

Detailed extraction uses reversible short evidence IDs and omits verbose locator
strings from model input, not source text. Every block and table cell is retained;
returned IDs are restored and validated against the untouched original bundle.
The author first tests the fast adapter. A deterministic failure returns feedback
without an unnecessary detailed-model call; passing the probe still requires the
actual generated workflow to pass independently.

SDK tool-call IDs remain unchanged between the Agent and provider. Only the isolated
child receives a fixed-size local hash reference: Responses adapters may combine a
wire call ID with a much longer opaque item ID. This is not a reason to reject an
otherwise valid Bash/filesystem call or weaken the tool's access controls.

## Material and business boundaries

A bundle contains the saved page, parsed attachment documents, original-reference
metadata, evidence blocks and explicit completeness state. HTML keeps its actual
response/DOM representation; binary files are not passed off as rendered HTML.

General readers support HTML tables, textual PDF pages, XLSX/XLS and DOCX tables,
DOC text through antiword, and XML sitemaps. Limits include input bytes, expanded
ZIP data, XML elements, PDF streams/pages, table dimensions, text, links and output.
The transport cap is 16 MiB; the reader cap is 12 MiB. A bundle attachment-content
budget also prevents one page from accumulating unlimited parsed material.
Classifier/auditor jobs hydrate bundles under bounded concurrency rather than
loading the whole site's content into memory for a single model request.

The source workflow consumes these parsed documents. Validity has three states:
`not_expired`, `expired`, `unknown`. Date-only deadlines use the end of the Shanghai
calendar day. Arrival dates, age dates, rolling recruitment, explicit clock times,
conflicts and per-position cutoffs are not silently converted into a whole-notice
expiry. `not_expired` is not a guarantee that registration has started or that the
user qualifies. A missing attachment does not erase a readable page's explicit cutoff:
`scope=available_materials` and `material_issues` retain that limitation. The
`basis` distinguishes rolling/unstated/per-position/unreadable cases from an
explicit deadline. This is date evidence, not proof that unseen amendments do not
exist. An extraction failure retains the already-validated script result.
Expired notices use the fast script; other notices can use detailed
xdog extraction. The host validates literal fields, row association, evidence and
validity independently of generated code.

Raw Chinese employment-arrangement text remains in the audit result. The existing
position table only distinguishes explicit labor dispatch from unknown; unsupported
arrangements must not become a fabricated establishment-status claim.

## Incremental execution

```sh
uv run flask --app youju source-tick ustc-employment \
  --database source.sqlite3 --storage .data/source-blobs
uv run flask --app youju source-watch ustc-employment \
  --database source.sqlite3 --storage .data/source-blobs --poll-seconds 60
uv run flask --app youju source-positions ustc-employment \
  --database source.sqlite3 --storage .data/source-blobs
```

Supply the same explicit model options when new material needs model processing.
`source-watch` is a foreground worker; `--cycles N` bounds its run. It installs no
service, timer or daemon. Ctrl-C leaves recoverable state. The CLI holds a
database-specific process lock; source execution is not a distributed scheduler.
The separate `recipe-worker` does not hold that source lock, but source state remains
stage-gated: incremental execution during authoring/audit repair is not supported.
Business recurrence belongs to `ScanPolicy.next_scan_at`;
`ResourceScan.available_at` only schedules acquisition attempts/retries. The old
`Resource.next_fetch_at` and POC queue are not alternate authorities for this source.

Incremental passes revisit due resources, follow pagination without stopping at the
first known URL, discover and classify new resources, and recheck attachments even
when the parent HTML is unchanged. Content/dependency versions determine bundle
identity. Unchanged extraction is reusable; the source validity script runs again
on a new evaluation day without requiring another expensive detailed model call.
Activation schedules from the last successful check, so already-overdue resources
are eligible immediately rather than waiting a fresh day.

Static acquisition with no useful content persists a dynamic retry for a later
pass. A URL is never attempted twice in one pass. Unsupported business extraction
can also schedule one later dynamic attempt. Policy denials never escalate.
Page-level audit retries are queued after activation when admission succeeds, so
one failed page does not restart onboarding or stop the source's incremental loop.
Schema/evidence failures and per-resource processing limits stay quarantined.
Unexpected execution failures remain conservative source-level blockers; a browser
retry cannot repair a broken workflow, sandbox or provider configuration.
Dynamic acquisition uses a source selector when supplied, otherwise a bounded
network-quiet wait; it does not promise that arbitrary delayed JavaScript is done.
Failed, queued, incomplete, superseded or unaudited material is not exposed as a
current position. Original versions and historical results remain stored.

## Isolation, verification and limits

Generated workflow capabilities are allowlisted before flow validation. Only
reviewed wrappers and the candidate validity/fast-parse references can run; no
inline Python, external tools, CLI backend, subflow or extra skill is admitted.
Both importing and executing candidate modules happen inside bubblewrap, never in
the host flow process. The child has no host home, credentials, DB or network and
gets read-only code/input mounts, a temporary workspace, resource limits and a
wall-clock deadline. Flow hooks alone are not the sandbox. Hardened multi-tenant
resource accounting still requires a separately operated deployment boundary.

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv lock --check
# Opt-in: requires Linux bwrap/user namespaces and installed Chromium.
uv run python scripts/verify-source-onboarding.py --output .artifacts/onboarding-demo
```

The opt-in command refuses to overwrite an existing output directory. It uses a
synthetic website through the real bounded HTTP broker, real isolated material
readers/scripts, real headless Chromium and real xdog-flow execution. Only model
boundaries are stubbed. It verifies onboarding, an attachment-only content change,
a new link, a persisted later-pass dynamic retry, SQLite positions and sandbox
file/network denial. It calls no live source or paid model.

The October 11, 2026 run at `.artifacts/onboarding-20261011-final/` activated three
announcement samples covering HTML/PDF/XLSX/DOCX, then produced three non-expired
current positions after an attachment update and dynamic discovery. The later
pass used one real dynamic acquisition; originals, versions and audit summaries
are available beside the SQLite file. This is synthetic integration evidence, not
real-source model-quality evaluation.

Not completed: OCR/scanned-PDF recovery, general PDF geometry-to-table reconstruction,
reliable legacy DOC table structure, automatic cross-announcement correction linkage,
browser session/login workflows, frontend integration or deployment. Word
complex content, Excel formulas/hidden content and unsupported attachment cases
produce explicit review outcomes. No eligibility, employer or establishment-status
facts are inferred to fill those gaps.

## Published-package and live-source verification updates

The earlier recorded live path used official PyPI 2.2.0 packages; current dependencies
are pinned to 2.4.1. `xdog-ai[openai]` installs the
upstream optional Chat Completions adapter dependency; `httpx[socks]` supports the
configured provider proxy. Published flow 2.2.0 keeps the per-task submission schema
in tool context, so the host also includes that same trusted schema in the model's
system instruction. Provider requests, locally completed submission acknowledgments,
blocked requests and unreported usage are accounted for separately. A prior submitted
result never turns a later stream/budget failure into success.

Links now carry structural `kind` metadata: `page`, `attachment`, `asset` or `form`.
CSS, scripts, images and form actions remain recorded as references, but are not
mistakenly fetched as HTML pages or submitted. This decision comes from their HTML
relationship, not just a filename extension. Previously observed failed attempts
are retained. Current acquisition counts exclude these non-page references and
report their count separately.

Legacy HTTP attachments on the exact source host can be explicitly approved with
`source-onboard --allow-http` or `source-resume --allow-http`. This does not permit
HTTP webpage traversal, other hosts, private addresses or unreviewed redirects.
Page and attachment transports have separate origin policies and share host pacing.
Verified robots rules may be cached for at most five minutes, respecting shorter
cache directives, freshness and redirect checks; expired rules are not used after a
failed refresh. The normal one-second request interval remains unchanged.

```sh
# Replay saved successful observations after a reader update. No new HTTP/model calls.
uv run flask --app youju source-reparse SOURCE --database source.sqlite3 \
  --storage .data/source-blobs
# Explicitly expand a bounded inventory; saved link inventories are replayed first.
uv run flask --app youju source-resume SOURCE --database source.sqlite3 \
  --storage .data/source-blobs --max-resources 2048 --max-depth 100
```

Reader version `materials-v2` preserves original DOM evidence while distinguishing
layout wrappers, spacing rows and structural links. Offline reparsing creates new
reader artifacts and bundles, not new successful fetch timestamps. A document/table
reader limit remains an explicit material/audit error; only an unfinished link
frontier or discovery budget prevents moving on to classification. Material failures
remain quarantined under the admission policy rather than blocking all good pages.

Confident complete samples and readable partial samples with known missing
attachments can be selected for authoring; missing formats/templates remain
in the coverage report. A general appointment guide without position-table rows is
supporting recruitment material, not automatically a standalone vacancy announcement.
Model-only evidence IDs can be compacted reversibly to reduce repeated hash text;
persisted references still point to the original document blocks.

For an explicitly authorized, budgeted live model run, use the existing approved
source database and inspect the report before claiming success:

```sh
uv run python scripts/verify-live-source.py SOURCE --database source.sqlite3 \
  --storage .data/source-blobs --output .artifacts/live-check --enable-models \
  --provider "$YOUJU_PROVIDER" --small-model "$YOUJU_SMALL_MODEL" \
  --large-model "$YOUJU_LARGE_MODEL" --author-model "$YOUJU_AUTHOR_MODEL" \
  --max-model-calls 1000
```

This command is not a default test. It records redacted accounting and phase outcomes,
uses the existing pipeline rather than another executor, stops on a blocked stage or
budget, and only checks incremental execution after actual activation. A completed
crawl or model response alone is not proof that the full source passed onboarding.
At queued/running authoring it reports `awaiting_recipe_worker` and returns instead
of launching an inline Agent. Run the separate sidecar, then rerun the driver with
a fresh output directory to collect the candidate and continue auditing.

The real USTC attempt with published packages is documented in
`USTC_LIVE_VERIFICATION.md`. That earlier attempt did not pass the full loop: collection uncovered
material/authority blockers, classification is incomplete, and two real authoring
preflight candidates were rejected. Do not interpret the earlier synthetic success
as real-source activation.

The later `ustc-author-agent-20261011-fixed-ai` replay successfully validated a
source recipe using three saved real USTC announcements. It used five author turns,
three successful detailed extraction calls and stored 20 evidence-backed position
records in a verification SQLite snapshot. All three validity results are unknown,
not a claim that applications remain open. At that sample checkpoint the source-wide baseline remained in classification;
the sample replay did not activate it or verify real incremental execution.
The subsequent real source-wide audit, activation and incremental verification
are recorded separately in `USTC_FULL_POC.md`.

Use the opt-in replay driver to debug authoring without resetting source history:

```sh
uv run python scripts/verify-source-authoring.py SOURCE \
  --database source.sqlite3 --storage .data/source-blobs --output .artifacts/author-check \
  --sample-url "$SAVED_NOTICE_1" --sample-url "$SAVED_NOTICE_2" \
  --provider "$YOUJU_PROVIDER" --small-model "$YOUJU_SMALL_MODEL" \
  --large-model "$YOUJU_LARGE_MODEL" --author-model gpt-6-astra --enable-models
```

This opt-in diagnostic replay is not the normal RecipeService submission path and
does not verify the new two-process boundary. Historical author-turn counts in
this document and linked POC reports describe experiments, not a current run limit.
Choose one to three complete saved notice URLs. The output directory must be new.
The verification database reuses the original blob directory; keep the report's
`storage` path available when inspecting evidence. It is not a standalone database
export, and the original source pipeline state is not changed.

## Reanalyze saved material without recrawling

```sh
uv run flask --app youju source-reanalyze SOURCE --database source.sqlite3 \
  --storage .data/source-blobs
```

This approved-source command schedules classification, fresh recipe authoring and
full audit from existing bundles, without retrying acquisition failures or changing
fetch/version timestamps. It refuses pending acquisition work or operation leases,
retains prior generations and source budgets, and does not approve new origins.
Continue with `source-step` using the same configured models/local classifier,
then use the separate `recipe-worker` when an authoring job is queued.

Readable incomplete bundles may now use detailed extraction; missing material is
still reported and outputs remain partial/review. Such candidate positions are
stored in audit JSON, not silently admitted by current-position queries. A partial
prose authoring sample must produce evidence-valid rows with only known acquisition
warnings; complete samples still need full success. Parent rerendering is skipped
for policy-only missing attachments when valid candidate rows already exist.

## Separate material warnings from content review

See `AUDIT_STATUS.md`. Audit status `partial` means nonempty, evidence-validated
available material with known acquisition warnings; it is not a human-review
queue entry. `review` and `failed` retain actual content/classification or repair
problems. Summaries expose extraction/material/validity independently and count
warnings apart from blocking errors. Raw workflow-schema error arrays remain for
provenance; do not treat every entry as a blocking problem.

`source-reassess` reapplies this policy to saved current-day outputs without models
or acquisition, preserving old generations. `source-positions --include-partial`
explicitly includes available-material rows with warnings; the default stays
complete-material-only. Add `--include-expired` only for intentional history
inspection. All current-version, pending-dependency and evidence checks still apply.

## Standard audit-to-Agent repair

`source-repair SOURCE --database DB --storage BLOBS` schedules one audit-driven
source repair without downloading or editing the generated source files. The
normal model-enabled `source-step` prepares saved failures in the requested
workspace and queues a RecipeService job. An explicit `recipe-worker` runs the
official coding Agent; a later `source-step` imports its candidate and reruns the
full audit. There is no inline author or automatic sidecar startup.
Unchanged submissions cannot masquerade as repairs. Previously parsed bundles and
position counts must not regress before activation. Audit feedback is kept under
`history/audits/` and linked by `latest_audit`; blocked runs reuse the same history.
See `SOURCE_REPAIR.md` for the verified USTC run and remaining limitations.

## Managed lifecycle transition

The source onboarding commands retain the explicit `legacy_build` compatibility
adapter on official xdog 2.4.1. They retain the original sample/full-audit admission checks and do not
pretend to support native session restore. The new InputCompleted/init/build/repair
control plane now has a native runtime; automatic source-server adoption remains
an explicit follow-up migration, not an implicit rewrite of old jobs; see `RECIPE_CONTROL_PLANE.md`. Do not create a managed workspace in an
existing source's legacy storage root expecting automatic conversion.
