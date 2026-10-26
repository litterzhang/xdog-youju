# Offline shared page classifier sidecar

This sidecar exports **saved** page features for annotation, splits an annotated
corpus, trains a candidate shared classifier and evaluates an unseen holdout.
It makes no HTTP, model-service, teacher-model, download or Agent calls. Training
uses the installed CPU-only classifier dependencies. There is no deployment,
background job, automatic model activation or production configuration change.

`register_classification_commands(app)` lives in
`server/src/youju/onboarding/classification_cli.py`. The application factory performs registration. Importing the module alone does not register commands or open a database.

## 1. Export existing saved material

Run from `server/` using the existing uv environment:

```sh
uv run --offline flask --app youju classifier-export \
  --database /absolute/path/to/existing.sqlite3 \
  --storage /absolute/path/to/existing-blobs \
  --source source-a --source source-b \
  --limit 500 --output /absolute/path/to/new-annotation.json
```

`--source` is optional and repeatable. The limit defaults to 500 and accepts
1–5,000 saved bundles, ordered by source ID and bundle ID. This is a bounded
annotation sample, **not** a representative population or a source-wide audit.
Multiple historical bundle versions may appear. The exporter does not select
only easy pages, label from existing assessments or generate a teacher corpus.

The database file and blob directory must already exist. SQLite is opened through
a SQLAlchemy `file:` URI with `mode=ro&uri=true`, not the onboarding writable-store
helper. There is no schema initialization, DDL, reset, write transaction, source
lock file or acquisition. Missing tables fail rather than being created. Saved
bundles are loaded with `PipelineStore.load_bundle`; blob references, digest,
regular-file size and strict JSON syntax are checked before decoding. Broken or
oversized material fails the export, rather than silently disappearing. Use a
consistent saved DB/blob snapshot for reproducible annotation; a live changing
inventory is not a frozen evaluation dataset.

Only the requested annotation JSON is published. Its parent directory must exist;
the output must be outside the blob directory. Existing files, including dangling
symlinks, are never replaced. There is intentionally **no overwrite/force option**;
choose a new versioned filename for every export, model and report.

An exported record has this shape (illustrative content only):

```json
{
  "id": "saved-bundle-id",
  "source_id": "registered-source-id",
  "template_family": null,
  "group": "content-group-sha256",
  "text": "links_few tables_one paragraphs_many body_long position_headers_present pagination_absent\n公开招聘公告\n...",
  "page_role": null,
  "business_type": null,
  "label_quality": "unlabeled",
  "bundle_id": "saved-bundle-id",
  "url": "https://official.example/notice"
}
```

The file is a JSON **array** of records. `text` comes exclusively from
`classification_features.feature_text(bundle)` and `group` from
`content_group(bundle)`. IDs, source IDs, bundle IDs and the URL are provenance
metadata, not concatenated classifier features. The existing feature extractor
removes URL/email references and normalizes digits; it is not a comprehensive PII
scrubber. Audit public material before sharing exports. `bundle_id` and `url`
are annotation aids; corpus validation/splitting retains the required corpus
fields rather than these optional extras. Original saved evidence is not edited.

## 2. Annotate before splitting

Exported records deliberately cannot train a model. An operator must review the
original saved page, annotate **both** semantic labels and an audited
`template_family`, and change `label_quality` to the actual provenance:

- `human`: declared human-reviewed labels. The declaration is not proof of review;
  keep the review record and original evidence separately.
- `silver`: machine/rule/teacher-derived labels. Permitted for training, **not**
  calibration or human-gold test evaluation. No paid teacher is invoked here.
- `synthetic`: fabricated test fixtures, accepted only with `--allow-synthetic`.
  They are smoke-test evidence, never gold accuracy or generalization evidence.
- `unlabeled`: export-only, with null labels. Not accepted by split/train/evaluate.

Allowed `page_role` values:
`notice_list`, `article`, `navigation`, `attachment`, `other`, `unknown`.
Allowed `business_type` values:
`recruitment`, `correction`, `cancellation`, `result`, `guide`, `report`,
`unrelated`, `unknown`. Do not guess a missing condition or convert similarity to
eligibility. Unknown labels are explicit uncertainty, not a negative decision.

