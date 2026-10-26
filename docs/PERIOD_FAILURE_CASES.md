# Saved-notice period failure cases — independent sidecar audit

Date: **October 24, 2026**. Scope: offline empirical input to the requested deadline
redesign, not an implementation, deployment, semantic gold set, or accuracy report.
The main implementation can change independently; all observations below refer to
one **saved pre-redesign replay**, not to the current working tree's behavior.

## 0. Required boundary: acquisition completeness is diagnostic only

The user's explicit redesign requirement applies to **both available position
parsing and date extraction/computation**. `material_status`, `material_issues`,
missing attachments, fetch failures, `page.partial`, and `truncated` are diagnostics,
not generic acceptance gates for either output. Keep every position/date fact
supported by the readable evidence, even when other facts are unavailable.

**Counterfactual invariant:** holding readable registration text/table and its
meaning fixed, adding/removing fetch failures or changing only acquisition flags
must leave the extracted position facts, date facts and date computations unchanged.
A concrete missing field remains unknown for that field; it does not erase the row,
other dates, other positions or the whole output. This also applies when an
attachment's purpose is unknown: absence or uncertain purpose alone is not a veto.

Only **concrete temporal evidence**—an explicit time-delegating clause, actual
unresolved scope/row ownership, incompatible rules for the same scope, or unsupported
timing purpose/semantics—can prevent a supported computation for the affected
field/scope. If the primary delegates a scope's date to an unavailable annex, that
scope's date is factually unavailable; other supported dates/positions still survive.
No primary/page/annex completeness precondition is proposed by this report.

## 1. Evidence boundary and reproducibility

The sidecar read `AGENTS.md` and `README.md` first. This sidecar writes only this new document
and its own `sidecar_*` artifacts under
`server/.artifacts/period-redesign-20261024/`. It does not change production data,
source, tests, dependency declarations, or the other agent's `before*` artifacts.
No models, network requests, attachment downloads, or external actions were used.

Input:

- Replay: `server/.artifacts/sol-flow-20261024/source-accuracy-template-current.json`.
- Replay SHA-256:
  `39ef75a62c7800b4d7029d5991d34ef562237be42e66dd3bf504abba69155537`.
- Frozen replay clock: **2026-10-24T13:55:03+08:00**, not the sidecar execution time.
- Source: `6fe41fd513a84c508760d07ee96bca11` (one USTC source).
- Saved contract/evaluator: `application-period-v2` / `application-evaluator-v2.2`.
- Content root: `server/.data/source-blobs/`. Both `bundle_body_ref` and the
  primary page's `sha256` are **direct content-addressed file names** under this
  root. The raw primary is not an assumed `pages/` path or an attachment URL.

Selection is exactly replay records where `business_type == recruitment` and
`article in page_roles`: **183 / 1,314** replay records. This is the requested
operational “recruitment details” denominator, not an independently verified
183-notice truth set. In particular, **16 / 183** have the generic site title and
`jobjs.aspx` job-demand pages (C15); do not silently relabel or subtract them to
improve a period metric. The other **167 / 183** have `indexnews.aspx` URLs.

The census traverses all 183 bundle/page representations and attachment-link
contexts. The 15 walkthroughs are purposively selected failure/negative/control
examples, not random sampling or complete semantic adjudication. `sidecar_census.json`
keeps a row for every selected record; `sidecar_cases.json` keeps exact bundle IDs,
material versions, raw-body hashes, full original evidence IDs and literal quotes.
Only short non-contact excerpts appear here. Missing attachment bodies were **not**
inspected: their purpose below means the primary page's description, not verified
contents of those files.

Reproduce locally, from repository root, without invoking the changing period code:

```sh
UV_PROJECT_ENVIRONMENT=/home/zhang/workspace/xdog-youju/server/.artifacts/recipe-redesign-20261023/venv \
  uv run --project server --frozen python \
  server/.artifacts/period-redesign-20261024/sidecar_audit.py
```

This is a counting/reference-validation script, not a duplicate deadline extractor.
It imports no production period evaluator and makes no independent status predictions.

