---
title: "Cross-Checking an AWS Waste Scanner Against the Account It Scans"
published: false
description: "A waste scanner reports what its checks cover. Reading the same AWS account a second time through the service APIs and Cost Explorer shows what the catalogue misses: two whole services with no check behind them, and a finding the scanner holds back on purpose."
tags: aws, python, devops, opensource
cover_image: https://raw.githubusercontent.com/xbill9/zombiescan/main/articles/zombiescan-crosscheck/devto-cover.47d4b2bb.jpg
---

This article provides a step by step guide to auditing a cost scanner against the account it reports on, by reading that account a second time through the AWS service APIs and Cost Explorer. A suite of Python checks is extended to cover the two services the first read showed missing.

https://github.com/xbill9/zombiescan

---

#### A Scanner Reports What Its Checks Cover

A waste scanner answers one question: of the resource types it knows about, which ones are unused. A resource type with no check behind it contributes zero to the total, and the report reads the same as it would for an account that is clean.

The account here runs a container service, a lakehouse table bucket and thirty-seven container image repositories. The scanner reported waste of under one cent a month.

Cost Explorer gives the other half of the picture. It knows what every service charged, including the services the scanner never calls.

---

#### At This Point You Should Have…

- An AWS account with credentials your shell can use, from `aws login`, a profile, SSO or environment variables
- `pricing:GetProducts` and `ce:GetCostAndUsage` for the cost side
- Python 3.12 and `uv`
- Read-only access wide enough to describe resources in every region you scan

---

#### Step 1 — Scan With the Catalogue as It Stands

Restrict the run to the twenty-two checks the tool shipped with, against the four US regions.

```
uv run zombiescan scan --all-regions --us-only <22 x --check ...>
```

```
Scanning as arn:aws:iam::<account>:root
4 region(s), 22 check(s) — read-only

zombiescan — 86 findings across 4 regions

Check                   Found  Monthly
log-group-no-retention     67    $0.00
empty-vpc                   1    $0.00
unused-security-group      18    $0.00

Estimated waste: under $0.01/month. These cost almost nothing today; they are
cleanup debt, not a bill.
```

Eighty-six findings, every one of them free. Twelve of the priced checks — unattached EBS, unassociated Elastic IPs, idle NAT gateways, idle load balancers, stopped RDS — returned nothing.

---

#### Step 2 — Ask Cost Explorer What the Account Charged

The scanner reads resources. Cost Explorer reads invoices, so it covers every service.

```
aws ce get-cost-and-usage --granularity MONTHLY --metrics UnblendedCost \
  --group-by Type=DIMENSION,Key=SERVICE --time-period Start=2026-07-01,End=2026-08-01
```

July 2026, the most recent month with unoffset charges:

| Service | Charged |
|---|---|
| Amazon Elastic Compute Cloud - Compute | $130.34 |
| EC2 - Other | $125.98 |
| Amazon Simple Storage Service | $13.70 |
| Amazon Lightsail | $7.00 |
| Amazon Virtual Private Cloud | $4.42 |
| Amazon EC2 Container Registry (ECR) | $3.44 |
| AWS Key Management Service | $2.00 |

Two lines on that invoice belong to services the scanner makes no API call to: Lightsail and ECR.

---

#### Step 3 — Read the Account Directly

`gather-evidence.py` in the article directory walks ECR in three regions, Lightsail in us-east-1, and EC2 images, snapshots and volumes. Describe and List calls only.

```
uv run python articles/zombiescan-crosscheck/gather-evidence.py
```

```
ECR: 37 repos, 66.48 GiB
wrote .../evidence/aws-inventory.json and cost-explorer.json
```

Thirty-seven repositories holding 66.48 GiB of images, against a catalogue with no ECR check in it.

---

#### Step 4 — The First Gap: Container Images

The inventory sorts by stored bytes and carries the most recent push date per repository.

| Repository | Images | GiB | Newest push |
|---|---|---|---|
| ai-course-creator-app | 26 | 5.21 | 2026-04-14 |
| ai-course-creator-orchestrator | 26 | 4.79 | 2026-04-14 |
| course-creator-stack | 21 | 4.71 | 2026-04-25 |
| ai-course-creator-judge | 26 | 4.41 | 2026-04-14 |
| biometric-scout | 18 | 3.81 | 2026-04-24 |

Two generations of the same project sit side by side — `ai-course-creator-*`, `adk-course-creator-*` and a set of bare names — each around twenty-six images, none pushed to since April. Every build added a tag and nothing removed one.

---

#### Step 5 — Pricing ECR From the Price List API

ECR storage lives under the `AmazonECR` service code in the product family `EC2 Container Registry`.

```
aws pricing get-products --service-code AmazonECR --region us-east-1
```

That family also prices archive retrieval per GB and image signing per Count, so the `GB-Mo` unit selects the storage rate:

```
USE1-TimedStorage-ByteHrs   GB-Mo   0.1000000000
```

`$0.10` per GB-month, identical across all thirty-six regions the API returns. The refresh script buckets it by unit and stores it per region.

---

#### Step 6 — Why the ECR Figure Is an Upper Bound