A template family is the **audited CMS/page-template lineage**, potentially shared
across multiple sources/hosts. Do not use the source ID as its family, assign
arbitrary per-split family names, or claim an automatic markup/content hash proves
independence. `content_group` identifies equivalent page features; it does not
identify CMS lineage. Review syndicated/cross-host copies and repeated historical
captures together. Audited families must be held entirely within one partition.
If this is impossible with available sources, collect or annotate genuinely
independent sources rather than relaxing the leakage checks.

## 3. Split explicitly by source

```sh
uv run --offline flask --app youju classifier-split annotated.json \
  --training-source source-a --training-source source-b \
  --calibration-source source-c --test-source source-d \
  --output-dir offline-split-v1
```

The output directory is created if its immediate parent exists; an existing
directory is allowed only if all three output filenames are absent. The command
writes `training.json`, `calibration.json`, and `test.json`. All three must be
nonempty. Every record source must be explicitly assigned. The corpus API rejects
cross-partition overlaps in **source, template family, group, normalized content
and record ID**, including copied content whose group was changed. There is no
random per-page split. File publication is exclusive per file, not a transaction
across three files: an I/O failure can leave completed files; inspect and use a
new output directory for a retry, without deleting someone else's work.

Input arrays are bounded by the shared corpus limits (5,000 records, 32,000
characters per feature text, 16 MiB corpus/file limit). JSON duplicate properties,
NaN/Infinity and overflowing non-finite numbers are rejected, not silently
resolved. Required field/label/identity validation is delegated to the shared
corpus validator. Export-only null template families must be replaced before
splitting. Extra annotation metadata does not become a feature.

## 4. Fit an offline candidate

```sh
uv run --offline flask --app youju classifier-train \
  offline-split-v1/training.json offline-split-v1/calibration.json \
  --output shared-candidate-v1.json \
  --min-precision 0.98 --negative-precision 0.995 --min-support 5
```

The shared model has separate page-role and business-type heads with conservative
abstention. Unseen/unsupported classes do not gain acceptance just because a
threshold was requested. Threshold selection uses calibration data, not the final
test set. Threshold-selection precision is **not** independently measured accuracy,
and logistic scores are not calibrated probabilities. Stronger negative-decision
requirements and minimum support are safety gates, not quality guarantees.

Silver data can help fit training features but does not establish human accuracy.
Calibration requires declared-human labels (or explicit synthetic smoke opt-in).
Save/load uses bounded, schema-validated JSON, not pickle or executable model code.
The CLI publishes the completed candidate without replacing any existing output.
The artifact is not activated and is not evidence that a source can be admitted.

## 5. Evaluate a frozen, genuinely unseen human holdout

```sh
uv run --offline flask --app youju classifier-evaluate \
  shared-candidate-v1.json offline-split-v1/test.json \
  --output shared-report-v1.json
```

The evaluator checks test IDs, sources, audited template families, groups and
normalized content against **both training and calibration** manifests. Test
records must be nonempty and declared human-reviewed. Silver evaluation is
rejected, even with `--allow-synthetic`. Repeatedly tuning against this test set
turns it into development data: reserve a new independent holdout afterward.

The JSON report preserves the evaluator's per-source and per-label metrics,
confusion matrices, accepted/total accuracy, coverage, abstentions, class support,
recruitment erroneous exclusions and provenance caveats. An `offline_cli` section
also records training/calibration label-quality counts and explicit interpretation
caveats, including synthetic-not-gold and coverage-not-accuracy boundaries. Abstentions map to
`unknown` in confusion matrices; an unknown/unknown cell is not an accepted hit.
Inspect denominators and missing-class support, not just aggregate averages.
Evaluation rates deduplicate connected group/content components; raw capture count
is reported separately. Per-source metrics deduplicate within each source and are
not additive when sources share copies. Deduplication does not prove statistical
independence of reviewed pages.
`label_quality` remains an operator declaration; code cannot certify honest human
annotation or correctly audited CMS families.

For local fixtures only, add `--allow-synthetic` independently to split, train and
evaluate. A report containing synthetic test data is marked `synthetic_test`,
not human gold. A synthetic training run followed by declared-human test labels
still needs the training provenance inspected; a human declaration alone does
not upgrade the training evidence.

## 6. Evaluate the actual cheap decision chain

Model-only accuracy is not combined accuracy: a rule can preempt a correct model,
and host safety checks can reject a model prediction. Replay the same untouched
holdout against its frozen SQLite/blob snapshot:

