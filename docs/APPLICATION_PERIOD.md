# Declared Application Periods

## Separate three facts

`validity.status` is the existing **explicit-clock expiry** result:
`expired`, `not_expired`, or `unknown`. It does not establish current vacancy
availability. With no fixed application cutoff, that comparison remains unknown.
Publication, age, arrival and capture dates must not be substituted for a deadline.

`validity.application_period` describes the **literal source-declared rule**:

| kind | Meaning | Suggested Chinese presentation |
|---|---|---|
| fixed_deadline | Unambiguous application cutoff | 明确报名截止日期 |
| open_ended | Explicit long-term/rolling declaration | 公告声明长期有效，无固定截止日期 |
| until_filled | Explicit fill-based stop condition | 招满即止，空缺情况待核验 |
| not_stated | No supported notice-level period stated | 未说明报名期限 |
| unknown | Conflict, incomplete page or unresolved scope | 期限规则待核验 |

`validity.current_hiring_status` is `not_verified`: neither a future deadline nor
an open-ended declaration proves that all jobs remain open, amendments are absent,
or eligibility is satisfied. A consumer must not show just “有效期未知” when an
explicit open_ended rule is present. Actual current-open/closed verification and
cross-announcement cancellation handling are separate future responsibilities.

## Output and evidence contract

The declaration object requires exactly `kind`, `text` and `evidence_ids`. A known
rule contains a literal quote and actual globally scoped block IDs. Unresolved and
not-stated rules use text=null and evidence_ids=[]. The known cutoff quote must
contain the recognized application date, not merely a publication date in the same
paragraph. No phantom dated deadline is created for open-ended or fill-based rules.

```json
{
  "status": "unknown",
  "deadline": null,
  "basis": "rolling",
  "application_period": {
    "kind": "open_ended",
    "text": "本招聘启事长期有效。应聘者可随时提交报名材料，学校将适时组织考核。",
    "evidence_ids": ["<actual saved block ID>"]
  },
  "current_hiring_status": "not_verified"
}
```

The example sentence is not transferable evidence for another source. Scope and
material_issues remain unchanged. Missing attachments do not erase readable-page
declarations, but truncated/unreadable pages, per-row conditions, conflicting
rolling/date rules, negations and observed closure language are not promoted into
unconditional notice-level openness. Credential validity is unrelated. The future
procedure “报名结束后将进行资格审查” is not evidence that registration already closed.
A long-term declaration qualified by 招满为止 is until_filled.

## Agent-owned generation

The trusted extraction guide, author system prompt and native runtime instructions
now teach this distinction. They expose the reviewed pure helper
`application_period_hint(bundle)` alongside check_validity. The source Agent can
combine them or implement an equivalent literal parser, preserving source identity
checks and evidence. No per-page Agent/model is needed to evaluate periods.

The host schema permits the new fields. Independent validators check kind, quoted
text, source IDs, date/negative/scope consistency and prohibit a parser from asserting
current hiring. Native authoring rejects an omitted known open-ended/fill rule with
APPLICATION_PERIOD_REQUIRED, so old acceptance or a prose explanation is insufficient.
Legacy outputs without the new fields remain schema-compatible; fields supplied by
any path must pass validation. Clock-expiry rules and expired model-free routing are
unchanged. Status replay compares the original expiry core and independently
revalidates enrichment; it does not discard the declaration to force a match.

## Explicit guidance upgrade

A saved native checkpoint pins the trusted guide digest. Updating prompts therefore
requires approval, not silently ignoring mismatches. The built-in worker accepts
`--approve-guidance-update` together with `--enable-models`. This can rebind only the
changed guide digest; provider, SDK, skill, source and profile identities must still
match. It restores the unchanged native conversation, reasserts current trusted
instructions and persists the new binding before any model or tool dispatch. Older
snapshots remain immutable. Clients cannot grant this flag through RPC; custom
runtime factories must explicitly implement their own operator approval.

Changing the toolkit also changes its fingerprint. Existing canonical source recipes
require normal revalidation/audit before their old validation fingerprint can be
used; no live record, old accepted artifact or evidence check is patched to bypass
that guard. This work verifies a managed-source repair, not automatic migration of
all source recipes.

## Recorded USTC repair (October 13, 2026)