## 2. Census: what the saved outputs actually say

| Observation | Count / denominator | Meaning / limit |
| --- | ---: | --- |
| `fixed_deadline` rules | 142 / 183 | Rule kind, not validated notice closure |
| `not_stated` rules | 18 / 183 | Includes C12's visible old endpoint and 16 job-demand pages |
| `unknown` rules | 14 / 183 | Different from unknown evaluated status |
| `until_filled` rules | 6 / 183 | Finite timing can still coexist with this label (C10) |
| `open_ended` rules | 3 / 183 | Known textual rule is not a current-vacancy guarantee |
| Evaluated `unknown` | 181 / 183 | Includes 140 fixed rules |
| Evaluated `expired` / `not_expired` | 1 / 183 each | Both are complete-primary Suzhou examples; not current hiring verification |
| Fixed rules evaluated unknown | 140 / 142 fixed rules | The specific redesign bottleneck |
| All three material blockers on fixed-unknown records | 140 / 140 | `bundle_not_complete`, `bundle_errors`, `incomplete_material` co-occur; not 420 notices |
| Only those material blockers | 135 / 140 fixed-unknown records | Candidate overblocking population, **not** 135 independently proven recoveries |
| Material blockers plus another unresolved reason | 5 / 140 | Two `unsupported_or_multiple_dates`; three `ambiguous_date_purpose` |
| Saved fixed-unknown endpoint before / after clock's date | 136 / 140; 4 / 140 | String/date inventory only; does not validate scopes or compute eligibility |
| Primary `status=ok` / `partial` diagnostics | 182 / 183; 1 / 183 | All 183 have `truncated=false`; neither flag is a generic parsing/timing gate |
| Records with saved failed attachments | 155 / 183 | Exactly one attachment entry per such record |
| Failed attachment entries | 155 / 155 | Every error is `attachment_outside_approved_scope`; no attachment version/body is invented |
| DOCX / XLSX attachment entries | 148 / 155; 7 / 155 | File suffix alone does not identify purpose |
| Generic attachment label `点击下载` / `点击此处` | 152 / 155; 1 / 155 | Numeric filenames plus generic labels are insufficient |
| Descriptive attachment labels | 2 / 155 | Both explicitly name an application form |
| Primary link context names a form/application workbook | 155 / 155 links | Includes XLSX with application and personal-summary worksheets (C04) |
| `per_position_deadlines` unresolved reason | 8 / 183 | All eight have readable primary tables and a missing form, not a missing acquired job table |

The 135/140 split is solely a partition of saved `coverage.unresolved`. It is not
accuracy, recall, a semantic label, a promised change in final dispositions, or
permission to invent a notice-wide scope. Remove acquisition-based vetoes entirely;
keep supported facts and compute their dates independently. Any remaining unknown
must identify its concrete missing field or temporal semantic problem, not a
material-completeness gate.

### Missing attachment purpose and actual delegation

For all **155 / 155** saved attachment links, primary anchor/paragraph text describes
an application form or application-information workbook. No link in this selected
set is described there as a separate job table or registration schedule. This is
an observed purpose census, **not** proof that an unseen file contains no dates.
The two descriptive labels are `中科大附中实验学校副校长岗位应聘表` and
`合肥微尺度交叉科学国家研究中心应聘申请表`. For the other 153 links, use the
adjacent primary text, not just the label or a numeric URL tail.

In this saved-primary review, **0 / 183 selected representations** furnished an
explicit time-delegating annex clause of the form “报名时间以附件为准” or
“各岗位报名截止时间详见附件”. This is a bounded sidecar observation, not a
verified absence in unavailable attachments or a tested delegation capability.
There is therefore **no actual positive annex-delegation walkthrough to manufacture**.
C07–C08 instead show actual row timing already present in the primary. Delegation
must remain a generic negative requirement (§4), with independent fixtures or
future saved cases needed by the main implementation.

## 3. Fifteen actual-case walkthroughs

