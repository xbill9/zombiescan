---
title: "Find the AWS Resources Nobody Is Using, and What They Cost You"
published: false
description: "A local-first CLI that scans an AWS account for unused resources, prices each one from the AWS Price List API, and generates or performs the cleanup. Read-only by default, with every price fetched from the live API and dated in each report."
tags: aws, python, devops, opensource
cover_image: https://raw.githubusercontent.com/xbill9/zombiescan/main/articles/zombiescan/devto-cover.e89ceeb8.jpg
---

This article provides a step by step guide to auditing an AWS account for resources nothing is using, pricing each finding from the AWS Price List API, and cleaning up the ones you choose. A suite of Python checks is built to cover storage, compute, networking, data services and the account-wide services that sit outside any region.

https://github.com/xbill9/zombiescan

---

#### What Gets Left Behind

A volume survives the instance it was attached to. An Elastic IP outlives the migration that freed it. A NAT gateway keeps running in a VPC whose workload was torn down last year. Each one bills every hour and reports nothing.

Cost Explorer shows the total. Trusted Advisor lists candidates. The figure that drives a decision is per-resource: this volume is attached to nothing and costs $50 a month.

`zombiescan` produces that figure for 22 classes of resource.

---

#### At This Point You Should Have…

- An AWS account and credentials the CLI can already use, from `aws login`, SSO, a profile or environment variables
- `uv` on the path
- Read access to EC2, RDS, ELB, CloudWatch Logs, KMS, EFS, Secrets Manager, S3, DynamoDB and Route 53 — the managed `ReadOnlyAccess` policy covers all of it

---

#### Step 1 — Install

```
uv tool install git+https://github.com/xbill9/zombiescan
```

`botocore[crt]` is a hard dependency. boto3 reads `aws login` sessions through its `login` credential provider, and that provider raises `MissingDependencyException` at credential load without the CRT extra installed.

---

#### Step 2 — List the Checks

```
zombiescan checks
```

```
  available-eni  Unattached network interfaces
  detached-internet-gateway  Internet gateways attached to no VPC
  disabled-kms-key  Disabled KMS keys still being billed
  empty-classic-lb  Classic load balancers with no instances
  empty-vpc  VPCs with nothing in them
  idle-load-balancer  Load balancers with no registered targets
  idle-nat-gateway  Idle NAT gateways
  idle-provisioned-dynamodb  Empty DynamoDB tables on provisioned billing
  incomplete-multipart-upload  S3 buckets holding incomplete multipart uploads
  log-group-no-retention  Log groups with no retention policy
  orphaned-rds-snapshot  Manual RDS snapshots of deleted databases
  orphaned-snapshot  Orphaned EBS snapshots
  stale-secret  Secrets nothing has read in 90 days
  stopped-instance  Stopped instances still billing for storage
  stopped-rds-instance  Stopped RDS instances still billing for storage
  unassociated-eip  Unassociated Elastic IPs
  unattached-ebs  Unattached EBS volumes
  unmounted-efs  EFS file systems with no mount targets
  unused-ami  AMIs no instance uses
  unused-route53-health-check  Route 53 health checks no record uses
  unused-security-group  Security groups attached to nothing
  unused-vpc-endpoint  Interface VPC endpoints with no workloads
```

Seventeen of these carry a monthly charge. Five cost nothing and are reported because they accumulate without limit and block deletions: unattached network interfaces, unused security groups, empty VPCs, detached internet gateways, and log groups with no retention policy.

---

#### Step 3 — Scan the Default Region

```
zombiescan scan
```

Every call is Describe, List or Get. The scan has no code path that deletes, terminates, modifies or releases anything.

---

#### Step 4 — Scan Every Enabled Region

```
zombiescan scan --all-regions
```

```
Scanning as arn:aws:iam::106059658660:root
17 region(s), 22 check(s) — read-only

zombiescan — 86 findings across 17 regions

Check                   Found  Monthly
log-group-no-retention     67    $0.00
empty-vpc                   1    $0.00
unused-security-group      18    $0.00
```

`--all-regions` asks EC2 which regions the account has enabled, so opted-out regions cost no calls. A 22-check sweep of 17 regions runs in about 20 seconds against a small account.

Global services run once per scan. Route 53 has no regions, and a check that ran in all seventeen would report one health check seventeen times and multiply the total to match.

---

#### Step 5 — Read the Per-Resource Table

The detail table shows the costliest findings first, and guarantees a row to every check that fired.

```
┏━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┓
┃ Region    ┃ Resource                      ┃  Monthly ┃
┡━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━┩
│ us-east-1 │ /ecs/adk-fargate-task         │   ~$0.00 │
│ us-east-1 │ vpc-0b52b7e6ebc50ba38         │    $0.00 │
│ us-east-1 │ sg-00122a49220e4ef1f          │    $0.00 │
└───────────┴───────────────────────────────┴──────────┘
```