ECR bills for unique layers. Images inside one repository share base layers, and repositories built from one base share them with each other, so adding up `imageSizeInBytes` counts the same stored bytes more than once.

The check reports its total as a ceiling and marks every finding approximate:

```json
"approximate_cost": true,
"approximate_reason": "shared-layers",
"note": "upper bound: ECR bills for unique layers, and images sharing a base layer are counted once each here"
```

The account gives the size of the gap. The check computes $6.42 a month across the flagged repositories; the July invoice charged $3.44. Both figures are correct, and the difference is the layers counted twice.

---

#### Step 7 — What the Check Skips

Thirty-seven repositories produce thirty-three findings.

Two are empty. A repository with no images stores nothing, so it bills nothing, and the check passes over it. Two more — `currency-mesh` and `research-mesh` — were pushed to in August, inside the ninety-day window, so the images are still changing.

```
33 flagged, 64.198 GiB, x $0.10 = $6.42
```

Staleness is judged from the newest push in a repository. One fresh tag keeps a repository out of the report however old the rest of its images are.

---

#### Step 8 — The Second Gap: A Whole Service

Lightsail bills separately from EC2 and answers on its own API. The scanner made no `lightsail` call at all, so instances, disks, static IPs, load balancers, snapshots and container services were all outside its reach.

```
aws lightsail get-container-services --region us-east-1
```

```json
{"name": "dog-or-not-lite", "state": "RUNNING", "power": "nano", "scale": 1,
 "has_currentDeployment": true, "currentDeployment_state": "ACTIVE",
 "createdAt": "2026-08-15T14:26:08+00:00"}
```

---

#### Step 9 — Where Lightsail Prices Come From

Lightsail's own API returns a monthly price keyed by the same identifiers the describe calls report.

```
aws lightsail get-container-service-powers --region us-east-1
aws lightsail get-bundles --region us-east-1
```

```
nano 7.0   micro 10.0   small 15.0   medium 40.0   large 80.0   xlarge 160.0
nano_3_0 5.0   micro_3_0 7.0   small_3_0 12.0
```

The Price List API carries the same rates under usagetype strings such as `USE1-BundleUsage:1GB`, which have to be mapped back to a `bundleId` like `micro_3_0` by hand. A wrong mapping prices the wrong machine and reads as a valid number.

Lightsail also bills container nodes over a 744-hour month: the Price List hourly rate for nano is `0.0094086020`, and `0.0094086020 x 744 = 7.00`. Multiplying by the price table's 730 hours gives $6.87 and understates every container service. Taking the monthly price from Lightsail avoids both problems.

Disk, static IP, load balancer and snapshot rates do come from the Price List, bucketed by the `group` attribute, because Lightsail products leave `productFamily` null.

---

#### Step 10 — The Stopped Instance Charge

Of the six Lightsail checks, `lightsail-stopped-instance` covers the largest gap between what a bill does and what an EC2 habit predicts.

Stopping an EC2 instance ends the compute charge and leaves the disks. A Lightsail bundle is one flat monthly price covering compute, storage and transfer together, and it is billed in full for every hour the instance exists in any state.

A Lightsail instance stopped to save money keeps charging its whole bundle. Deleting it ends the charge, which is why the cleaner takes a snapshot first:

```
aws lightsail create-instance-snapshot --instance-name <name> \
  --instance-snapshot-name <name>-final --region <region> \
  && aws lightsail delete-instance --instance-name <name> --region <region>
```

---

#### Step 11 — A Finding the Scanner Holds Back

us-east-1 holds one snapshot and no volumes.

```
aws ec2 describe-snapshots --owner-ids self --region us-east-1
aws ec2 describe-volumes --region us-east-1
```

```
snap-0eae9560093fac9c9  80 GiB  vol-091123a2014b25abe
  "gpu-vllm-g5g-2b SM7.5 vLLM image, 80 GiB, PARTUUID corrected"
Volumes: 0
```

Its source volume is gone, which makes it a candidate for `orphaned-snapshot`. That check passes over it, because the snapshot backs an AMI:

```
ami-0b44b90b3d02430ee  gpu-vllm-g5g-2b-sm75-vllm0272rc0-80g-v2
  CreationDate 2026-08-13   age_days 38   snapshots ['snap-0eae9560093fac9c9']
```

No instance in the account uses that image, so `unused-ami` is the check that covers it. It passes over it too, on age:

```python
MIN_AGE_DAYS = 90
...
if age is None or age < MIN_AGE_DAYS:
    continue
```

At thirty-eight days the image sits inside the window, where a fresh AMI is treated as mid-rollout. Eighty GiB at the us-east-1 snapshot rate of $0.05 per GB-month is $4.00 a month, held back until the image passes ninety days.

A threshold that suppresses a real charge for three months is a deliberate trade against reporting images that an Auto Scaling group or launch template still references, which this check cannot see.

---

#### Step 12 — A Region With No Endpoint

Lightsail serves fifteen regions of the account's seventeen. A check against the other two reaches a hostname that does not resolve.

```
us-west-1 lightsail-stopped-instance: EndpointConnectionError: Could not
connect to the endpoint URL: "https://lightsail.us-west-1.amazonaws.com/"
```

