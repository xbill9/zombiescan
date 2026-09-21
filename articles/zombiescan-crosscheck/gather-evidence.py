#!/usr/bin/env python3
"""Regenerate the AWS-side evidence for this article.

    uv run python articles/zombiescan-crosscheck/gather-evidence.py

Read-only: Describe/List/Get only. Writes evidence/aws-inventory.json and
evidence/cost-explorer.json, each with a captured_utc stamp.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib

import boto3

HERE = pathlib.Path(__file__).parent
EVIDENCE = HERE / "evidence"
ECR_REGIONS = ["us-east-1", "us-east-2", "us-west-2"]
NOW = dt.datetime.now(dt.UTC)


def ecr_inventory(session):
    rows, total = [], 0
    for region in ECR_REGIONS:
        client = session.client("ecr", region_name=region)
        repos = []
        for page in client.get_paginator("describe_repositories").paginate():
            repos.extend(page["repositories"])
        for repo in repos:
            details = []
            for page in client.get_paginator("describe_images").paginate(
                repositoryName=repo["repositoryName"]
            ):
                details.extend(page["imageDetails"])
            size = sum(d.get("imageSizeInBytes", 0) for d in details)
            total += size
            pushes = [d["imagePushedAt"] for d in details if d.get("imagePushedAt")]
            rows.append(
                {
                    "region": region,
                    "repo": repo["repositoryName"],
                    "images": len(details),
                    "bytes": size,
                    "gib": round(size / 1024**3, 3),
                    "newest_push": str(max(pushes))[:10] if pushes else None,
                }
            )
    rows.sort(key=lambda r: -r["bytes"])
    return {
        "repo_count": len(rows),
        "empty_repos": sum(1 for r in rows if r["images"] == 0),
        "total_bytes": total,
        "total_gib": round(total / 1024**3, 2),
        "note": "gib is bytes/1024**3, which is what the check uses (BYTES_PER_GB)",
        "repos": rows,
    }


def main():
    session = boto3.Session()
    ec2 = session.client("ec2", region_name="us-east-1")
    images = ec2.describe_images(Owners=["self"])["Images"]
    amis = []
    for image in images:
        created = dt.datetime.fromisoformat(image["CreationDate"].replace("Z", "+00:00"))
        amis.append(
            {
                "ImageId": image["ImageId"],
                "Name": image.get("Name"),
                "CreationDate": image["CreationDate"],
                "age_days": (NOW - created).days,
                "snapshot_ids": [
                    b["Ebs"]["SnapshotId"]
                    for b in image.get("BlockDeviceMappings", [])
                    if b.get("Ebs")
                ],
            }
        )

    inventory = {
        "captured_utc": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ecr": ecr_inventory(session),
        "lightsail_container_services": [
            {
                "name": s["containerServiceName"],
                "state": s.get("state"),
                "power": s.get("power"),
                "scale": s.get("scale"),
                "has_currentDeployment": bool(s.get("currentDeployment")),
                "currentDeployment_state": (s.get("currentDeployment") or {}).get("state"),
                "createdAt": str(s.get("createdAt")),
            }
            for s in session.client("lightsail", region_name="us-east-1")
            .get_container_services()["containerServices"]
        ],
        "lightsail_container_powers_us_east_1": {
            p["name"]: p["price"]
            for p in session.client("lightsail", region_name="us-east-1")
            .get_container_service_powers()["powers"]
        },
        "ec2_us_east_1": {
            "volumes_present": len(ec2.describe_volumes()["Volumes"]),
            "amis": amis,
            "snapshots": [
                {
                    "SnapshotId": s["SnapshotId"],
                    "VolumeId": s.get("VolumeId"),
                    "VolumeSize": s.get("VolumeSize"),
                    "StartTime": str(s.get("StartTime")),
                    "Description": s.get("Description"),
                }
                for s in ec2.describe_snapshots(OwnerIds=["self"])["Snapshots"]
            ],
        },
    }
    (EVIDENCE / "aws-inventory.json").write_text(json.dumps(inventory, indent=2) + "\n")

    today = dt.date.today()
    start = (today.replace(day=1) - dt.timedelta(days=125)).replace(day=1)
    ce = session.client("ce", region_name="us-east-1").get_cost_and_usage(
        TimePeriod={"Start": start.isoformat(), "End": today.isoformat()},
        Granularity="MONTHLY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )
    months = []
    for period in ce["ResultsByTime"]:
        rows = {
            g["Keys"][0]: round(float(g["Metrics"]["UnblendedCost"]["Amount"]), 4)
            for g in period["Groups"]
        }
        months.append(
            {
                "start": period["TimePeriod"]["Start"],
                "end": period["TimePeriod"]["End"],
                "ecr": rows.get("Amazon EC2 Container Registry (ECR)"),
                "lightsail": rows.get("Amazon Lightsail"),
                "nonzero": {k: v for k, v in sorted(rows.items(), key=lambda x: -x[1]) if v},
            }
        )
    (EVIDENCE / "cost-explorer.json").write_text(
        json.dumps(
            {
                "captured_utc": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "metric": "UnblendedCost",
                "note": "list-price charges as billed; months after 2026-07 carry offsetting negative rows",
                "months": months,
            },
            indent=2,
        )
        + "\n"
    )
    july = next((m for m in months if m["start"] == "2026-07-01"), None)
    lines = [
        "# Figures derived from the artifacts beside this file.",
        "# Each is arithmetic on a measurement, not a separate measurement.",
        f"# written {NOW.strftime('%Y-%m-%dT%H:%M:%SZ')} by gather-evidence.py",
        "",
        "## July 2026 invoice lines, from cost-explorer.json, rounded to cents",
    ]
    if july:
        for service, amount in sorted(july["nonzero"].items(), key=lambda x: -x[1]):
            lines.append(f"{amount:>12.2f}   {service}")
    lines += [
        "",
        "## Lightsail nano container node, hourly rate x hours per month",
        "# hourly rate 0.0094086020 USD from the AWS Price List API,",
        "# group 'Lightsail Container', usagetype USE1-ContainerSvcUsage:Nano-0.25CPU-0.5GB",
        f"0.0094086020 x 744 = {0.0094086020 * 744:.2f}   (Lightsail's 744-hour month)",
        f"0.0094086020 x 730 = {0.0094086020 * 730:.2f}   (the price table's 730-hour month)",
        "",
        "## Orphaned snapshot, us-east-1",
        f"80 GiB x 0.05 USD/GB-month = {80 * 0.05:.2f}",
        "",
        "## ECR flagged repositories",
    ]
    flagged = [
        r for r in inventory["ecr"]["repos"]
        if r["images"] > 0 and (r["newest_push"] or "") < "2026-06-23"
    ]
    gib = sum(r["bytes"] for r in flagged) / 1024**3
    lines += [
        f"repositories flagged: {len(flagged)} of {inventory['ecr']['repo_count']}",
        f"flagged bytes: {sum(r['bytes'] for r in flagged)}",
        f"flagged GiB: {gib:.3f}",
        f"{gib:.3f} x 0.10 USD/GB-month = {gib * 0.10:.2f}",
        "",
    ]
    (EVIDENCE / "derived-figures.txt").write_text("\n".join(lines) + "\n")

    print(f"ECR: {inventory['ecr']['repo_count']} repos, {inventory['ecr']['total_gib']} GiB")
    print(f"wrote {EVIDENCE}/aws-inventory.json and cost-explorer.json")


if __name__ == "__main__":
    main()