A `~` marks an estimate or an upper bound. Three conditions set it: the region had no price entry and the figure fell back to us-east-1, the resource type was unrecognised, or the resource bills on what it stores instead of what it provisions. Each finding carries a `note` in JSON output naming which.

🔎 Tip: `--min-cost 5` hides findings under five dollars a month, and `--limit 0` prints every row instead of the top 25.

---

#### Step 6 — Where the Prices Come From

The bundled price table is generated from the AWS Price List API.

```
uv run python -m zombiescan.pricing.refresh
```

```
fetching EBS volume prices...
  106 regions
fetching snapshot prices...
  40 regions
fetching NAT gateway prices...
  38 regions
```

Shipping the table with the package keeps a scan offline and removes `pricing:GetProducts` from the permissions a scan needs. Every report records the date the table was generated, so a reader can tell which rates produced the figures.

---

#### Step 7 — Why the Table Is Per-Region

Region changes the answer enough to change a decision.

| Region | gp3 per GB-month | NAT gateway per month | Interface VPC endpoint per month |
|---|---|---|---|
| us-east-1 | $0.080 | $32.85 | $7.30 |
| eu-west-1 | $0.088 | $35.04 | $8.03 |
| ap-southeast-2 | $0.096 | $43.07 | $9.49 |
| sa-east-1 | $0.152 | $67.89 | $15.33 |

A single flat rate halves a São Paulo NAT gateway and understates gp3 there by 90%.

---

#### Step 8 — Reading Tiered Price Entries

A Price List entry can carry several price dimensions. S3 storage lists every volume tier inside one entry, and DynamoDB provisioned capacity lists a `$0.00` free-allowance dimension beside the billed rate.

Selecting the highest positive rate for the requested unit gives the rate an account pays before tiers and allowances apply:

```python
rates = [
    float(usd)
    for term in entry.get("terms", {}).get("OnDemand", {}).values()
    for dim in term.get("priceDimensions", {}).values()
    if dim.get("unit") == unit and (usd := dim.get("pricePerUnit", {}).get("USD")) is not None
]
positive = [r for r in rates if r > 0]
return max(positive) if positive else None
```

Filtering on `unit` keeps read capacity and write capacity apart, since both live under one product family with different unit names.

---

#### Step 9 — JSON Output

```
zombiescan scan --all-regions --json findings.json
```

The document is a versioned contract with a published JSON Schema, and the test suite validates generated output against it.

```json
{
  "schema_version": 1,
  "tool": { "name": "zombiescan", "version": "0.1.0" },
  "scan": {
    "duration_seconds": 17.97,
    "account_id": "106059658660",
    "pairs_attempted": 358,
    "complete": true
  },
  "pricing": {
    "generated": "2026-09-21T01:16:11Z",
    "basis": "AWS Price List API, on-demand USD list prices",
    "excludes": ["savings plans", "reserved capacity", "private pricing", "credits"]
  }
}
```

Two fields carry more weight than their size suggests. `pricing.generated` dates the rates behind every figure. `scan.complete` reads false when every region/check pair failed, which lets a consumer tell an empty result from an unreadable account.

🔎 Tip: check `schema_version` before parsing. A consumer that cannot tell which version it holds breaks without saying so.

---

#### Step 10 — The HTML Report

```
zombiescan scan --all-regions --html report.html
```

One file, with no stylesheet, font, script or network request in it. Cost reports get emailed, attached to tickets and opened on laptops with no connectivity, and a self-contained file renders the same in all three. A print stylesheet covers PDF through browser print, which keeps a rendering engine out of the dependency list.

---

#### Step 11 — The Cleanup Plan

```
zombiescan scan --all-regions --script cleanup.sh
```

```bash
#!/usr/bin/env bash
# zombiescan cleanup plan — generated 2026-09-21T00:55:51Z
#
# READ EVERY LINE BEFORE RUNNING THIS.
# zombiescan generated this file and did not run it. Deleting AWS
# resources is not reversible. Where a backup is possible the command
# takes one first, but a backup is not a substitute for knowing what
# you are deleting.

set -euo pipefail
```

Every value interpolated into a command is shell-quoted, so a resource name arrives as one argument to the flag it belongs to.

---

#### Step 12 — Cleaning Up

`clean` is a separate command, reachable only by typing it.

```
zombiescan clean
```

Dry run is the default and prints the calls it would make:

```
dry run — 15 of 15 finding(s) can be cleaned

unused-security-group  sg-00122a49220e4ef1f  us-east-1 · $0.00/mo
    → delete security group sg-00122a49220e4ef1f
      ec2.delete_security_group({'GroupId': 'sg-00122a49220e4ef1f'})
```

```
zombiescan clean --apply --audit audit.json
```

`--apply` gates one thing: whether a planned step is sent to AWS. The set of planned steps is identical in both modes, which makes the dry run an exact preview.

Backups precede destruction, and the order is enforced. Volumes are snapshotted before deletion, instances imaged before termination, databases given a final snapshot, secrets deleted with a 30-day recovery window. A failed step ends that finding, so the destructive step that depended on a backup stays unsent.

