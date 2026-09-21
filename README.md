# zombiescan

Find the AWS resources nobody is using, and what they cost you.

Not "here are your 14 EBS volumes" — "9 of these are attached to nothing and
cost you $47/month, here is the plan to kill them."

## Local-first

zombiescan runs on your machine with your existing AWS credentials. There is no
hosted service, no cross-account IAM role to create, and no data sent anywhere.

It is **read-only**: Describe/List/Get calls only. It never deletes anything.
The cleanup commands it generates are written to a file for you to read and run
yourself.

## Install

```
uv sync
uv run zombiescan scan
```

`botocore[crt]` is a required dependency, not an optional extra — boto3 cannot
read `aws login` sessions without it.

## Usage

```
uv run zombiescan checks                    # list the checks
uv run zombiescan scan                      # default region
uv run zombiescan scan --all-regions        # every region the account has enabled
uv run zombiescan scan --check unattached-ebs --check unassociated-eip
uv run zombiescan scan --min-cost 5         # hide findings under $5/month
uv run zombiescan scan --limit 0            # show every finding, not just the top 25
uv run zombiescan scan --json findings.json # machine-readable output
uv run zombiescan scan --script cleanup.sh  # write the (unexecuted) cleanup plan
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
| `log-group-no-retention` | CloudWatch log groups that never expire | grows |
| `available-eni` | Unattached interfaces blocking subnet/SG deletion | no |
| `unused-security-group` | Groups attached to nothing, referenced by nothing | no |
| `empty-vpc` | VPCs holding no network interfaces at all | no |
| `detached-internet-gateway` | Gateways attached to no VPC, eating region quota | no |

The free ones are reported because they accumulate without limit and block
deletions, not because of this month's bill.

## About the numbers

Costs are estimates from on-demand list prices, not from your bill. They ignore
savings plans, private pricing, and credits.

Prices come from the AWS Price List API and are regenerated with:

```
uv run python -m zombiescan.pricing.refresh
```

A `~` next to a cost means it is an estimate or an upper bound — the region had
no price entry, the resource type was unrecognised, or the resource bills
incrementally. Every finding carries a `note` in `--json` output explaining
which.

Public IPv4 pricing is the one hardcoded figure: the Price List API does not
expose it.

## What it deliberately does not flag

Checks would rather miss waste than invent it:

- A load balancer whose targets are registered but unhealthy is an outage, not
  waste.
- A snapshot with no recorded source volume cannot be proven orphaned.
- A snapshot backing an AMI is load-bearing.
- An AMI under 90 days old is probably mid-rollout.
- A log group with no retention and no data costs nothing today.
- Gateway VPC endpoints (S3, DynamoDB) are free.
- A VPC's default security group cannot be deleted.
- A default VPC sitting unused is normal in every region.
- AWS managed KMS keys are free, so a disabled one is not waste.
- A KMS key or secret already scheduled for deletion is leaving on a timer.

Two checks report *staleness*, which is a prompt to look rather than a verdict.
`stale-secret` cannot tell an abandoned secret from break-glass credentials that
are dormant by design. `unused-ami` is the least certain check in the catalog: it can see instances but
not launch templates, Auto Scaling groups, or cross-account shares. Read its
findings before acting on them.

## Development

```
uv run pytest                               # offline, no credentials needed
ZOMBIESCAN_LIVE=1 uv run pytest -m live     # end-to-end against a real account
uv run ruff format . && uv run ruff check --fix .
```

MIT.
