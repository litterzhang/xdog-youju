# Recipe and Position Refactor Proposal

**Status: Recipe service/domain redesign remains in progress.** The structured
position model is now implemented and used by ORM, Recipe, public API, client and
feedback. Only `position-facts-v2` is accepted; there is no old-contract adapter
or migration guarantee. See `POSITION_MODEL.md`. No deployment or source Agent run
was performed by this change.

## What the original POC modeled

- `models/poc.py::Position` is an immutable **extracted row**, keyed by
  `(parse_id, ordinal)`. It is not a stable vacancy identity. Audit recording
  includes recipe, bundle, evaluation day and generation in the parse key;
  another evaluation can therefore create new position IDs without a new vacancy.
- Position facts are mostly free text. Education has one field; degree has none.
  An unpopulated field does not distinguish not-declared, not-extracted, missing
  material and conflicting evidence. A dated cutoff is not hiring availability.
- Public reads reconstruct admission across position, parse, audit, bundle, scan,
  assessment, recipe and source records (`jobs.py`). Those safety gates are useful,
  but the query layer must currently understand most of the extraction pipeline.
- Recipe execution has distinct managed and legacy paths. `RecipeService` inherits
  `ManagedLifecycle`; its worker reaches private service operations. Native runtime
  imports source-specific authoring/validation code, and the source bridge invokes
  private Pipeline methods and stores its coordination state in summary JSON.
- The recipe service has workspace/input/task records but no first-class immutable
  recipe revision record: candidate/accepted pointers reference tasks and digests.
  The business DB independently represents candidates and active source recipes.
- User issue records, service audit feedback and repair requests are different
  concepts. Their linkage must be explicit, not driven by a shared word “feedback”.

## Proposed responsibilities

Keep the existing three processes and official xdog SDK. Do not introduce a generic
Agent platform, distributed scheduler, second workflow engine or replacement SDK.

### Recipe control and execution

1. **Workspace**: service-owned directory, source/owner scope, immutable Agent
   profile, initialization and native session checkpoint. Preparation is not a
   recipe's production approval. One source keeps its durable authoring context.
2. **InputRevision**: immutable, server-prepared normalized material and samples;
   no downloader or material-reading tool. Keep InputCompleted-driven init.
3. **RecipeTask**: init/build/repair execution, request idempotency, leases,
   progress, cancellation and checkpoint fences. A successful task only proposes
   output; it cannot publish positions or approve itself.
4. **RecipeRevision**: immutable workflow/scripts/config, content digests,
   input/base revision, output-contract version and sample-validation result.
   Successful generation and business admission are separate states.
5. **Source recipe binding**, owned by the main server: candidate selection,
   full-source audit and active revision. Callback receipt never activates it.

Make the source coordinator a small explicit component rather than an adapter
calling Pipeline private methods. Persist build intent and task/revision/audit
references as coordination records, not mutable user-facing summary JSON.
Keep RPC wire compatibility during the first extraction of these boundaries.

The native adapter still owns official skills, dump/restore and auto compaction;
it is recreated/closed per task. Keep gpt-6-astra for authoring and existing source
safety/tool policies. Do not silently reintroduce turn/token/run deadlines.
Isolate legacy compatibility; retire it only after auditing callers and retained
workspaces, never by deleting historical jobs or assuming a directory is prepared.
A changed trusted profile/guide/SDK contract can invalidate an existing native
checkpoint. Model an explicit session-generation reset when necessary, preserving
old history; do not mutate a pinned profile or silently resume incompatible state.

### Recruitment domain

Start with three business concepts, reusing existing bundle/audit provenance:

- **RecruitmentNotice**: an official recruitment event/announcement, source,
  publisher, primary material, explicit publication information and verified
  correction/cancellation associations. A resource or URL is evidence, not always
  sufficient notice identity when the publisher reuses a URL.
- **Position**: a logical position within that notice. Use an explicit official
  position code when available. Never merge by job title alone; multiple 教授 rows
  can represent different units/disciplines. Uncertain correspondence across
  reordered/changed rows remains unresolved, not a manufactured stable identity.
- **PositionVersion**: admitted facts with material, extraction, recipe and audit
  provenance. Preserve old extracted rows as provenance/compatibility records.
  Feedback stays bound to the exact version the reporter saw.

Do not give a new business identity to every reparse or clock-only expiry check.
Keep extraction attempts/audits separate from content versions and temporal
assessment. New business versions need an actual fact/evidence change, while
provenance still records a different recipe or extraction attempt.

Use a small versioned facts contract, not dozens of ad-hoc new nullable columns
or a generic rule engine. Initial priorities:

| Concept | Required distinction |
| --- | --- |
| Education | Independent tagged value, not inferred from a degree without an AI tag. |
| Degree | Independent tagged value; 博士学位 does not establish 博士学历. |
| Other qualifications | Retain complete original wording and explicit AND/OR groups when supported; otherwise unresolved. |
| Organization roles | Publisher, hiring unit and contract employer are not interchangeable. |
| Location | Proven work location, not the source's region tag or publisher address. |
| Headcount | Announced/planned count, never asserted current remaining vacancies. |
| Application rule | Fixed cutoff, open-ended, until-filled, not-stated or unresolved; notice-wide and position-specific scope. |
| Availability | Separately observed hiring status; unknown unless explicitly verified. |
| Data quality | Missing material, extraction uncertainty and conflicts remain visible. |

Each business field has exactly `value` and `tag`: `source`, `ai_inferred` or
`unknown`. No field-level quote, page/row locator, evidence ID, confidence or
inference explanation is mandatory. The source tag is a parser-origin declaration,
not a human verification claim. Missing values remain null/unknown;
no qualification or staffing entitlement is automatically guaranteed.
Material and extraction versions can remain internal notice-level provenance,
without expanding every position field into an evidence record.

