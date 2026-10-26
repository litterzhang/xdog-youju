# Job Field Visibility

Unknown business facts are omitted from the client presentation, not removed from
stored data or the API. This applies to the public job list/detail and the admin
incremental job snapshots. Unknown-value filter options and feedback snapshots
remain available for diagnosis.

## Presentation rules

- Do not render a business-field row or provenance badge for a null/unknown value.
  Defensive blank-string checks also avoid empty rows.
- Hide the entire business-information card when none of its fields are known.
- Omit list placeholders for missing unit, headcount, location, employment and
  deadline. Do not leave empty trait or timing containers.
- Preserve zero headcount, source facts and explicitly AI-inferred values with
  their existing provenance. Hiding unknown values must not mutate the job DTO.
- Keep expiry, parsing conflicts, incomplete-material notices and official-source
  information. Recruitment-condition uncertainty and AND/OR relationships are not
  filtered: missing optional facts must not conceal qualification restrictions.

`position-display.ts` owns the shared visibility predicate; `FactRow` applies it
centrally. The detail card and list/snapshot components avoid empty containers.
Feedback-specific unknown-value formatting is unchanged.

## Verification and delivery boundary

Type checking and all **135 client tests** passed, including three focused tests
executing the actual stateless TSX components and testing zero/inferred values,
empty sections, immutable input and preserved risk/condition displays.

H5 and WeChat builds use isolated output directories under
`server/.artifacts/job-field-visibility-20261026/`. H5 retains its existing webpack
entrypoint-size warning. Build verification does not publish H5 or upload WeChat;
no server restart or backend reprocessing change is part of this UI adjustment.
Native-device behavior is not verified by these checks.
