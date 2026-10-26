# Workflow Expiry Debug

`server/scripts/debug-source-workflow.py` is an opt-in, offline diagnostic for an
accepted generated recipe. It does not author a new recipe, crawl a website,
perform a source-wide audit, write positions to a database, or activate a source.

## Run

Use the configured uv environment and Linux bubblewrap. The script executes the
actual recipe graph through xdog-flow; generated module import and execution go
through the real `Sandbox`. Only the detailed-model boundary is stubbed. No
provider credentials, live model call, or unsafe sandbox fallback is required.

From the repository root, choose a **previously unused** output directory:

```sh
uv run --frozen --directory server python scripts/debug-source-workflow.py \
  --author-report .artifacts/ustc-author-agent-20261011-fixed-ai/report.json \
  --now-iso 2026-10-11T15:07:21.330970+00:00 \
  --output .artifacts/ustc-expiry-rerun
```

The candidate defaults to `accepted-candidate.json` beside the author report;
`--candidate PATH` can select another artifact explicitly. Saved samples are read
from the report's `workspace/input/payload.json`. An explicit timezone is required
for the evaluation clock. Synthetic dates are derived from its Shanghai local day.

The script refuses to overwrite an output directory. Inputs are size-bounded,
read without modification, and hashed before and after execution. It writes at
most five control records, three saved-sample validity records, and one summary.
Each JSON report is limited to 2 MiB. Failure produces a failing report and exit
status, not an apparent success.

## What is verified

- Validity is the first actual flow node and the first generated script call.
- Expired controls execute `validity -> fast -> expired_result`, with **zero**
  detailed-model calls.
- Non-expired controls may use a validated fast script or the detailed branch,
  according to the actual source-owned graph. Observed references, node order and
  model-call counts must agree; any detailed calls are explicit stubs. The older
  reference recipe used `validity -> detailed -> current_result` for these cases.
- Final validity exactly matches the generated script result and the independent
  host check. No model determines or overwrites validity.
- A date-only deadline includes `23:59:59.999999` of its Shanghai local day and
  becomes expired at the next local midnight.

Node traces come from official `NodeStarted`/`NodeFinished` events forwarded by
`RecipeRunner.run(..., on_event=callback)`. Preview strings are not treated as
source evidence. Per-script results are observed after real sandbox execution.

## Recorded USTC verification

`server/.artifacts/ustc-expiry-20261011/report.json` records a successful run with
xdog-flow 2.2.0: all five synthetic controls passed, with no live model calls.
The accepted candidate and original input files retained their hashes.

The three **unaltered saved USTC bundles** (`sign=10010`, `12030`, `12060`) were
checked separately using the generated validity script in bubblewrap. All three
returned `unknown`, a null deadline, and `no_explicit_application_deadline`.
This is the script's finding, not proof that recruitment is open or expired.
Those runs did not perform detailed extraction. Dates in synthetic control files
must never be attributed to the USTC announcements.

This diagnostic supplies expiry-branch evidence only; it does not establish
classification coverage, baseline completeness, source activation or incremental
collection success.