Six Lightsail checks in each unserved region produce six of those lines, and none of them describes a failure. The engine counts them apart from errors:

```python
except botocore.exceptions.EndpointConnectionError:
    # The service is not offered in this region, so there is
    # nothing to find and nothing went wrong.
    result.unavailable += 1
```

A pair counts as scanned only when it neither errored nor was unavailable, so a run where every service is missing still reports as incomplete:

```json
{"pairs_attempted": 2, "pairs_unavailable": 1, "complete": true}
```

---

#### Step 13 — Scan Again

Same account, same four regions, twenty-nine checks.

```
uv run zombiescan scan --all-regions --us-only
```

```
4 region(s), 29 check(s) — read-only

zombiescan — 119 findings across 4 regions

Check                   Found  Monthly
ecr-stale-images           33    $6.42
log-group-no-retention     67    $0.00
empty-vpc                   1    $0.00
unused-security-group      18    $0.00

Estimated waste: $6.42/month ($77.05/year)
```

All six Lightsail checks return nothing, and that reading is correct: `dog-or-not-lite` carries an ACTIVE deployment, so it is a service in use. The account holds no stopped instances, detached static IPs or disks, empty load balancers or orphaned Lightsail snapshots.

---

#### What the Checks Still Leave Alone

The direct read named resources that remain outside the catalogue:

- **S3 Tables** — a table bucket and a Glue database backing a lakehouse
- **A private Route 53 hosted zone** at $0.50 a month, charged on every invoice since April
- **Eight CloudFormation stacks** in `CREATE_COMPLETE` describing EKS clusters that `eks:ListClusters` no longer returns
- **Twelve KMS key ids**, which `ListKeys` reports without separating customer managed keys from the AWS managed keys that cost nothing

Each needs its own check, its own price entry and its own recorded API responses.

---

#### Compare and Contrast

| | Scanner catalogue | Cost Explorer | Direct API read |
|---|---|---|---|
| Names the resource | 🥇 yes, with an id | ❌ service totals only | 🥇 yes, with an id |
| Covers every service | ❌ only what has a check | 🥇 yes | 🥈 only what you call |
| Gives a price | 🥇 per resource | 🥇 per service, as billed | ❌ none |
| Shows what it misses | ❌ reads as zero | 🥇 a line with no check behind it | 🥈 by comparison |
| Cost to run | free | $0.01 per request | free |

---

#### So, Which One?

Cost Explorer decides where to look, because a service line with no matching check is the shape of a gap. The direct read names the resources inside that line. The scanner is where the answer gets encoded, so the next account gets it without the cross-check.

---

#### Cost

Cost Explorer charges $0.01 per paid request, and the July invoice shows $0.03 against it. The describe calls and the Price List API are free. The scan performs no mutation in any mode.

---

#### Tests

Each new check is tested against recorded AWS API responses, so the suite runs offline with no credentials.

```
uv run pytest -q
```

```
256 passed, 2 deselected in 0.69s
```

The two deselected are the live smoke test, which costs money and needs real credentials.

---

#### Summary

The goal of this article was to establish what a waste scanner misses on an account it reports as clean. The key to the solution was reading the same account a second time, through Cost Explorer for coverage and the service APIs for resource names. The results were:

- 🟢 Twenty-two checks reported under $0.01 a month across 86 findings
- 🟢 Cost Explorer named two charged services with no check behind them: ECR at $3.44 and Lightsail at $7.00 in July
- 🟢 Thirty-seven ECR repositories held 66.48 GiB, 33 of them unpushed for ninety days or more
- 🟢 Seven new checks moved the reported total to $6.42 a month across 119 findings
- ⚠️ The ECR figure is an upper bound: $6.42 computed against $3.44 invoiced, the difference being shared layers counted once per image
- ⚠️ An 80 GiB snapshot worth $4.00 a month stays unreported until its AMI passes ninety days
- ❌ S3 Tables, a Route 53 private zone, orphaned CloudFormation stacks and KMS keys remain uncovered

One account, four US regions (us-east-1, us-east-2, us-west-1, us-west-2), scanned on 2026-09-21 with a price table generated the same day. Prices are on-demand USD list prices from the AWS Price List API, and from the Lightsail API for bundles and container powers; they ignore private pricing, savings plans and credits, so they will not match an invoice to the cent. The before and after runs differ only in which checks were enabled. Invoice figures are July 2026, the most recent month whose charges carry no offsetting rows.

The strategy for using a direct API read to audit a cost scanner was validated with an incremental step by step approach.

---

#### References

- zombiescan — https://github.com/xbill9/zombiescan
- AWS Price List API, GetProducts — https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_pricing_GetProducts.html
- Amazon ECR pricing — https://aws.amazon.com/ecr/pricing/
- Amazon Lightsail pricing — https://aws.amazon.com/lightsail/pricing/
- Lightsail GetContainerServicePowers — https://docs.aws.amazon.com/lightsail/2016-11-28/api-reference/API_GetContainerServicePowers.html
- Cost Explorer GetCostAndUsage — https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_GetCostAndUsage.html