Within each case, `bN` is the original primary block suffix, bound to the **full
page version** printed there. Full `resource_id:page_sha256:bN` references and both
content-addressed paths are in `sidecar_cases.json`; these are source block IDs,
not invented line/page numbers. `material-only` below abbreviates the three
co-occurring material blockers listed in §2. “Reasonable determination” means a
proposed interpretation of the saved notice at the frozen clock, never verified
current hiring or subsequent-amendment status.

### C01. 中国科学技术大学 中国科大生命科学与医学部劳务派遣岗位招聘启事
- Saved bundle: `5d173a2c6fc5494ba356732d7e3bb823`.
- Primary material version: `9b4eaf887b98448f83ae7d97e50ec4dd`.
- Literal timing/purpose excerpts: `b16` “提交报名材料，截止期为 2025 年 12 月 20 日”.

The missing DOCX's label is only `点击下载`; primary `b8` calls it a
`劳务派遣岗位招聘报名表`. The saved rule already has the notice cutoff
2025-12-20 but evaluated status is unknown with material-only blockers. The
primary recruitment-procedure sentence supplies the time independently of filling
out the form. Reasonable determination: the **saved notice's stated cutoff has
passed**, while the missing form and unverified later changes remain visible.
Do not require successful position/form extraction to acknowledge this cutoff;
do not claim the position has actually been filled.

### C02. 中国科学技术大学 中国科大微尺度物质科学国家研究中心劳务派遣岗位招聘启事
- Saved bundle: `ed15488f237f4d58ba8ee89ff66f2f06`.
- Primary material version: `ad463f4e1ca342699efbb6875279990a`.
- Literal timing/purpose excerpts: `b16` “提交报名材料，截止期为 202 6 年 11 月 14 日”.

This micro-scale research-center notice has the same form-described missing
DOCX (`点击下载`; `b8` says `劳务派遣岗位 应 聘报名表`). Its saved fixed rule is
2026-11-14, still unknown with material-only blockers. Reasonable determination:
the **stated deadline has not passed** at the replay clock. It is not a reason
to archive the notice. Internal whitespace in `202 6` is present in the saved
quote and should not become a fabricated year or a new semantic condition.
A future end does not, by itself, establish an opening date or verified availability.

### C03. 中国科学技术大学 中国科学技术大学体育教师招聘启事
- Saved bundle: `6c0b9002b3044ad19422f2569ce3b1f0`.
- Primary material version: `21fd8a87836a4d339ada1acb885380c6`.
- Literal timing/purpose excerpts: `b16` “四、报名截止时间”; `b17` “2026 年 3 月 15 日。”.

This sports-teacher notice's missing DOCX is described by primary `b11` as
`中国科学技术大学体育教师招聘报名表`, not a time schedule. The saved rule
successfully combines `b16` and `b17` into a 2026-03-15 window, yet retains
`unsupported_or_multiple_dates` plus material blockers. The raw HTML contains
**one**, not two, occurrences of this cutoff heading. The relevant duplication
is the extraction view: the standalone date-free heading and its paired value
are overlapping candidates, not conflicting notices. Bind both original blocks
as one declaration; do not let its already-consumed heading become an independent
unsupported-date blocker. The saved primary supports an elapsed cutoff.

### C04. 中国科学技术大学 合肥国家实验室技术支撑人员招聘启事
- Saved bundle: `24268e1e44f14bcdb40e051eaba4b23e`.
- Primary material version: `3e5a7551a6004ad3b4a9f8e67c5a5d4d`.
- Literal timing/purpose excerpts: `b21` “四、报名截止时间”; `b22` “2022 年 12 月 10 日。”; `b20` “《应聘申请表》”; `b20` “《个人基本信息汇总表》”.

The failed XLSX is labelled `点击此处`. Its primary paragraph explicitly
instructs completion of an `应聘申请表` and `个人基本信息汇总表`. Thus XLSX
must not automatically mean “missing job table with unknown deadlines”. The
primary already displays five job rows without a timing column, followed by
the notice-wide 2022-12-10 cutoff. Saved output has that fixed window but also
`unsupported_or_multiple_dates` and material blockers, another heading/value
case. The separate birth-date condition in `b8` (1984-10-01) belongs to age,
not registration. Reasonable determination: the primary notice cutoff elapsed;
the applicant workbook and qualification completeness remain unresolved.

