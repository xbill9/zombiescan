---
name: livesmoke
description: Run the opt-in end-to-end zombiescan smoke test against the real AWS account, after verifying the aws login session is still valid.
disable-model-invocation: true
---

Run the live end-to-end scan against the real AWS account.

This hits real AWS APIs and costs money. It is gated behind `ZOMBIESCAN_LIVE=1`
and excluded from the default test run for that reason.

## Steps

1. **Check the session before anything else.** `aws login` sessions are
   short-lived and expiring mid-scan is the normal failure here:

   ```
   aws configure export-credentials | python3 -c 'import json,sys; d=json.load(sys.stdin); print("expires:", d.get("Expiration","none"))'
   ```

   Compare against the current UTC time. If it has expired or has under five
   minutes left, stop and tell the user to run `aws login` — do not start a scan
   that will die partway through.

   Never print the credential values themselves, only the expiration.

2. **Confirm the account** with `aws sts get-caller-identity` and show the user
   which account and principal is about to be scanned. If the principal is the
   account root user, note that the least-privilege policy is not being
   exercised by this run.

3. **Run** `ZOMBIESCAN_LIVE=1 uv run pytest -m live`.

4. **Report** the findings count, the monthly waste total, and any check that
   errored. An empty result is a valid outcome, not a failure — say so plainly
   rather than hunting for something to report.

5. If a check raised on a real API shape that the fixtures do not cover, capture
   that shape as a new fixture case so the offline suite catches it next time.

Read-only throughout. Never run the generated remediation script.
