---
description: Scan this AWS account for unused resources and report what they cost.
argument-hint: "[region ...] or --all-regions"
---

Scan the AWS account these credentials belong to and report the waste.

Regions requested: $ARGUMENTS (empty means scan every enabled region).

1. Call `scan_account` on the `zombiescan` MCP server. Pass `regions` if any
   were named above, otherwise `all_regions: true`.
2. Report, in this order:
   - the monthly total and the annual figure beside it
   - the checks behind most of it, from `by_check`
   - the costliest few resources, with their region
   - anything under `errors`, and the `warning` if the scan was incomplete
3. Say that the figures are list-price estimates rather than the account's
   bill, and that nothing was changed.
4. Give the `report_path` so follow-up questions can use it.

Answer follow-ups with `estimate_savings` against that report rather than
scanning again or adding the findings up yourself.