### C05. 中国科学技术大学 中国科大对外联络与基金事务处劳务派遣岗位招聘启事
- Saved bundle: `b603769e26f64b398dc080b155b2271f`.
- Primary material version: `66c7dcb34a52407a92243af32dc66590`.
- Literal timing/purpose excerpts: `b16` “提交报名材料，截止期为 2026 年 8 月 20 日”; `b24` “并于 报名截止 后 一周内通知本人”.

For the external-relations/fund-affairs notice, a `点击下载` DOCX is
form-described in `b8`. The saved fixed window is 2026-08-20 but unknown status
adds `ambiguous_date_purpose`. The later sentence in `b24` promises notification
**within a week after** registration closes; it supplies no replacement intake
endpoint. Keep the 2026-08-20 registration rule and the review/notification stage
separate. This is an observed false-blocker candidate, not permission to ignore
all sentences containing “截止后”. A clause allowing late applications or
supplementary submission could actually change a registration scope. The same
notification wording also occurs on replay index 98, not counted as a new rule here.

### C06. 中国科学技术大学 关于公开招聘中国科学技术大学基础教育集团所属中科大附中实验学校副校长的公告
- Saved bundle: `b46236481d6c4bdebc14ee61a083c258`.
- Primary material version: `4d08eb5bcd90400a8d1b6dc6f56bce40`.
- Literal timing/purpose excerpts: `b18` “应聘材料投递截止时间： 2025 年 8 月 24 日 24:00 。”.

The deputy-headteacher notice's missing link is explicitly labelled
`中科大附中实验学校副校长岗位应聘表`. The saved window is only day-precision
2025-08-24, with `ambiguous_date_purpose` and material blockers. In the same
primary block the email-subject instructions mention a graduation institution;
that does not turn the later, expressly named application-submission cutoff
into a graduation-date requirement. Isolate the timing clause. Preserve literal
`24:00`; if supported, normalize it to **2025-08-25T00:00+08:00, exclusive**,
not 00:00 at the start of August 24 or minute-inclusive 00:01. At this replay
clock even the conservative complete August 24 interval is past. This case does
not independently test exact-boundary behavior.

### C07. 中国科学技术大学 中国科大碳中和研究院劳务派遣岗位招聘启事
- Saved bundle: `038c0a68ab4f414090a5222b0147fd7e`.
- Primary material version: `91ab1356402d4d0e95f824e9a112ffff`.
- Literal timing/purpose excerpts: `b36` “报名截止期： 2025 年 11 月 30 日”; `b42` “报名截止期： 2025 年 11 月 30 日”.

The carbon-neutrality notice's primary procedure says only to submit
materials. Its readable `t1` table has **two** job rows; both remarks cells give
2025-11-30. The missing DOCX is an application form (`点击下载`, with its purpose
in `b8`). Saved kind/status are unknown and the extra reason is
`per_position_deadlines`. These dates need row ownership, not a missing-annex
assumption. Bind each remarks cell to its own row and compute the two supported row dates:
both represented cutoffs are past. The form's absence must not erase those rules
or block available position parsing. Equal endpoints alone do not expand their
scope to other recruitment not represented by that declaration.

### C08. 中国科学技术大学 中国科大火灾安全全国重点实验室劳务派遣岗位招聘启事
- Saved bundle: `f6a651223c11441296eb2c7d97e2b654`.
- Primary material version: `5ee4b73e86424627a7be32d1bcbaa91b`.
- Literal timing/purpose excerpts: `b36` “报名 截止期： 2025 年 12 月 3 0 日”; `b42` “报名截止期： 2025 年 12 月 10 日”; `b48` “报名截止期： 2025 年 12 月 30 日”.

