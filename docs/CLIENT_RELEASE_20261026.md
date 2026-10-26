# Client Release — October 26, 2026

## Delivered behavior

Unknown business-field rows, their provenance badges, empty business-information
cards and list placeholders are omitted. Recognized zero counts and AI-inferred
facts remain visible. Qualification logic, expiry, conflicts, material warnings,
feedback snapshots and unknown-value filters are unchanged. See
`JOB_FIELD_VISIBILITY.md` for the display-only boundary.

## WeChat upload

The official Taro mini-ci command uploaded development version **0.1.18** at
**15:13:22 Asia/Shanghai**, reporting **572.9296875 KB**. The build retains the public
HTTPS API origin, native page navigation and URL checks. It contains no server
credentials or code-upload key.

This is a code upload, not a review submission or production mini-program release.
Experience-version selection and WeChat-device behavior are not verified. The
console may require explicitly selecting this upload as the experience version.

## H5 publication and ingress limitation

The production H5 build was published with an atomic directory exchange, scoped
SELinux labels and a retained rollback tree. No Nginx configuration/reload or
backend restart was needed; API, source-worker and Recipe PIDs stayed unchanged.

Actual public verification used **youjuh5-h.942295.xyz**: HTML plus 98 JavaScript,
CSS and image assets matched the published files. A read-only system-Chromium
smoke against the real public API verified the list and detail page, no missing
business-value placeholders, and six recognized detail business rows for the
selected real job. No login, feedback, permissions or user data were modified.

**youjuh5.942295.xyz still returns the outer Nginx welcome page.** It is not counted
as a successful application ingress. The existing working `-h` ingress serves the
new release; repairing the outer bare-host route remains a separate infrastructure
operation. TLS validation was not disabled to make these checks pass.

## Verification and source-control hygiene

Type checking and all **135 client tests** passed. Both official builds succeeded;
H5 retains the webpack entrypoint-size warning. Focused actual-TSX regressions
preserve zero/inferred facts and ensure unknown rows do not mutate API facts.

The initial Git snapshot excludes private environment files, signing keys,
databases/snapshots, captures, verification logs, caches, dependencies and build
outputs. Pattern scanning and exact comparison against current project secret
settings found no actual credentials in the candidate source or built clients.
Six credential-shaped URL strings are deliberate unsafe-URL rejection fixtures
using example/test/invalid hosts, not real credentials; their tests are preserved.
This bounded scan is not a claim that all security risks have been audited.

Receipts, upload log/QR, public asset checks, screenshots and the old H5 tree are
private local files under `server/.artifacts/release-20261026-151234/`, not Git
content. The client release does not restart or take ownership of the ongoing
source re-extraction task.
