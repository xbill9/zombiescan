---
name: newcheck
description: Scaffold a new zombiescan waste check end to end - the check module, a scrubbed API fixture, its test, the pricing rate and fetcher, and its cleaner or refusal. Use when adding a new resource type to the zombie catalog.
---

Add a new waste check named `$ARGUMENTS` (e.g. `unused-nat-gateway`).

If no name was given, ask which resource type to check before doing anything.

## Steps

1. **Read `@PLAN.md`** and find the row for this check in the zombie catalog
   table. It gives the waste condition and the rough monthly cost basis. If the
   check is not in the table, add a row for it.

2. **Pick the pack.** Checks live in `src/zombiescan/packs/<pack>/`. Use
   `core` unless the resource belongs to a service that already has its own
   pack (Lightsail), or is a whole service nothing currently scans — in which
   case read `@docs/PACKS.md` and start a pack for it. There is no import list
   to update: a module in a pack directory is discovered by existing.

3. **Write the check module** at `src/zombiescan/packs/<pack>/<name>.py`.
   Follow the shape of the existing modules in that directory — match their
   signature, return type, and registration decorator rather than inventing a
   new one. If the check is genuinely one describe call plus one filter, build
   it with `simple_check` from `zombiescan.building`; see
   `detached_internet_gateway.py` for an example. Anything needing a second API
   call, a sum over sub-resources, or a judgement a predicate cannot express
   stays a plain `@check` function.

   Constraints, no exceptions:
   - Describe/List/Get API calls only. Never a mutating call.
   - Paginate. Accounts that accumulate zombies have a lot of them.
   - Return findings with resource id, region, the reason it is waste, the
     estimated monthly cost, and a remediation command as a *string*.
   - Never execute the remediation command.
   - Pass `scope="global"` for account-wide resources with no region of their
     own, or the resource is reported once per region.

4. **Record a fixture** at `tests/fixtures/<name>.json` — the raw AWS API
   response shape the check consumes. Capture it from a real call if
   credentials are live, otherwise hand-write it to match the documented API
   shape. Keep it small: enough rows to cover the waste case, the healthy case,
   and one edge case.

5. **Write the test** at `tests/test_<name>.py`, driving the check off the
   fixture with the `make_context` fixture and no network access. Assert on
   which resources are flagged and on the computed cost, not just the count.

6. **Add the rate and its fetcher.** Register a `RateSpec` (in the pack's
   `rates.py`, or `zombiescan/pricing/rates.py` for core) and price the finding
   through `ctx.pricing.rate(...)`. Then register a `@price_fetcher` for the
   table section so `python -m zombiescan.pricing.refresh` can rebuild it —
   `tests/test_packs.py` asserts every section has exactly one fetcher, so a
   missing one fails the suite. Verify the figure against the AWS Price List
   API rather than the placeholder in PLAN.md; those are explicitly unverified.

7. **Give it a cleaner or a refusal.** Either add a planner to the pack's
   `cleaners.py` (back up before destroying, mark `irreversible=True` where
   there is no recovery window) or pass `uncleanable="<why not>"` to `@check`.
   A check with neither fails `test_clean.py`.

8. **Run** `python3 -m pytest` and `python3 -m ruff check src tests`, then
   report the new check's output against the fixture.

Do not run a live scan as part of this skill — that is `/livesmoke`.
