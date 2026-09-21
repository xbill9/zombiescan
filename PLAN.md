# zombiescan — build plan

**Scope decision (2026-09-20):** local only. No hosted site, no publish endpoint.
The scan runs on the local machine against the local AWS account using AWS CLI
credentials. Everything below reflects that.

## What it is

An open-source, local-first AWS waste scanner. It runs on your machine with your
existing AWS CLI credentials, finds resources you are paying for that nothing is
using, prices them, and prints a report with a dollar total.

Positioning: not "here are your 14 EBS volumes" but "9 of these are attached to
nothing and cost you $47/month; here is the plan to kill them."

Two front doors, one engine:

1. **CLI** — `zombiescan scan --profile prod --all-regions`
2. **Claude Code plugin** — a skill plus an MCP server, so the agent can run a
   scan, explain a finding, and draft the cleanup itself

Nothing leaves the machine. No cross-account role, no credential handover, no
findings uploaded anywhere. That is the whole privacy story and it is a real
advantage over every SaaS tool in this space.

## Hackathon status

This shape does **not** satisfy the Zero to Shipped ship gate, which requires a
live application on AWS reachable by a public URL and is pass/fail with no
exceptions. A local CLI cannot pass it.

That is a deliberate choice, not an oversight. The weekend challenge entry
(comment + complete Builder profile) is already placed and qualifying. If the
Zero to Shipped submission is wanted later, the missing piece is additive: a
`--publish` flag plus a small API + DynamoDB + CloudFront report viewer, roughly
a day of work on top of a finished CLI. The CLI is built so that bolting it on
later does not require rework — the scan engine already emits a clean JSON
findings document.

## The zombie catalog

Each check returns: resource id, region, why it is considered waste, estimated
monthly cost, and a suggested remediation command. **v1 has no delete capability
at all** — it prints commands, it never runs them. That is a feature, and it is
the honest answer to "would you let this thing loose on production?"

| Check | Why it's waste | Rough monthly cost |
| --- | --- | --- |
| ✅ Unattached EBS volumes | Billed in full while attached to nothing | ~$0.08/GB (gp3) |
| ✅ Unassociated Elastic IPs | All public IPv4 is billed hourly now | ~$3.65 each |
| ✅ Idle NAT Gateways | Hourly charge in VPCs with nothing running | ~$32 each |
| ✅ Stopped EC2 instances | Instance is free, its disks are not | disk cost |
| ✅ Orphaned EBS snapshots | Source volume and AMI both gone | ~$0.05/GB |
| ✅ Available ENIs | Left behind by deleted Lambdas and instances | $0 (hygiene) |
| ✅ Idle load balancers | ALB/NLB with no healthy targets | ~$16–22 each |
| ✅ Unused AMIs | Plus the snapshots behind them | snapshot cost |
| ✅ Stopped RDS instances | Stopped, but storage still bills | storage cost |
| ✅ Log groups with no retention | Grows forever, nobody notices | ~$0.03/GB |
| ✅ Empty VPCs | Flags the NAT/IGW/endpoint waste inside them | varies |
| ✅ Unused security groups | Referenced by nothing | $0 (hygiene) |

EBS, snapshot, and NAT figures are now generated from the live Price List API by
`src/zombiescan/pricing/refresh.py`. Public IPv4 is a documented constant: the
API does not expose it (the EC2 'IP Address' family is Wavelength CarrierIP
only). Remaining figures are placeholders to be verified against the API
(`pricing:GetProducts`) during the build. Ship with a bundled per-region price
table plus a refresh script — faster, works offline, and avoids adding a network
failure mode to every scan.

**IAM posture:** read-only. Describe and List calls only. Ship a minimal policy
document in the repo so users can see exactly what it needs.

## Credentials (verified 2026-09-20)

The local machine authenticates with `aws login`, not static keys. There is no
`~/.aws/credentials` file; the session lives in `~/.aws/login/cache/` and the
CLI's `~/.aws/cli/cache/session.db`, and it expires on a timer.

**boto3 reads this natively — but only with the CRT extra installed.**

Verified behaviour:

- `pip install boto3` alone → `MissingDependencyException: Using the login
  credential provider requires an additional dependency. You will need to
  pip install "botocore[crt]" before proceeding.`