This fire-safety laboratory page has **three** primary job rows: two
research-assistant rows and one management-assistant row. Their quoted cutoffs
are December 30, December 10 and December 30, 2025. One date contains the source
spacing `3 0`; retain the original evidence while normalizing only the digits.
The only failed attachment is again a form. Saved output is unknown with
`per_position_deadlines`, not an independently established contradiction.
Different rows are distinct scopes: taking the first deadline would close later
rows too early. All three represented row deadlines are
past at the replay clock; at an intermediate clock preserve individual row states.
Do not take the latest date across unrelated or unenumerated scopes.

### C09. 中国科学技术大学 中国科大苏州高等研究院公共实验中心工程师岗位招聘启事
- Saved bundle: `1c867125953a470a8090fb0cd6372a07`.
- Primary material version: `62a7493c956a48e4bc45a98d54497fdf`.
- Literal timing/purpose excerpts: `b53` “滚动招聘，招满即止，本轮报名截止时间： 2026 年 4 月 25 日。”.

The Suzhou public-experiment-center page has no failed attachments.
Saved kind/status are unknown with rolling/association reasons. The primary
combines rolling recruitment, filling the quota and an explicitly named
**current round** cutoff. The April 25, 2026 round has passed; that fact cannot
close later rolling rounds or prove that hiring has stopped. This is a genuine
scope limitation, not a material-completeness false blocker. Keep the round's
finite rule alongside the open rolling/fill condition; do not force a whole-notice
expired disposition merely because one quoted date is historical.

### C10. 中国科学技术大学 中国科大人文与社会科学学院招聘考古学教授、副教授启事
- Saved bundle: `eb6b570c4ce344098d24af9092a450c7`.
- Primary material version: `f4da037e649f4caaa994633d79e4ae04`.
- Literal timing/purpose excerpts: `b24` “2025 年 1 月— 2025 年 12 月 ，招满为止。”.

For the archaeology professor/associate-professor page, saved kind is
`until_filled` and evaluated status unknown. There are no saved failed attachments.
The primary declares a January–December 2025 range **and** filling the quota,
so retaining only the fill condition loses the stated outer interval. At the
October 2026 clock that whole declared interval is past, without determining
when an early fill occurred. Represent the range at its actual month precision
and preserve the early-fill qualifier. A finite historical outer bound can be
useful if the contract validates that interpretation; it is not a license to
invent a literal December 31 day/time or silently treat every “date or filled”
expression as the same conjunction (contrast C14).

### C11. 中国科学技术大学 中国科学技术大学国家同步辐射实验室支撑岗位招聘启事
- Saved bundle: `bd3f92977cfa48aaa2363c00f90e3474`.
- Primary material version: `7b0a1987d3cc469989be85233fd704d5`.
- Literal timing/purpose excerpts: `b17` “本招聘启事长期有效。应聘者可随时提交报名材料”.

The national-synchrotron support-position page has no failed attachments.
Saved kind is `open_ended` with unknown evaluated status. Its long-term declaration
is a **known rule**, not an unknown date to be guessed from its title or year.
No finite deadline can be concluded from this clause; current vacancy/filling
is also not verified. Keep the long-term text discoverable and separate temporal
rule extraction from the decision to use an expired-notice shortcut. The later
qualification-review notification does not create a finite application end.

### C12. 中国科学技术大学 中国科学技术大学公开招聘部分学院执行院长公告
- Saved bundle: `0b696f0cfbbd483fa347c00b7252940a`.
- Primary material version: `874c3b75ed3d46d584b2a597b19477a4`.
- Literal timing/purpose excerpts: `b45` “七、报名时限”; `b46` “自公告发布之日起，至2014年6月30日止。”.

The executive-dean recruitment notice has no failed attachments. Saved
kind is `not_stated`, despite the primary's express endpoint in `b46` following
`报名时限`. This illustrates a vocabulary/heading association gap, not lack of
a source date. The final endpoint is fully year-qualified, so no capture-time or
publication-year inference is needed to conclude that the saved 2014 cutoff is
past. “自公告发布之日起” supplies opening context here, not a reason to discard
the independent explicit end. No workday calendar or relative-day counting is
needed for that endpoint.