### Atomic conditions and field tags (agreed direction)

Every position has a `conditions` array. Each independent condition carries a
version-local `id`, `type`, `operator`, `scope`, `value` and `tag`. Technical IDs
and logical operators are metadata, while each asserted business value is tagged.
No condition requires original quotes or evidence references.

```json
{
  "schema_version": "position-facts-v2",
  "hiring_unit": {"value": "合成大学", "tag": "source"},
  "title": {"value": "科研助理", "tag": "source"},
  "headcount": {"value": 2, "tag": "source"},
  "employment_type": {"value": "personnel_agency", "tag": "ai_inferred"},
  "deadline_on": {"value": null, "tag": "unknown"},
  "conditions": [
    {
      "id": "c1", "type": "degree", "operator": "equals", "scope": "position",
      "value": "博士", "tag": "source"
    },
    {
      "id": "c2", "type": "work_experience", "operator": "equals", "scope": "position",
      "value": "博士后出站", "tag": "source"
    }
  ],
  "condition_logic": {
    "operator": "any",
    "items": [{"condition_id": "c1"}, {"condition_id": "c2"}]
  }
}
```

This is a shortened example; the Python contract requires all declared business
fields, including unknown placeholders. Current fields are hiring unit, employer,
title, announced headcount, headcount description, deadline, application rule,
employment type, work location and complete requirements text. Declared zero headcount is preserved as a fact, not inferred to mean a new
open vacancy. `headcount` is not
remaining vacancies: 若干名 uses a source-tagged description plus unknown numeric
count. Declared 长期有效 is an independently known application rule, not a made-up
fixed date.

`employment_type` means hiring identity/personnel management:

| Value | Meaning |
| --- | --- |
| `public_institution_staff` | 事业编制 |
| `personnel_agency` | 人事代理 |
| `labor_dispatch` | 劳务派遣 |
| `labor_contract` | 劳动合同制 |
| `other` | Another explicitly described hiring category |

There is no separate `staffing_type` field in this prototype. Fixed-term,
indefinite-term and temporary describe contract duration, not employment type;
`fixed_term` and `temporary` are rejected here. Direct hiring alone does not
establish personnel status. A university/public institution is not evidence of
事业编制. Use null/`unknown` when the category cannot be established; any AI-proposed
category must remain `ai_inferred`, not a guaranteed staffing entitlement.

A phrase such as 全日制本科及以上 splits into study-form and education-level
conditions. Major, age, experience, graduation status/year, political status,
licenses and language requirements are separate array entries. Qualitative clauses
can remain `other`; they must not silently disappear. Explicit all/any logic is
separate reference-based data rather than a new rule execution engine. For example,
博士后出站或具有博士学位 is ANY, never two simultaneous requirements. Age reference
information remains unknown when not supplied; notice/position scope and exceptions
must not be lost during effective-condition assembly.

AI may split prose, normalize supported semantics and propose missing values.
Inferences are permitted but tagged `ai_inferred`; they are not promoted to `source`
or silently treated as confirmed qualifications. Unknown values are null. Source
and inferred values must be nonnull. No field-level original-evidence contract is
required by the user; old v1 evidence remains available during compatibility rollout.

Non-expired notices require full extraction of all positions/conditions. Clock-
unknown, open-ended and until-filled notices cannot be classified as expired simply
to avoid parsing. Use scripts/rules first and AI for missing/ambiguous structure,
with explicit retry/review rather than silent omission. An AI-inferred cutoff must
not silently suppress a potentially active notice. Page-classification cost gates
are distinct from necessary position-analysis calls. These are target completeness
policies, not guaranteed parse success. Clearly expired notices retain the cheap
archival path.

The Python contract now drives Recipe schemas, host validation, `Position.facts`
JSON storage and public reads. The client consumes tagged fields and independent
conditions. Source-specific Agent scripts must generate this contract; bootstrap
scripts retain unresolved complex wording, while detailed extraction receives
instructions permitting explicitly tagged AI inference. Actual source completeness
still needs a source audit; no real-model authoring experiment is implied.

## Feedback boundary

Keep reporting and human review non-executing. If repair initiation is added,
make it a separate administrator action producing a **RepairRequest** that pins
reviewed issue IDs, source, base recipe and immutable sample/version references.

```text
User report -> human review -> explicit repair request -> RecipeTask
             -> RecipeRevision -> source audit -> explicit publication
```

Task completion does not resolve a report. A passed audit does not necessarily
prove the reported field was fixed: targeted issue checks and unchanged-evidence
regression guards are required. Failed repairs retain the admitted revision and
issue history. Neither callback delivery nor a resolved UI label is publication.

## Fresh-schema rollout

The user explicitly chose not to preserve old contracts or existing POC data.
Initialize a fresh SQLite path, use fresh source/workspace storage, regenerate
source recipes through the official Agent and run a new full audit. Do not claim
`create_all` upgrades an old table; initialization rejects old position schemas.
The service does not automatically delete databases or historical workspaces.

The active toolkit key now includes the position contract/parser/validator, so old
source recipes cannot be silently treated as newly validated. Native Agent
checkpoints pin trusted instructions; an incompatible workspace must be reset or
replaced explicitly, not resumed with mutated trusted settings.

Stable vacancy identities, RecruitmentNotice/PositionVersion business records,
RecipeRevision coordination and explicit reviewed repair initiation remain proposed
work. Keep task completion, full-source audit and publication separate.