- `pip install "botocore[crt]"` (pulls `awscrt`) → `Session().get_credentials()`
  reports `method == "login"` and STS calls succeed.

Consequences for the build:

- **Pin `botocore[crt]` as a hard dependency**, not an extra. Without it the tool
  fails at import-time for anyone using `aws login`, which is the flow the
  hackathon is pushing people toward. This is the single most likely install-time
  bug report the project would get.
- No need to shell out to `aws configure export-credentials`, and no need for
  `save-aws-creds.sh`. Plain `boto3.Session()` is enough.
- Surface a clear error when the session has expired, naming `aws login` as the
  fix. Expiry mid-scan is the common case, since sessions are short.

**Testing caveat:** the current principal is `arn:aws:iam::106059658660:root`.
These are temporary session credentials rather than stored root keys, so this is
not a leaked-secret problem. But IAM policies do not constrain the root user, so
the minimal read-only policy this tool ships cannot be validated while signed in
as root. Create a scoped IAM user or role before claiming least privilege in the
README.

## Architecture

```
your laptop
-----------
aws login session (~/.aws/login/cache) | --profile | SSO | env vars
        |  read by boto3 via the "login" provider, requires botocore[crt]
        v
zombiescan engine (python, boto3)
  multi-region, parallel, read-only
  checks/  one module per zombie type
  pricing/ bundled per-region price table
        |
        +--> terminal report (rich table, dollar total)
        +--> findings.json
        +--> remediation.sh  (printed, never executed)
        |
        v
Claude Code plugin
  skill + slash command + MCP server
  tools: scan_account, explain_finding, estimate_savings
```

**Repo layout**

```
zombiescan/
  src/zombiescan/
    packs/        pack manifest, discovery, API version
      core/         one module per zombie check, + cleaners.py
      lightsail/    checks, cleaners, rates.py, refresh.py
    pricing/      bundled price table, rate registry, refresh script
    building.py   simple_check, for checks that are one call and one filter
    helpers.py    shared describe helpers, public to packs
    engine.py     region fan-out, credential handling
    report.py     terminal table, JSON, remediation script
    cli.py        click entry point
  docs/PACKS.md   the pack-author contract
  plugin/         Claude Code plugin: skill, slash command, MCP server
  policy/         minimal read-only IAM policy
  tests/          check logic against recorded API fixtures
```

**Packs (2026-09-21).** Checks are grouped into installable packs. A pack owns
its checks, cleaners, price rates and rate fetchers; third-party packs are
discovered through a `zombiescan.packs` entry point and declared compatible by
`PACK_API_VERSION`. Lightsail was extracted from the core checks as the proof
the seam carries a whole service — its own API, its own pricing source, its own
744-hour month. The JSON report records loaded packs under `packs`
(`schema_version` 2).

The trade-off, recorded deliberately: a pack is code that runs with the local
AWS credentials, and nothing verifies that its checks only read. The read-only
guarantee holds for the packs in this repository because they are reviewed. A
botocore `before-call` allowlist would make it structural; it was considered
and deferred.

**Distribution:** MIT on GitHub, installable with `pipx install zombiescan`, plus
the Claude Code plugin in the same repo.

## Build order

1. Environment: `botocore[crt]` pinned, fix `uvx`/`aws-mcp`, repo skeleton
2. Engine: region fan-out, credential handling, findings data model
3. First five checks — unattached EBS, unassociated EIPs, orphaned snapshots,
   available ENIs, stopped instances. These carry the story on their own.
4. Pricing table and cost attribution. The dollar total works end to end.
5. Terminal report, JSON output, remediation script generation
6. Remaining checks: NAT, ALB/NLB, RDS, AMIs, log groups, VPCs, SGs
7. Tests against recorded fixtures
8. Claude Code plugin: skill, slash command, MCP server
9. README, read-only policy doc, packaging

## Beyond the original catalog