### C13. 中国科学技术大学 中国科大合肥微尺度国家研究中心副主任招聘启事
- Saved bundle: `b3cfe9873a074578ab6aed9b2c1b0514`.
- Primary material version: `fe1f79a58c7948568e3e3e15164a5310`.
- Literal timing/purpose excerpts: `b25` “截至日期为 11 月 20 日 。”.

This micro-scale center deputy-director page has an explicitly labelled
application-form attachment, but its primary cutoff is only November 20 with
**no year**. Saved kind is `not_stated` and unresolved includes material blockers
and `unsupported_or_multiple_dates`. The saved primary exposes no independent
publication-year anchor establishing the missing year. Removing the form blocker
therefore cannot justify a finite evaluated deadline. Keep the literal timing
clause known and its year unresolved; do not borrow 2026 from capture/replay,
a numeric URL, or neighboring notices. This is a necessary negative case against
blindly converting all form-incomplete records into dated expiry.

### C14. 中国科学技术大学 中国科大诚聘特任副研究员（特任高级工程师）暨博士后
- Saved bundle: `737fdd0eece34d86b20d8d9081d66694`.
- Primary material version: `5777a84a82d94dc8996192fe37fc0cea`.
- Literal timing/purpose excerpts: `b21` “非完全版，供参考。”; `b1330` “长期有效”; `b1435` “2026年12月30日或待招满为止”; `b1821` “2026年12月”.

The special-appointed/postdoc compilation is the one `partial` primary;
its bundle/page diagnostic is `INVALID_TABLE_SPAN` (rowspan exceeds source rows),
not an attachment failure. The page itself says it is incomplete/reference-only.
**Neither that flag nor that disclaimer vetoes any readable date/position fact.**
The concrete temporal limits are instead the represented long-term/until-filled
rows, month-only dates, and the 2026-12-30 **or** until-filled expression. Saved
kind/status are unknown. Continue parsing available positions, retaining each
supported field/date and computing independently supported row dates. Only a
specific broken cell-to-row association makes that association unknown; the
span diagnostic alone does not. Do not infer whole-page closure from a finite
row while other represented rows are long-term, or resolve the disjunction by
taking a maximum. Different rows' dates are not inherently contradictory.

### C15. 中国科学技术大学人才招聘网
- Saved bundle: `0dec6e6e64e14eaa94f1cedf0708274e`.
- Primary material version: `2c346bf975114b9b84c843c9f298ea26`.
- Literal section identifier: `b60` “马克思主义学院高级专业技术岗位招聘”.

This selected record is actually a `jobjs.aspx?sign=12000` standing
job-demand page, with navigation and a job table. It has **no literal timing
quote** to reproduce: the excerpt above identifies its primary job section only.
Saved kind is `not_stated` and evaluated status unknown, without unresolved
blockers. It exemplifies the **16/183** generic-title `jobjs.aspx` records in the
operational detail denominator. Do not infer expiration or long-term validity
from an absent deadline, the page category, or age requirements in the table.
This is a classification/notice-form boundary for the main system to preserve,
not an additional deadline-parser recovery to count.

## 4. Generic rule boundaries recommended to the main implementation

These are requirements inferred from the saved cases, **not delivered behavior**.
Do not implement a list of USTC units, source URLs, dates, or case IDs as exceptions.

### A. Acquisition diagnostics must not gate either output

1. **Keep available position parsing and time processing independent of material
   completeness.** Never use incomplete bundles, failed/missing attachments,
   partial/truncated flags or acquisition-error lists as generic reasons to skip
   parsing, erase supported fields, block date extraction, or block calculation.
   Keep those diagnostics visible alongside available facts. The invariant in §0
   is unconditional with respect to acquisition flags, not limited to form-labelled
   attachments or `status=ok` primary pages.
2. **Use concrete evidence at field/scope granularity.** Extract readable
   declarations, supported dates/precision and positions. Compute what their
   concrete temporal meaning supports. If a year is absent (C13), that date's year
   remains unknown. If a particular table association is indeterminate, that
   association remains unknown. Do not erase other rows, supported dates or
   qualifications, or infer a failure from a partial/truncated flag (C14).
