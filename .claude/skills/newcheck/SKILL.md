---
name: newcheck
description: Scaffold a new zombiescan waste check end to end - the check module, a scrubbed API fixture, its test, the pricing entry, and registry wiring. Use when adding a new resource type to the zombie catalog.
---

Add a new waste check named `$ARGUMENTS` (e.g. `unused-nat-gateway`).

If no name was given, ask which resource type to check before doing anything.

## Steps

1. **Read `@PLAN.md`** and find the row for this check in the zombie catalog
   table. It gives the waste condition and the rough monthly cost basis. If the
   check is not in the table, add a row for it.

2. **Write the check module** at `src/zombiescan/checks/<name>.py`. Follow the
   shape of the existing modules in that directory — match their signature,
   return type, and registration decorator rather than inventing a new one.

   Constraints, no exceptions:
   - Describe/List/Get API calls only. Never a mutating call.
   - Paginate. Accounts that accumulate zombies have a lot of them.
   - Return findings with resource id, region, the reason it is waste, the
     estimated monthly cost, and a remediation command as a *string*.
   - Never execute the remediation command.

3. **Record a fixture** at `tests/fixtures/<name>.json` — the raw AWS API
   response shape the check consumes. Capture it from a real call if
   credentials are live, otherwise hand-write it to match the documented API
   shape. Keep it small: enough rows to cover the waste case, the healthy case,
   and one edge case.

4. **Write the test** at `tests/test_<name>.py`, driving the check off the
   fixture with no network access. Assert on which resources are flagged and on
   the computed cost, not just the count.

5. **Add the pricing entry** to the table under `src/zombiescan/pricing/`, with
   per-region values where the price varies by region. Verify the figure against
   the AWS Price List API rather than the placeholder in PLAN.md — those are
   explicitly marked unverified.

6. **Wire it into the registry** so `zombiescan scan` picks it up, and confirm
   it appears in `--help` output if checks are individually selectable.

7. **Run** `uv run pytest` and `uv run ruff check .`, then report the new
   check's output against the fixture.

Do not run a live scan as part of this skill — that is `/livesmoke`.
