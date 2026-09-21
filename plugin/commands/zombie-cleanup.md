---
description: Show exactly what cleaning up a zombiescan report would do, without changing anything.
argument-hint: "<report path> [check ...]"
---

Plan the cleanup of a zombiescan report. Change nothing.

Arguments: $ARGUMENTS — the first is the report path from a scan, any others
are check names to narrow it to.

1. Call `plan_cleanup` on the `zombiescan` MCP server with that `report_path`,
   and `checks` if any were named.
2. Show the plan grouped by resource: the calls in order, with the backup step
   first where there is one.
3. **Name every irreversible step explicitly**, and give the count. A step
   marked `irreversible` has no snapshot, no recovery window and no undo.
4. List anything under `unsupported` with the reason zombiescan refuses to
   clean it. Do not improvise a delete command for those.
5. Finish with the command for the operator to run themselves, and do not run
   it:

   ```
   zombiescan clean --from <report path> --apply
   ```

   Mention that without `--apply` it is a dry run, that it prompts per resource
   unless `--yes`, and that it refuses a report from a different account.