The standard managed SubmitFeedback/repair path resumed the real gpt-6-astra Agent
in the same workspace/session with explicitly approved guidance. It completed in
**seven turns with one accepted submission** and changed only **validity.py**.
The host did not hand-edit generated source files. The Agent preserved source_id
checking and added the declared application rule and not_verified hiring status.

A fresh real bubblewrap/workflow sample audit returned **11 positions**, no blocking
errors, kind=open_ended and the original b17 evidence. Deadline remains null; the
2025-05-20 notice signature is not a cutoff. No detailed extraction-model call,
baseline download, external callback or canonical source activation was performed.
This is not a full 184-notice rerun or proof of currently open hiring.

Preserved under `server/.artifacts/recipe-native-241-20261012-ustc/reports/`:

- application-period-request.json: exact parent version and feedback.
- application-period-agent.diff: the actual xdog Agent-owned script change.
- application-period-verification.json: output, evidence, counts and usage.
- Native immutable candidate, checkpoint and attempt history in the service blob DB.

## Full saved-notice replay (October 13, 2026)

The later replay used managed task `15dd1150ca5e4d2c8be43772850b813d`, recipe
`3c39803b5cfb27d368f4186617cc1d6c0143b3e340942984d4e67c471320e8ef`,
with a fixed evaluation clock of `2026-10-13T05:43:35.426402+00:00`.
All **184 identified announcements** ran fresh through official xdog-flow and
isolated scripts, including partial material and previously failed resources.
This is coverage of the saved classified inventory, not every notice on the website.

| Disposition | Announcements | Stored position rows |
|---|---:|---:|
| Complete material, parsed | 16 | 126 |
| Available material parsed, incomplete acquisition | 147 | 200 |
| Content/classification review | 19 | 0 |
| Failed independent validation | 2 | 0 |

All 184 retained validated expiry and declared-period metadata:

- Clock expiry: 137 expired, four not_expired, 43 unknown.
- Declared period: 141 fixed_deadline, three open_ended, six until_filled,
  19 not_stated, 15 unknown.
- Current hiring status: not_verified for every notice. The **326 stored position
  rows are historical evidence-backed records**, not 326 currently available jobs.

Actual node starts confirmed 137 expired fast branches and 47 current/unknown
script branches. No live model calls, downloads, reclassification, author runs or
source activation occurred. The original source lifecycle pointers and acquisition
record counts were unchanged. New rows reside only in the diagnostic database.

The two failures have SUSPICIOUS_ZERO_POSITIONS. All 19 review notices have
NO_POSITIONS; two also retain classification/material defects and unsupported
position-table layouts. Error occurrence counts must not be mistaken for affected
notice counts. Compared with the previous active audit, **two previously partial
notices regressed from one extracted position each to zero/review** (notice signs
639014112026147614 and 638731548331617007). No other previously accepted notice lost
its accepted status or position count. The managed candidate is therefore **not
ready to replace the active recipe**. Fewer failed dispositions do not prove every
previous failure was fixed: some now remain zero-position review results.
Source-specific repairs must go back through the xdog Agent, not host hand edits.

Artifacts in `server/.artifacts/ustc-native-all-notices-20261013/`:

- `request.json` and `candidate.json`: exact inputs, recipe identity and clock.
- `source-snapshot.sqlite3` and `blobs/`: private database with immutable material
  copies, new SourceRecipe/AuditRun/AuditResult records and 326 new Position rows.
- `audit-report.json`: every notice's disposition, warnings, validity and flow path.
- `verification.json`: coverage, persistence, original-state comparisons,
  notice-level error counts and status/position-count regressions.

From `server/`, use a **new output directory** for every replay; existing output is
not overwritten. This diagnostic intentionally rejects workflows requiring the
live detailed-extraction model rather than silently changing their behavior.

```sh
uv run --frozen python scripts/audit-managed-notices.py \
  --database .artifacts/ustc-live-20261011/source.sqlite3 \
  --source-storage .artifacts/ustc-live-20261011/blobs \
  --recipe-storage .artifacts/recipe-native-241-20261012-ustc/blobs \
  --task-id 15dd1150ca5e4d2c8be43772850b813d \
  --output .artifacts/ustc-native-all-notices-20261013-next
```

Verification: the real 184-notice replay and database assertions completed;
13 application-period tests passed; the replay script passed Ruff lint and format
checks. This is not a browser/current-vacancy check or source activation.
