# zombiescan

[![CI](https://github.com/xbill9/zombiescan/actions/workflows/ci.yml/badge.svg)](https://github.com/xbill9/zombiescan/actions/workflows/ci.yml)

Find the AWS resources nobody is using, and what they cost you.

Not *"here are your 14 EBS volumes"* — **"9 of these are attached to nothing and
cost you $47/month, here is the plan to kill them."**

```
$ zombiescan scan --all-regions

zombiescan — 15 findings across 17 regions

Check                   Found  Monthly
idle-nat-gateway            1   $67.89
stopped-instance            1   $58.00
unattached-ebs              1   $50.00
unmounted-efs               1   $25.20
stopped-rds-instance        1   $23.00
unused-vpc-endpoint         1   $21.90
empty-classic-lb            1   $18.25
idle-load-balancer          1   $16.43
orphaned-snapshot           1    $5.00
unassociated-eip            1    $3.65
disabled-kms-key            1    $1.00
stale-secret                1    $0.40
log-group-no-retention      1    $0.36
empty-vpc                   1    $0.00
unused-security-group       1    $0.00

┏━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Region    ┃ Resource                    ┃  Monthly ┃ Why                                  ┃
┡━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ sa-east-1 │ nat-0a1b2c3d4e5f6a7b8       │   $67.89 │ NAT gateway in vpc-0f2e with no      │
│           │                             │          │ workload interfaces behind it        │
│ us-east-1 │ i-04c8d9e2f1a3b5c7d         │   $58.00 │ Stopped instance still paying for 2  │
│           │                             │          │ attached volumes (600 GB); stopped   │
│           │                             │          │ 565 days ago                         │
│ us-east-1 │ vol-09f8e7d6c5b4a3210       │   $50.00 │ 500 GB gp2 volume in the 'available' │
│           │                             │          │ state, attached to nothing           │
└───────────┴─────────────────────────────┴──────────┴──────────────────────────────────────┘
showing 10 of 15, costliest first with every check represented

Estimated waste: $291.08/month ($3,492.96/year)
~ marks an estimate or upper bound; the per-finding 'note' in --json output says why.
Estimates from list prices, not your bill. zombiescan is read-only and deleted nothing.
```

## Local-first

zombiescan runs on your machine with the AWS credentials you already have.
There is no hosted service, no account to create, no cross-account IAM role to
grant, and nothing is sent anywhere. Every SaaS tool in this space asks you to
hand over a role into your production account. This one never asks.

**`scan` is read-only.** Describe, List and Get calls only. It cannot change
anything, by construction.

**`clean` deletes things**, and is a separate command for that reason — it is
not a flag you can reach by typo. It dry-runs by default, asks before each
resource, and takes a backup first wherever AWS lets it.

## Install

```
uv tool install git+https://github.com/xbill9/zombiescan
zombiescan scan
```

Or clone it, to read the code before pointing it at your account:

```
git clone https://github.com/xbill9/zombiescan
cd zombiescan
uv sync
uv run zombiescan scan
```

`botocore[crt]` is a hard dependency, not an optional extra: boto3 cannot read
`aws login` sessions without it, and raises `MissingDependencyException` at
credential load if it is missing.

## Usage

```
zombiescan checks                     # list the checks
zombiescan scan                       # default region
zombiescan scan --all-regions         # every region the account has enabled
zombiescan scan --all-regions --us-only  # narrow those to the US regions
zombiescan scan --region eu-west-1 --region us-east-1
zombiescan scan --check unattached-ebs --check unassociated-eip
zombiescan scan --profile production
zombiescan scan --min-cost 5          # hide findings under $5/month
zombiescan scan --limit 0             # every finding, not just the top 25
zombiescan scan --json findings.json  # machine-readable, full detail
zombiescan scan --html report.html    # shareable report; print to PDF from a browser
zombiescan scan --script cleanup.sh   # write the plan (never runs it)
```

A full 22-check sweep of 17 regions takes about 20 seconds.

## The Claude Code plugin

The same engine, reachable from an agent instead of a terminal. It ships in
[`plugin/`](plugin/) and installs from this repository:

```
/plugin marketplace add xbill9/zombiescan
/plugin install zombiescan@zombiescan
```

That gives you `/zombiescan` to scan an account, `/zombie-cleanup` to see what
a cleanup would do, and a skill that picks itself up whenever the conversation
turns to AWS spend. Behind them is an MCP server with five tools:

| Tool | What it does |
| --- | --- |
| `list_checks` | What the scanner looks for. No credentials needed. |
| `scan_account` | Scans, prices, writes the JSON report, returns the totals |
| `estimate_savings` | Totals and breakdowns over a report, with a filter |
| `explain_finding` | Why a resource counts as waste and what keeping it costs |
| `plan_cleanup` | The calls a cleanup would make, and which have no undo |

**Every tool is read-only.** `plan_cleanup` builds the same plan `clean` shows
on a dry run and stops there; `clean.apply_outcome`, the one function that
sends a step to AWS, is not reachable from the server, and the suite asserts
that the module does not name it. Applying a plan stays `zombiescan clean
--apply` at your own terminal, where the per-resource prompt and the
irreversible-step warning are.

The tools return figures already computed — totals, counts, per-check and
per-region breakdowns, the cheapest and costliest row — and echo the filter
they applied under `filter_applied`. A filter naming a check that is not in the
report returns an exact, sourced zero, which reads like good news; the echo
carries `no_such_checks_in_report` so the mistake is visible in the answer
rather than in next month's bill.

The server speaks JSON-RPC over stdio using the standard library alone, so
installing zombiescan does not pull an MCP SDK in behind it. It can also be run
directly:

```
zombiescan-mcp     # or: uv run zombiescan-mcp
```

## Checks

| Check | Finds | Costs money |
| --- | --- | --- |
| `unattached-ebs` | Volumes in the `available` state, attached to nothing | yes |
| `unassociated-eip` | Elastic IPs allocated but associated with nothing | yes |
| `orphaned-snapshot` | Snapshots whose source volume is gone and which back no AMI | yes |
| `stopped-instance` | Stopped instances still billing for their attached disks | yes |
| `stopped-rds-instance` | Stopped databases still billing for allocated storage | yes |
| `idle-nat-gateway` | NAT gateways with no workload interfaces behind them | yes |
| `idle-load-balancer` | ALB/NLB with no registered targets | yes |
| `empty-classic-lb` | Classic (ELBv1) balancers with no instances | yes |
| `unused-vpc-endpoint` | Interface endpoints in VPCs with no workloads | yes |
| `unmounted-efs` | EFS file systems nothing can reach | yes |
| `disabled-kms-key` | Customer managed keys disabled but still billed | yes |
| `stale-secret` | Secrets nothing has read in 90 days | yes |
| `unused-ami` | AMIs over 90 days old that no instance uses | yes |
| `incomplete-multipart-upload` | S3 parts no object listing shows | yes |
| `orphaned-rds-snapshot` | Manual snapshots of databases that no longer exist | yes |
| `idle-provisioned-dynamodb` | Empty tables paying for provisioned capacity | yes |
| `unused-route53-health-check` | Health checks no DNS record references | yes |
| `unused-route53-zone` | Hosted zones holding only their default SOA and NS records | yes |
| `log-group-no-retention` | CloudWatch log groups that never expire | grows |
| `available-eni` | Unattached interfaces blocking subnet/SG deletion | no |
| `unused-security-group` | Groups attached to nothing, referenced by nothing | no |
| `empty-vpc` | VPCs holding no network interfaces at all | no |
| `detached-internet-gateway` | Gateways attached to no VPC, eating region quota | no |

The free ones are reported because they accumulate without limit and block
deletions, not because of this month's bill.

The two Route 53 checks are **global**: Route 53 has no regions, so they run
once per scan rather than once per region, and their findings are labelled
`global`. Running them seventeen times would report one health check seventeen
times and multiply the waste total to match.

A zone that AWS Cloud Map created is reported against its namespace:
`delete-hosted-zone` would leave the namespace pointing at nothing, so the
remediation is `servicediscovery delete-namespace` in the namespace's own
region. Cloud Map stamps the namespace ARN into the zone's comment, so this
costs no extra API call.

`unused-route53-zone` prices a zone at the **marginal** rate. Hosted zones cost
$0.50/month for the first 25 in an account and $0.10 after that, so deleting
one from an account with thirty saves $0.10, whatever the other twenty-nine
are priced at. A zone is flagged only when it holds exactly the SOA and NS
records Route 53 creates with it — those two cannot be deleted, so a count of
two means the zone publishes nothing at all.

`incomplete-multipart-upload` is the one worth running today even if you skip
the rest. Stranded multipart parts bill at full storage rates and appear in no
object listing — not in the console, not in `aws s3 ls`, not in the bucket size
on the overview page. They are the only finding here that is genuinely invisible
until you ask for it by name.

## About the numbers

Costs are estimates from on-demand **list prices**, not from your bill. They
ignore savings plans, reserved capacity, private pricing and credits.

Prices come from the AWS Price List API, not from anyone's memory, and are
regenerated with:

```
uv run python -m zombiescan.pricing.refresh
```

Region matters more than people expect, which is why the table is per-region
rather than a single rate: a NAT gateway is **$32.85/month in us-east-1 and
$67.89 in sa-east-1**, and an interface VPC endpoint doubles from $7.30 to
$15.33 between the same two.

A `~` next to a cost means it is an estimate or an upper bound — the region had
no price entry, the resource type was unrecognised, or the resource bills
incrementally rather than on provisioned size. Every finding carries a `note`
in `--json` output saying which.

Public IPv4 is the one hardcoded figure. The Price List API does not expose it:
the `IP Address` product family under EC2 turns out to be entirely Wavelength
CarrierIP, and AmazonVPC has no matching family. It is recorded as a constant
with its source rather than guessed per-region.

## What it deliberately does not flag

Checks would rather miss waste than invent it. A false positive here costs
someone an outage.

- A load balancer whose targets are registered but **unhealthy** is an outage,
  not waste.
- A snapshot with no recorded source volume cannot be *proven* orphaned.
- A snapshot backing an AMI is load-bearing.
- An AMI under 90 days old is probably mid-rollout.
- A log group with no retention and no data costs nothing today.
- Gateway VPC endpoints (S3, DynamoDB) are free.
- A VPC's default security group cannot be deleted.
- A default VPC sitting unused is normal in every region.
- AWS managed KMS keys are free, so a disabled one is not waste.
- A key or secret already scheduled for deletion is leaving on a timer.

Two checks report **staleness**, which is a prompt to look rather than a
verdict. `stale-secret` cannot tell an abandoned secret from break-glass
credentials that are dormant by design. A secret nothing has ever read is
judged on its age instead — from the later of when it was created and when its
value was last written, because a secret rewritten last week is being looked
after whoever reads it. Otherwise every secret would be waste for its first
ninety days, having never been retrieved yet.

`unused-ami` is the least certain check here: it can see instances but not
launch templates, Auto Scaling groups, or cross-account shares. An AMI your ASG
depends on looks unused to it, and deleting one breaks the next scale-out hours
later. Read its findings before acting on them.

## Cleaning up

`scan` tells you what to delete. `clean` does it.

```
zombiescan clean                          # dry run: prints the exact API calls, changes nothing
zombiescan clean --apply                   # asks before each resource
zombiescan clean --apply --yes             # no prompts
zombiescan clean --from findings.json      # act on a report you have already read
zombiescan clean --check unassociated-eip --apply
zombiescan clean --apply --audit audit.json
```

**Dry run is the default and it is exact.** `--apply` changes one thing: whether
a planned call is sent. It does not change which calls get planned, so what the
dry run shows is what the real run does.

**Backups come first where AWS allows one.** Volumes are snapshotted before
deletion, instances imaged before termination, databases given a final snapshot,
secrets deleted with a 30-day recovery window. If the backup step fails, the
destructive step that assumed it does not run.

**Irreversible steps are labelled.** Deleting a volume after snapshotting it is
recoverable; deleting the snapshot is not. Terminating an instance, scheduling a
KMS key for deletion, deleting an EFS file system or setting log retention all
destroy data with no undo, and the prompt says so before you answer.

**`--audit` writes a record** of every call attempted, its parameters, its
result and what it saved — the file you will want when someone asks what
happened.

`empty-vpc` is the one finding with no cleaner. A VPC will not delete until
every subnet, route table and gateway inside it is gone, and working out that
order safely is a different tool. It is reported as unsupported with that reason
rather than attempted.

### Permissions for cleaning

`clean --apply` needs write access, which is a different posture from scanning.
Grant it deliberately and scope it to what you intend to remove — an
`AdministratorAccess` run of `clean --apply --yes` across all regions is capable
of deleting a great deal.

## The generated script

`--script` writes a plan. zombiescan never runs it, and has no flag that will.

Every value interpolated into a command is shell-quoted, so a resource name
cannot become a command in the file you are about to execute. AWS's own naming
rules make that unreachable today, but a tool whose whole proposition is handing
you a script to run should not depend on a remote service's input validation for
local shell safety.

Where a backup is possible the command takes one first — volumes are
snapshotted, instances imaged, databases given a final snapshot, secrets deleted
with a 30-day recovery window. A backup is not a substitute for knowing what you
are deleting.

## Output formats

**`--json`** is a versioned contract, not a dump. The schema lives at
[`docs/findings.schema.json`](docs/findings.schema.json) and the suite validates
real output against it, so the two cannot drift. Check `schema_version` before
parsing: the shape will change, and a consumer that cannot tell which version it
is reading breaks silently rather than loudly.

Two fields matter more than they look:

- `pricing.generated` — when the bundled price table was built. Without it a
  cost report is not auditable, because nobody can tell whether it used current
  rates.
- `scan.complete` — false when every region/check pair failed. An incomplete
  scan finds nothing, and "found nothing" must never be read as "you are clean".

**`--html`** writes one self-contained file: no stylesheet, no font, no script,
no network request. Cost reports get emailed, attached to tickets and opened on
laptops with no internet, and one that renders as unstyled text in those places
is worse than no report. It carries a print stylesheet, so a PDF is
browser-print-to-PDF — which is how most people make one anyway, and avoids
taking on a rendering engine as a dependency.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | The scan ran. Findings may or may not exist. |
| 1 | Nothing could be scanned — every region/check pair failed. **Not an all-clear.** |
| 2 | Usage or credentials problem: unknown check, unknown profile, no credentials. |

The distinction between 0 and 1 matters in CI. A scan of a region that does not
exist finds nothing, and "found nothing" must never be reported as "you are
clean".

## Permissions

Read-only. The managed `ReadOnlyAccess` policy is more than enough; a minimal
policy needs `Describe*`/`List*`/`Get*` on ec2, rds, elasticloadbalancing, logs,
kms, efs, secretsmanager, s3, dynamodb and route53, plus `sts:GetCallerIdentity`.

Refreshing the price table additionally needs `pricing:GetProducts`, which is
only used by `zombiescan.pricing.refresh` and never during a scan.

## Development

```
uv run pytest                            # 281 tests, offline, no credentials
ZOMBIESCAN_LIVE=1 uv run pytest -m live  # end-to-end against a real account
uv run ruff format . && uv run ruff check --fix .
```

The offline suite runs against recorded API fixtures with a price table pinned
separately from the bundled one, so refreshing real prices cannot break an
assertion. The live test is excluded by default because it costs money.

### Packs

Checks are grouped into **packs**. A pack carries its own checks, cleaners,
price rates and rate fetchers, and is discovered rather than listed — the
built-in `core` and `lightsail` packs load through the same path as a pack
installed from PyPI:

```
zombiescan packs                    # what is installed and what each contributes
zombiescan scan --disable-pack lightsail
```

Adding a check means a module in `src/zombiescan/packs/<pack>/`, a fixture, a
test, a rate plus its fetcher, and either a cleaner or an `uncleanable` reason.
There is no import list to update. Pass `scope="global"` to the `@check`
decorator for account-wide services with no region of their own, and use
`simple_check` from `zombiescan.building` when a check really is one describe
call and one filter.

Checks make Describe/List/Get calls only; a check that mutates anything is a
bug, not a feature request. Note that this is a promise kept by the people who
write checks, not enforced by the machinery that runs them — a third-party pack
runs with your AWS credentials like any other dependency.

**[docs/PACKS.md](docs/PACKS.md) is the guide for writing one.**

## License

MIT.