Shipped past the twelve rows above: `unused-vpc-endpoint`, `empty-classic-lb`,
`stopped-rds-instance`, `disabled-kms-key`, `stale-secret`, `unmounted-efs`,
`detached-internet-gateway`, `incomplete-multipart-upload`,
`orphaned-rds-snapshot`, `idle-provisioned-dynamodb`,
`unused-route53-health-check`, `ecr-stale-images`, and six Lightsail checks:
`lightsail-stopped-instance`, `lightsail-unattached-static-ip`,
`lightsail-unattached-disk`, `lightsail-idle-container-service`,
`lightsail-empty-load-balancer`, `lightsail-orphaned-snapshot`.

`ecr-stale-images` reports repositories nothing has pushed to in 90 days, at
the ECR storage rate ($0.10/GB-month, flat across all 36 regions the Price List
API returns under `AmazonECR` / `EC2 Container Registry` / `GB-Mo`). Its cost is
an explicit upper bound: ECR bills for unique layers, and images sharing a base
layer are counted once each. Added after a manual cross-check of a live account
found 71 GB across 37 repositories that no check could see.

Lightsail was the other blind spot that cross-check found, and it is a whole
service the scanner made no call to. The headline trap is
`lightsail-stopped-instance`: unlike EC2, a *stopped* Lightsail instance bills
its full bundle, so "stop it to save money" saves nothing.

Lightsail prices come from **Lightsail's own API**, not the Price List API, and
`refresh.py` says why at the fetcher. `GetBundles` and
`GetContainerServicePowers` return a monthly price keyed by the exact
`bundleId` / power name that the describe calls report, while the Price List
carries the same rates keyed by a usagetype string (`USE1-BundleUsage:1GB`)
that would have to be mapped back to a bundle by guesswork -- and a wrong
mapping silently prices the wrong machine. Lightsail also bills containers over
a 744-hour month, so multiplying its hourly rate by the table's 730 understates
every one of them. Disk, static IP, load balancer and snapshot rates do come
from the Price List, bucketed by the `group` attribute because Lightsail
products leave `productFamily` null.

The engine now supports global checks (`scope="global"`), which run once per
scan instead of once per region. Route 53 was the first; CloudFront, IAM and
account-level S3 settings can use it next.

**The Claude Code plugin (2026-09-21).** Built as the second front door, in
`plugin/`: two slash commands, a skill, and an MCP server
(`src/zombiescan/mcp_server.py`, console script `zombiescan-mcp`) with five
tools — `list_checks`, `scan_account`, `estimate_savings`, `explain_finding`
and `plan_cleanup`. `.claude-plugin/marketplace.json` at the repo root makes it
installable with `/plugin marketplace add xbill9/zombiescan`.

Two decisions worth keeping:

- **The server is read-only, and the suite holds it to that.** `plan_cleanup`
  reaches `clean.plan_for` and nothing else; `tests/test_mcp_server.py` asserts
  the module names no other `clean.*` attribute, so adding a tool that applies
  a plan fails the suite rather than shipping. Applying stays `zombiescan clean
  --apply` at a terminal, which is where the per-resource prompt and the
  irreversible warning live.
- **The tools compute the arithmetic.** `estimate_savings` takes a filter and
  returns exact totals, counts, minima, maxima and per-check, per-region and
  per-type breakdowns, and echoes the filter it applied — a filter naming a
  check the report does not contain returns a precise zero that reads like an
  all-clear, and `no_such_checks_in_report` is what makes that visible.

The protocol (JSON-RPC 2.0 over stdio) is implemented against the standard
library, so an install of zombiescan does not carry an MCP SDK it would have to
keep current.

Wanted but not built:

- **Transit gateway attachments** (~$36/month each). Price List API does not
  expose transit gateway pricing under any service code tried; building it
  would mean a second hardcoded constant, so it is on hold.
- **Metric-driven checks** -- idle RDS by connection count, idle provisioned
  DynamoDB, KMS keys unused per CloudTrail. Every check so far answers from a
  single describe call; these need CloudWatch and a lookback window, which is a
  new capability rather than another row.

## Open questions

- Cost lookback for "idle" judgments (RDS connections, ALB targets) needs
  CloudWatch metrics; decide the default window (7 days is the usual answer)
- Whether `--all-regions` defaults on or off. Off is faster; on is what people
  actually want, because the forgotten resources are always in a region nobody
  looks at.
