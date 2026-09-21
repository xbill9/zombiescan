# zombiescan: find the AWS resources nobody is using, and what they cost you

> Submitted to the AWS Builder Center as a project write-up.
> Draft: https://builder.aws.com/edit/project/3JWn4aYr5vvfPRgVurGlrXJ1ZgE
> Kept here so the text is versioned rather than living only in a web form.

**Description (345/512):** An open-source, local-first CLI that scans an AWS
account for 22 kinds of resource nothing is using — unattached disks, idle NAT
gateways, stranded multipart uploads, disabled KMS keys — prices each one from
the AWS Price List API, and can clean them up. Credentials never leave your
machine, and the scan cannot change anything by construction.

**Tags:** `workplace-efficiency` (the lane tag could not be set — see below)
**Repository:** https://github.com/xbill9/zombiescan

---

Every AWS account accumulates things nobody is using. A volume detached from an instance that got replaced. An Elastic IP freed up during a migration that nobody released. A NAT gateway in a VPC whose workload was torn down eighteen months ago. None of it breaks. Nothing alerts. The bill just goes up.

The tooling for this already exists, which was my problem with building another one. Trusted Advisor, Cost Explorer, Config and a dozen open-source scanners will all happily list your resources. What they mostly won't tell you is the only thing that makes anyone act: **this specific thing is attached to nothing, and it is costing you $47 a month.**

So `zombiescan` is not an inventory tool. It's a waste finder with a dollar figure attached to every line.

## What it finds

Twenty-two checks. Seventeen cost real money: `unattached-ebs`, `unassociated-eip`, `orphaned-snapshot`, `stopped-instance`, `stopped-rds-instance`, `idle-nat-gateway`, `idle-load-balancer`, `empty-classic-lb`, `unused-vpc-endpoint`, `unmounted-efs`, `disabled-kms-key`, `stale-secret`, `unused-ami`, `incomplete-multipart-upload`, `orphaned-rds-snapshot`, `idle-provisioned-dynamodb`, `unused-route53-health-check`.

Five cost nothing but accumulate without limit and block deletions: unattached network interfaces, unused security groups, empty VPCs, detached internet gateways, and log groups with no retention policy.

My favourite is `incomplete-multipart-upload`. When a multipart upload fails — an interrupted `aws s3 cp`, a crashed backup job, an SDK retry that gave up — the parts already uploaded stay in the bucket and bill at full storage rates. They appear in no object listing. Not the console, not `aws s3 ls`, not the bucket size on the overview page. The only way to see them is to ask for them by name, which is why they survive for years.

## Prices come from the API, not from memory

My first instinct was to hardcode a price table. The agent wired up the AWS Price List API instead, and the numbers immediately contradicted what I would have written down. gp3 is $0.08/GB-month in us-east-1 — and $0.088 in eu-west-1, $0.096 in ap-southeast-2. A NAT gateway is $32.85/month in us-east-1 and **$67.89 in sa-east-1**. An interface VPC endpoint doubles from $7.30 to $15.33 between the same two regions.

A tool assuming one flat rate understates a São Paulo finding by half. Every report also records *when* the price table was generated, because a cost report nobody can date is not auditable.

That fetcher produced the best bug of the build. AWS returns tiered pricing and free allowances as sibling *dimensions inside a single price entry*, and the parser took the first one it saw. S3 storage read whichever tier came back first, and DynamoDB's `$0.00` free-allowance row arrived ahead of the real rate — where a `price <= 0` guard then discarded the entire region. That is why us-east-1 was missing from a 28-region table and priced at zero.

The verification was the interesting part: we diffed the regenerated table against the old one. 23 rates changed (all S3), 31 added (all DynamoDB), 0 removed, every other product byte-identical — so every price shipped before the fix was provably correct.

## Local-first, and read-only by construction

Every SaaS tool in this space asks you to create a cross-account IAM role into your production account. `zombiescan` never asks.

`scan` makes Describe, List and Get calls only. `clean` does delete things, and is a separate command for that reason — not a flag you can reach by typo. The design is a plan/execute split: cleaners never call anything, they *yield* the mutations that would resolve a finding, and a runner decides whether to send them. `--apply` gates exactly one thing — whether a planned step executes. It cannot change which steps get planned, which is the only way a dry run is worth reading.

Backups precede destruction and the ordering is enforced. If a backup step fails, the destructive step that assumed it succeeded does not run.

## What it deliberately refuses to flag

A false positive here costs someone an outage.

- A load balancer whose targets are registered but **unhealthy** is an outage, not waste.
- A snapshot with no recorded source volume cannot be *proven* orphaned.
- A snapshot backing an AMI is load-bearing.
- An AMI under 90 days old is probably mid-rollout.
- Gateway VPC endpoints (S3, DynamoDB) are free.
- AWS managed KMS keys are free, so a disabled one is not waste.
- `empty-vpc` has no cleaner at all: a VPC will not delete until its subnets, route tables and gateways are gone in an order this tool does not attempt to derive.

## Where the coding agent earned its place

**It refused to trust its own training data about prices.** Where the API genuinely doesn't expose something — public IPv4 pricing lives under a product family that is entirely Wavelength CarrierIP — it recorded a documented constant with its source rather than guessing.

**It found bugs by using the thing, not reading it.** Four came from running the CLI rather than the tests:

- A scan of a nonexistent region printed *"No waste found"* and exited **0**. Every check had failed. A false all-clear is the worst failure this tool can have.
- A resource name containing a single quote broke out of the quoting in the generated cleanup script and became an executable command — still *valid bash*, so `bash -n` passed.
- 67 real findings tied at $0.00 rendered as "$0.00/month ($0.01/year)", which reads as a broken tool rather than cleanup debt.
- boto3 cannot read `aws login` sessions without `botocore[crt]`.

**It was also wrong twice and needed steering.** It proposed a hosted web frontend twice before accepting local-only was the design, and framed "not on PyPI" as a shortfall when a one-line git install is fine.

200 tests, all offline against recorded fixtures. A 22-check sweep of 17 regions takes about 19 seconds.

## Try it

```
uv tool install git+https://github.com/xbill9/zombiescan
zombiescan scan --all-regions
```

Read-only. It will tell you what it found and what it costs, and delete nothing.

Source, MIT: **https://github.com/xbill9/zombiescan**