3. **Retain link labels/purpose as evidence, not a new acceptance gate.** Keep
   label, surrounding paragraph/section, URL and failure diagnostics. Numeric
   filenames, generic labels and suffixes cannot prove purpose. The 155 form/
   workbook descriptions explain the observed blanket veto's poor fit; they are
   **not an allowlist or precondition** for extracting/computing primary dates.
   An unknown-purpose link by itself does not block an otherwise supported fact.
   An explicit clause delegating registration timing to an unavailable annex is
   different: the delegated field lacks its specified temporal evidence, while
   unrelated supported dates and position fields remain available.
4. **Qualify conclusions, not permission to parse.** A supported primary rule can
   describe an elapsed/not-yet-elapsed saved cutoff, not proof that an unseen file
   is date-free or that later changes/current hiring were verified. Preserve that
   scope and uncertainty without suppressing the supported result. An incomplete
   form/job table does not invalidate readable primary rules; a completely acquired
   bundle does not resolve an actual date conflict or scope ambiguity.

### B. Bind local clauses and roles before judging conflicts

5. **Consume heading/value pairs once.** An adjacent short timing heading and
   date paragraph should contribute both original IDs to one candidate. Its
   standalone date-free heading must not subsequently block that same candidate
   (C03–C04). Deduplicate overlapping views, not genuinely separate declarations.
6. **Judge the date's local purpose, not keywords anywhere in the paragraph.**
   Application intake, age/birth requirements, graduation, interviews, fee payment
   and post-cutoff notification are different event roles. A subject-line example
   mentioning graduation does not invalidate a named submission deadline (C06).
   A notification after closure is not another application window (C05). Do not
   globally suppress any text containing “通知”, “毕业” or “截止后”: an actual
   late-submission permission or time change still requires analysis.
7. **Keep evidence while normalizing supported notation.** Internal whitespace
   in date digits is present in C02/C08. Normalize only well-supported notation;
   never rewrite the literal quote or infer a year for C13. Keep the end-time
   precision and timezone. An explicit `24:00` must not become the beginning of
   the same day or silently acquire a one-minute extension (C06). Day-only dates
   do not authorize a fabricated office closing hour.
8. **Different dates are not automatically conflicting.** Distinguish publication,
   intake, review and birth dates; distinguish row/round ownership. Equal dates
   in two rows do not remove the coverage requirement. Different dates in
   different rows are scoped rules, not a single contradictory notice. Two
   incompatible registration ends for the **same** scope without established
   precedence must remain unresolved; do not pick the latest by convenience.

### C. Treat finite scopes, bounded exceptions and open rules differently

9. **Use primary row rules when available.** C07–C08 need row-to-cell ownership,
   not a successful download of an application form or full position extraction.
   Compute each supported row date independently. A whole-notice temporal claim
   additionally needs concrete notice-wide scope support; do not infer an unknown
   row from an acquisition flag. An **actually represented** open-ended or
   temporally unresolved row prevents a universal expired claim, not extraction or
   computation of other rows. Keep scope identity for future/intermediate clocks.
10. **Preserve round qualifiers.** “本轮” inside rolling recruitment can establish
    the round's cutoff but cannot end unspecified later rounds (C09). Preserve
    both the finite round and the fill/rolling rule. A broad post-cutoff status
    cannot replace the scoped result.
11. **Do not collapse all fill conditions together.** A stated finite outer
    interval plus an early-fill condition (C10) is different from a bare
    until-filled declaration, a rolling next round, or an ambiguous “date **or**
    until filled” row (C14). A passed unambiguous outer interval can be evaluated
    without knowing the early fill date, but month precision and conjunction/
    disjunction semantics must remain explicit. Otherwise retain the known text
    and abstain rather than guessing a global endpoint.
12. **Retain known nonfinite rules and honest unknowns.** Long-term recruitment
    is a known rule with no finite end (C11), not a failed extraction and not
    proof of active vacancies. A missing year (C13), an unbounded mixed table
    (C14), or no timing clause (C15) cannot become an expired notice through a
    heuristic current year, URL year, category, or capture timestamp.

