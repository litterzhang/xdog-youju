# Mini-program Filter Sheet and Condition Groups

## Filter interaction

The previous inline selectors/chip wall are replaced by three lightweight entries:
deadline, education and all filters. Each opens a Taro View/ScrollView bottom sheet
with single-select option blocks, local reset and a fixed confirmation footer.
This is an application sheet, not an OS-native ActionSheet. Native page titles and
navigation remain unchanged. The tab bar is hidden through Taro while the sheet is
mounted and restored on dismissal/unmount, including late completion of hiding.

Pending selections never change the list. Closing or tapping the backdrop discards
them; confirmation applies one query and resets transport pagination. Reset changes
only pending facets. The committed search and page size are preserved. Selected
facet count appears in the entry, without expanding the page with form fields or
chips. Existing result totals, infinite scrolling and native refresh remain.

## Detail and condition relationships

Business information precedes the standalone recruitment-condition card. Conditions
retain their names, values and separate provenance badges. A declared logic group
has a border and a top-right `AND` or `OR` badge; mixed operators produce nested
groups. There is no prose expression of the relation or condition-number references.

The presentation reads only `condition_logic`. It does not group by resemblance,
infer missing logic, alter qualification facts or imply applicant eligibility.
Associative same-operator wrappers can flatten without changing the relation.
Unknown/incomplete relationships display the complete flat condition list with an
explicit unresolved-combination tag, never an assumed AND group.

## Verification boundary

Client type checking, unit tests, staged H5 build, browser interactions and official
Taro WeChat upload are separate receipts. Browser checks exercise actual option
clicks, cancellation, one-query confirmation, uncommitted reset, search preservation,
320px layout, 20→40→45 scrolling, business-info order, mixed group badges and unknown
logic. Verification uses synthetic DTOs with a saved public DTO as their shape
fixture; it is not source accuracy or WeChat device verification.

Artifacts are under `server/.artifacts/jobs-filter-sheet-20261026/`. The live H5
root and server services are not modified/restarted by this client-only change.

## Delivery

Official Taro upload **0.1.16** succeeded on October 26, 2026 at **11:45:04
Asia/Shanghai** (571.9150390625 KB). Client tests: **132 passed**; typecheck,
staged H5 build, browser checks and upload/weapp compilation passed. The three
server process IDs and the served H5 manifest were unchanged. This is a development
upload, not review submission, public release or native-device verification.