🔎 Tip: `clean --from findings.json` acts on a report already reviewed, and refuses when that report came from a different account than the current credentials.

---

#### What the Checks Leave Alone

A false positive here costs an outage, so each check states the condition it will not act on.

| Condition | Treatment |
|---|---|
| Load balancer with registered targets that are unhealthy | An outage; left alone |
| Snapshot with no recorded source volume | Orphan status unprovable; left alone |
| Snapshot referenced by an AMI | Load-bearing; left alone |
| AMI under 90 days old | Mid-rollout; left alone |
| Gateway VPC endpoint for S3 or DynamoDB | Free; never reported |
| Default security group of a VPC | Undeletable; never reported |
| Default VPC with nothing in it | Normal in every region; never reported |
| AWS managed KMS key, disabled | Free; never reported |
| Key or secret already scheduled for deletion | Leaving on a timer; left alone |

`empty-vpc` has no cleanup step. A VPC deletes only after its subnets, route tables and gateways are gone, in an order this tool leaves to an operator, and the finding says so where a cleanup command would otherwise appear.

`unused-ami` reads instances and reports its own limit in every finding: launch templates, Auto Scaling groups and cross-account shares stay outside its view, so an AMI an Auto Scaling group depends on appears unused to it.

---

#### Compare and Contrast

| | zombiescan | AWS Trusted Advisor | Cost Explorer | Cross-account SaaS |
|---|---|---|---|---|
| Per-resource dollar figure | 🥇 yes | partial | aggregate only | yes |
| Credentials leave the machine | 🥇 never | n/a, AWS-side | n/a, AWS-side | ❌ role granted |
| Deletes on request | 🥈 opt-in, dry run first | ❌ no | ❌ no | 🥇 yes |
| Price source | Price List API, dated | AWS-side | your bill | vendor |
| Runs offline after install | 🥇 yes | ❌ no | ❌ no | ❌ no |
| Breadth of checks | 22 | broader | n/a | broader |

---

#### So, Which One?

Cost Explorer answers what the account spent. Trusted Advisor covers more ground and reaches areas outside these 22 checks.

`zombiescan` fits the case where the answer needs to be per-resource, priced, and produced without granting anything a role in the account. A laptop, existing credentials, and about twenty seconds.

---

#### Cost

A scan costs nothing. Describe and List calls carry no charge, and the price table ships with the package, so a scan makes no `pricing:GetProducts` call.

Regenerating the table calls the Price List API, which is also free.

---

#### Tests

```
uv run pytest -q
```

```
200 passed, 2 deselected in 0.79s
```

The suite runs offline against recorded API responses, with a price table pinned separately from the bundled one, so regenerating real prices leaves every assertion intact. The two deselected tests reach a live account and run under `ZOMBIESCAN_LIVE=1`.

---

#### Teardown

```
uv tool uninstall zombiescan
```

Nothing is left in the account. The tool creates no role, no bucket, no stack and no stored state.

---

#### Summary

The goal of this article was to audit an AWS account for unused resources and attach a monthly cost to each one. The key to the solution was fetching every rate from the AWS Price List API and keeping the scan read-only, with deletion behind a separate command that previews itself. The results were:

- 🟢 22 checks across storage, compute, networking, data services and account-wide services
- 🟢 A 17-region sweep in about 20 seconds, with per-check subtotals and a per-resource table
- 🟢 Prices dated in every report, and per-region: a NAT gateway is $32.85 a month in us-east-1 and $67.89 in sa-east-1
- 🟢 JSON against a published schema, a self-contained HTML report, and a shell cleanup plan
- 🟢 `clean --apply` backs a resource up before removing it, and stops the sequence when a backup fails
- ⚠️ `unused-ami` reads instances only; launch templates and Auto Scaling groups stay outside its view
- ⚠️ Costs are on-demand list prices, excluding savings plans, reserved capacity, private pricing and credits
- ❌ `empty-vpc` reports without a cleanup step, because a VPC needs its dependencies removed in an order the tool leaves to an operator

Scope: one AWS account, 17 enabled regions, 22 checks, a single sweep per figure quoted, run from one laptop against `aws login` credentials. The 86 findings reported here come from an account whose waste is entirely in the free classes, so the dollar figures in the tables above come from the price table itself.

The strategy for using per-resource pricing for AWS waste detection was validated with an incremental step by step approach.

---

#### References

- Repository, MIT: https://github.com/xbill9/zombiescan
- Findings JSON Schema: https://github.com/xbill9/zombiescan/blob/main/docs/findings.schema.json
- AWS Price List API, `GetProducts`: https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_pricing_GetProducts.html
- Amazon VPC pricing, public IPv4 addresses: https://aws.amazon.com/vpc/pricing/
- AWS CLI `aws login`: https://docs.aws.amazon.com/cli/latest/reference/login.html