### D. Negative requirements not positively represented by this saved corpus

The following are **illustrative requirements, not quotations from the 183
records** and not claims that these cases were empirically validated here:

- **Explicit temporal delegation:** if a primary says registration times are in
  an annex or each job's time is controlled by an unavailable annex, that annex
  is a temporal dependency. A general date elsewhere cannot silently override it.
  A missing job table never creates a generic parsing/timing veto. Compute a
  supported primary rule and retain available positions regardless. The label
  “岗位表”, an unknown label, or missing acquisition alone establishes neither
  temporal delegation nor a timing exception. If delegation is explicit, only the
  affected missing temporal field/scope stays unknown; never discard the output.
- **Scoped exception:** a general cutoff plus an expressly exempt group, later
  batch, separately agreed specialist intake, or “until filled” subgroup cannot
  be flattened to universal expiry. Preserve the exception's target and relation;
  an unspecified exception stays unresolved. Do not confuse an age/qualification
  relaxation with a registration-time exemption.
- **Actual same-scope conflict:** conflicting dates, unclear replacement wording,
  shortened deadlines, or an unassociated extension/cancellation remain review.
  A later timestamp or similar title does not establish authority or precedence.
  Saved `host_period.conflicts` is empty on **183 / 183** selected records; that
  empty list is not an independent finding that their temporal semantics are
  conflict-free. C08/C14 expose why scope still matters.
- **Publication-relative/workday rule:** a literal publication anchor is required
  when a relative rule actually depends on it; capture time is not publication.
  A workday computation needs reviewed calendar coverage, not weekdays guessed
  locally. Neither calendar nor change-review tables exist in this replay's
  reported snapshot inventory. Do not invent those trusted inputs in the audit.
- **Opening date vs deadline:** a future cutoff alone means “deadline not passed”,
  not necessarily “applications have opened”. Unknown current hiring,
  unavailable routes and later changes stay visible for both past and future ends.

## 5. Verification and remaining limits

Offline verification performed by the sidecar:

- Loaded exactly **1,314** saved replay records and selected **183** by the stated
  predicate; retained all selected records in `sidecar_census.json`.
- Validated **183 / 183 bundle-body hashes** and **183 / 183 primary-body hashes**
  against their content-addressed names; checked bundle ID and page-version
  bindings. These are reference-integrity checks, not semantic correctness checks.
- Resolved **155 / 155** attachment anchors in saved raw primary HTML, one anchor
  for each saved attachment link, and preserved their non-contact purpose context
  in `sidecar_link_contexts.json`. No missing attachment contents were recovered.
- Validated **27 / 27 short excerpts** as literal substrings of their specified
  source blocks across **15** walkthroughs, with exact version/evidence IDs in
  `sidecar_cases.json`. C15's excerpt is a section identifier, explicitly not a
  nonexistent timing clause.
- Recomputed the census into `sidecar_counts.json` using the existing isolated
  frozen uv environment. No package installation or production evaluator run.

No production tests were added, deleted, modified, or run by this documentation
sidecar. The counterfactual invariant in §0 is a user-required acceptance boundary,
not a production test executed here. No post-redesign replay, semantic gold, model experiment, browser/device
verification, live source check, qualification determination, or deployment is
claimed. The counted population is one saved source and one snapshot/clock. The
15 interpretations need main-implementation regression checks and independent
review if promoted into acceptance criteria; they must not silently become
host-reviewed semantic cases or an accuracy denominator.

**Main handoff:** replace the blanket material-completeness veto with independently
concrete field/scope evidence, for **both available positions and dates**. Preserve
primary dates despite form failures (C01–C06), compute represented row dates
(C07–C08), and distinguish rolling, genuinely unsupported timing semantics and
missing-year limits (C09–C15) from nonblocking partial/truncated diagnostics.
Fix overlapping heading candidates and notification-purpose
pollution generically. Keep explicit delegation and genuine scope/conflict
abstention. Any later change in the 140 fixed-unknown outcomes must be measured
by a new frozen replay, not inferred from this census.
