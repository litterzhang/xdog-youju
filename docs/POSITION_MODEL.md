# Structured Position Model

## Scope and status

The server and client now use **one** position contract, `position-facts-v2`.
Recipe extraction, audit persistence, public reads, filters and issue reports use
this shape. Older flat Recipe/HTTP output is rejected rather than translated.
This is still an extracted position within a parse, not a stable cross-version
vacancy identity. Recruitment-event association and eligibility matching are not
implemented by this change.

## Fields and provenance

`models.poc.Position` stores `parse_id`, `ordinal`, validated JSON `facts`, optional
source locators and review status. Every business field is required, with exactly
`value` and `tag`:

- `source`: extracted or normalized from original material; not a human audit claim.
- `ai_inferred`: inferred by AI, never silently promoted to original facts.
- `unknown`: null value, not “unrestricted”.

Fields: `hiring_unit`, `employer`, `title`, `headcount`, `headcount_description`,
`deadline_on`, `application_rule`, `employment_type`, `work_location`,
`requirements_text`. Publisher, hiring unit and contract employer are distinct.
Counts are announced counts, not current vacancies; an explicit zero is retained.
若干名 belongs in the description with an unknown numeric count.

Employment categories are `public_institution_staff` (事业编制), `personnel_agency`
(人事代理), `labor_dispatch` (劳务派遣), `labor_contract` (劳动合同制) and `other`.
Contract duration and an institution's ownership do not establish personnel status.
There are no mandatory field-level quotations, evidence IDs or confidence scores.

## Independent requirements

`conditions` is an array of `{id, type, operator, scope, value, tag}`. Types:
education, degree, study_form, major, age, work_experience, graduation_status,
graduation_year, political_status, license, language, other. Scope is `notice` or
`position`. Education and degree are not interchangeable.

Operators are equals, at_least, at_most, in, contains, unspecified. A bounded
`condition_logic` tree references IDs and expresses explicit `all`/`any` groups.
Nonempty conditions require a tree covering every condition; no hidden default
AND or code expression is evaluated. Exceptions/reference dates must remain in
values or unresolved wording. Unknown values retain their tag.

```json
{
  "employment_type": {"value": "personnel_agency", "tag": "ai_inferred"},
  "conditions": [
    {"id": "degree", "type": "degree", "operator": "equals", "scope": "position", "value": "博士", "tag": "source"},
    {"id": "experience", "type": "work_experience", "operator": "at_least", "scope": "position", "value": 3, "tag": "source"}
  ],
  "condition_logic": {"operator": "any", "items": [{"condition_id": "degree"}, {"condition_id": "experience"}]}
}
```

This is a shortened example, not a complete accepted position. Experience units
and age reference rules must be supplied in real condition values when applicable.

## Extraction and public reads

The Recipe envelope includes `schema_version`, `status`, `positions`, `errors`
and, after finalization, `validity`. Config uses
`position_contract: position-facts-v2`. Authoring receives the new schemas and
instructions; source scripts remain Agent-owned. The bootstrap table helper
`youju.positions.parsing.extract_position_facts` splits recognizable simple
requirements, preserving complex clauses as unresolved `other`. Source-specific
scripts or the existing tool-free xdog detailed node must handle richer structure.

Unknown/open-ended/until-filled notices still take the full extraction path;
expired notices use scripts without a model call. Missing attachments permit
partial parsing with warnings. Validation checks tag/null consistency, canonical
dates, bounded logic, notice validity and recognized-row coverage (including
repeated titles). It does not prove semantic accuracy or universal completeness.
A source-tagged cutoff must occur as an application cutoff in saved material.
AI-inferred dates cannot hide jobs or establish whole-notice expiry.

Public jobs expose `facts` plus existing source/audit/material metadata, not old
flat fields. Filters read employment type and education/degree condition values;
search is literal, not a qualification test. The client labels source/inferred/
unknown values and displays conditions and their combination. Reports capture the
exact tagged value and condition logic; reporting/review does not start repairs.

## Fresh database and verification

No old-contract adapter or data migration is provided. For an old database, use
a **new path** rather than assuming `create_all` upgrades it:

```sh
cd server
uv run --frozen flask --app youju:create_app init-db --database youju-structured.sqlite3
uv run --frozen pytest -q tests/test_position_facts.py tests/test_position_pipeline.py tests/test_jobs_api.py tests/test_feedback_api.py
```

Old databases are not automatically deleted. Use fresh source/workspace storage
and regenerate/audit recipes for a fresh POC. Existing native sessions may need
an explicit new workspace because the trusted guide/profile changed.
No service restart, source regeneration, live model call, upload or deployment is
part of this implementation. Offline tests are not USTC or device verification.

### Recorded verification (October 15, 2026)

- Server tests ran in two disjoint groups: 334 passed / 2 opt-in sandbox skips,
  plus 38 passed in the remaining three files. Ruff passed.
- Client: 67 tests and TypeScript checking passed. Staged H5/weapp builds passed;
  H5 retains a bundle-size warning. No served static root was overwritten.
- Real isolated template scripts ran through xdog-flow without model/network calls.
- A fresh synthetic SQLite/Flask response was accepted by the actual client parser,
  including zero count and AI-inferred fields. Staged Chromium list/detail rendering
  verified tagged fields, OR conditions and inferred-cutoff warnings without page
  errors; external requests were blocked. This is not a real-source replay.
- Receipts/builds are under `server/.artifacts/position-model-v2-20261015/`.
  Live USTC recipe generation, source-wide audit and WeChat device behavior remain
  unverified for this new contract. Existing workspaces/DBs were not reset.