```sh
uv run --offline flask --app youju classifier-evaluate-pipeline \
  shared-candidate-v1.json offline-split-v1/test.json \
  --database /absolute/path/to/frozen.sqlite3 \
  --storage /absolute/path/to/frozen-blobs \
  --output combined-report-v1.json
```

The command uses read-only saved material, verifies source ownership and unchanged
exported features, and invokes rules plus the shared classifier with **no external
model configured**. Missing-source/material or changed-feature inputs fail rather
than quietly altering the benchmark. It reports methods, abstentions and erroneous
announcement exclusions as well as the ordinary held-out metrics. A genuine
article/attachment announcement classified confidently as a list, unrelated,
guide or report counts as a routing exclusion. Multi-role output uses the first
role for primary-role metrics and still needs separate review. Compare this report
with model-only results; neither is a live-source or LLM-assisted evaluation.

## Host policy and evidence boundaries

- Runtime worker/CLI loading rejects old binary artifacts and synthetic-training
  or empty/synthetic-calibration artifacts. Declared-human calibration is a
  minimum prerequisite, not proof of gold review or generic accuracy. Offline
  smoke evaluation still accepts explicit synthetic opt-in.
- The model predicts page role/business type, **not** `revisit`. Revisit cadence,
  evidence checks, incomplete-material handling, negative-decision guards and
  admission remain host policy. An offline report is not an end-to-end host audit.
- The old USTC binary local model and historical single-source rule/local coverage
  are **not proof of generic cross-site classifier accuracy**. They are not a
  replacement for this shared-model format or unseen-source/template human tests.
- **95% cheap coverage is a cost target only**, not a promised result, precision,
  recall, eligibility guarantee or demonstrated quality level. Measure erroneous
  exclusions as well as accepted coverage; abstention is preferable to unsafe
  exclusion.
- This workflow does not mutate production `.env`, config, models, recipes or
  source databases. It does not deploy, download dependencies, rerun a Recipe
  Agent, send notifications or invoke a live model. Explicit integration and
  separately approved validation/activation are still required.

## Verification

```sh
uv run --offline pytest tests/test_classification_cli.py
uv run --offline ruff check src/youju/onboarding/classification_cli.py tests/test_classification_cli.py
uv run --offline ruff format --check src/youju/onboarding/classification_cli.py tests/test_classification_cli.py
```

These are local synthetic CLI tests and static checks, including actual SQLite
read-only enforcement, unchanged DB/blob bytes, missing-schema rejection and
leakage rejection. They are not real-corpus accuracy measurements, a paid-model
experiment, production activation, browser validation or WeChat device validation.

## Current implementation and limits

The Flask factory registers all five commands. Source CLI and daemon load the
shared format; the runtime classifier reads both heads instead of inheriting a
page role from a binary relevance score. The implementation fingerprint includes
the classifier, feature, policy, model, corpus and numerical primitive code. Changing a profile does not refill
the existing cumulative source LLM budget.

Rules distinguish supporting guides/reports from openings. Form alone is not
vacancy intent. Missing materials, mixed offers and absent original evidence do
not authorize a confident negative. Cadence is host-owned: lists and announcement
resources default daily; other/unknown resources default weekly. Known lists keep
daily monitoring even when intent abstains. This first iteration does not add
history-adaptive intervals or a per-URL administrative stop-scanning UI. Link
inventory/discovery remains independent of these labels. Primary PDF/office
announcements can route to audit without pretending to be HTML articles.

No multi-source human-gold corpus has been built or scored by this implementation.
There is no promised accuracy, 95% coverage result, automatic teacher labeling,
model promotion service, deployment or new source acquisition. The original USTC
budget/history and generated recipes were not reset or edited.

### Recorded local verification — October 15, 2026

Three disjoint targeted groups passed: 23 classifier/CLI/runtime checks, 65
pipeline/worker checks and 130 job/feedback regressions. Ruff lint/format passed.
The actual offline CLI wrote a synthetic shared artifact and report; runtime
loading explicitly rejects that smoke artifact. The combined evaluator was also
exercised through Flask CLI against a fresh synthetic saved-material database.

Twelve USTC saved bundles were exported read-only for annotation. Their labels and
template families remain unset. No new source was fetched or silently labeled.
Receipts are in `server/.artifacts/shared-classifier-20261015/`; the artifact and
report under `smoke/` are synthetic, not gold. No real multi-source accuracy was
measured, no live model was called and no model/service was activated or deployed.
