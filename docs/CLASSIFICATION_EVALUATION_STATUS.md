# Classification Evaluation Evidence and Qualification Limits

Date: October 24, 2026. No model promotion or accuracy guarantee.

## Whole-chain measurements

The existing offline `classifier-evaluate-pipeline` command evaluates saved material
through shared rules, the shared local model and host safety gates, with no external
LLM. Its per-source metrics now use the **same combined decision path** as global
metrics. Previously the global path was combined but per-source predictions were
model-only, which could misrepresent a source's actual rules/host behavior.

Reports now include:

- whole-chain and per-source cheap **resolved** coverage, with denominators;
- review/unknown counts separate from confident exclusions;
- confirmed recruitment notices recognized/deferred/confidently excluded;
- list-role misses/deferment and expected-role membership (not only the first role);
- nonvacancy pages entering extraction candidates;
- model-only comparison, source/template/content/group leakage checks and declared
  label provenance, without treating scores as calibrated probabilities.

Unknown is neither a coverage success nor a negative classification. 95% cheap-path
coverage is a cost target, not accuracy. Compound role membership is reported
separately from primary-role metrics. Saved-link inventory and acquisition/scope
safety have their own tests; role metrics are not a claim of link scheduling recall.

## What is actually available

Existing saved real material in this repository is **USTC only**, including the
frozen 40-case redesign corpus and the older USTC unlabeled export. Historical
assistant annotations/predictions are not independent gold. The cross-source
training/evaluation artifacts currently available are explicitly synthetic smoke
fixtures. Their numerical scores do not prove real-source generalization.

New tests use genuinely disjoint synthetic source/template/feature records and
show that rules can recognize notices the model-only path does not, while empty
portals stay abstained. They verify evaluator behavior, not real classification
accuracy. No silver/model suggestion is promoted to human gold.

## Optional global benchmark; no per-source annotation gate

Normal source onboarding uses automatic classification and audits, without manual
labels. Shared rules/local models and selective budgeted inference are reused;
unknown resources remain explicit rather than being confidently excluded.

To qualify a future shared-model/rule release with measured real generalization,
use independently reviewed annotations from multiple real source families, including
true lists, details, mixed pages,
news with recruitment words, results, guides and unreadable/unknown pages. A
30–50-case starting set is useful but not an accuracy assurance. Preserve source,
template and near-duplicate group identity, material hashes and reviewer provenance;
reserve source/template/content-disjoint test material from training/calibration.
Use the existing export/split/train/evaluate commands and compare both model-only
and whole-chain reports. Merely setting `label_quality=human` is a declaration,
not proof someone checked it.

This is an unmeasured quality limitation, not a prerequisite for completing the
offline implementation or onboarding each source. No claim of 95% accuracy or
independent multi-source quality is made. Manual review is reserved for unresolved
exceptions, confirmed defects and optional sampling; model suggestions are not gold.
The optional layout-scoped authoring path
from the redesign remains proposed: generic region improvements are implemented,
but no independently reviewed saved case currently demonstrates a need that would
justify building a layout platform for every source. Any future layout adoption
must retain its separate version/replay/publication fences.
