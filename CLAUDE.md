# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`zombiescan` — an open-source CLI that scans an AWS account for resources nobody
is using (unattached EBS volumes, unassociated Elastic IPs, idle NAT gateways,
orphaned snapshots), prices them, and reports the monthly waste total. Ships with
a Claude Code plugin (skill + MCP server) wrapping the same engine.

Check catalog, architecture, and build order: @PLAN.md

## Scope: local only

There is **no hosted component**. No web frontend, no API, no Lambda, no
cross-account IAM role, no publish endpoint. The scan runs on the local machine
against the local AWS account and writes to the terminal and local files.

Do not propose a hosted or deployed piece unless asked. This was decided
deliberately and reversed twice; the tradeoff (it cannot pass the Zero to Shipped
ship gate) is recorded in PLAN.md and is understood.

## Safety: read-only, always

The tool makes **Describe/List/Get calls only**. It must never call Delete,
Terminate, Release, Deregister, Modify, or any other mutating AWS API.

Remediation output is generated as text — a shell script the user can read and
run themselves. Never execute it, and never add a flag that does.

## Credentials

Authentication is via `aws login` (not static keys, no `~/.aws/credentials`).
boto3 reads these sessions through its `login` credential provider, which
**requires `botocore[crt]`** — pin it as a hard dependency, not an optional
extra. Without it boto3 raises `MissingDependencyException` at credential load.

`aws login` issues short (~15 minute) sessions, but the credential provider
renews them automatically — an expiration timestamp in the near future is normal
and does not mean re-authentication is needed. Renewal still fails once the
underlying login lapses, so catch credential errors and name `aws login` as the
fix rather than reporting them as a scan failure.

The dev account authenticates as the account root user. IAM policies do not
constrain root, so the shipped least-privilege policy cannot be verified with it
— use a scoped IAM principal before claiming least privilege in docs.

## Commands

```
uv sync                                    # install deps
uv run zombiescan scan --all-regions       # run the CLI
uv run pytest                              # tests (fixtures, offline)
ZOMBIESCAN_LIVE=1 uv run pytest -m live    # opt-in live smoke test, real account
uv run ruff format . && uv run ruff check --fix .
```

## Testing

Check logic is tested against committed JSON fixtures of real AWS API responses,
so the suite runs offline with no credentials. One end-to-end smoke test hits a
real account and is gated behind `ZOMBIESCAN_LIVE=1` — it is excluded by default
because it costs money and needs live credentials.

When adding a check, add its fixture and test in the same change.
