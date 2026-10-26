# Condition Group Frame Refinement — October 26, 2026

## Visual scope

The condition card keeps its existing position after business information. Its
root logic group no longer draws a second complete frame inside the card. The
root AND/OR badge aligns with the card heading at the upper right. Nested groups
use one thin neutral outline, a light-gray surface and a rounded operator badge
inset into the upper edge, rather than a separate label row consuming space.

Root condition values align with the business-information columns. Nested rows
retain an appropriate indent and their independent provenance badges. Row spacing
and separators preserve reading rhythm without thick borders or additional cards.
Deep groups keep the compact layout. Unknown logic still has no invented group.

Only presentation markup and SCSS change. Conditions, grouping logic, AND/OR
meaning, provenance, filters, API contracts, scripts and stored facts are unchanged.

## Verification boundary

Type checking and the existing 132 client tests cover the root modifier, floating
operator label and unchanged grouping rules. An isolated staged H5/browser run
checks mixed AND/OR, unknown relations, filter transactions, scrolling and a 320px
viewport using synthetic DTOs. Native WeChat device behavior is not established
by these checks. Builds do not replace the served H5; no server restart or paid
model task is needed. Receipts reside in
`server/.artifacts/condition-frame-20261026/`.

## Delivery

Official Taro upload **0.1.17** succeeded at **12:53:31 Asia/Shanghai** on October
26, 2026 (572.7021484375 KB). All 132 client tests and typecheck passed; staged H5
build, browser checks and upload/weapp build passed. Existing H5 size warnings
remain. Live H5 file hashes and the three server process IDs are unchanged.
This is a development upload, not review submission, release or device testing.
