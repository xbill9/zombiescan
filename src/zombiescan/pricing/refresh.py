"""Regenerate ``table.json`` from the AWS Price List API.

Run this, not your memory, when a price looks wrong:

    uv run python -m zombiescan.pricing.refresh

Needs ``pricing:GetProducts``. The API is only served from us-east-1 and
ap-south-1, so the client is pinned to us-east-1 regardless of your default
region. Prices are on-demand USD list prices: they ignore private pricing,
savings plans, and credits.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
from typing import Any

import boto3
import botocore.exceptions

VOLUME_TYPES = ["gp3", "gp2", "io1", "io2", "st1", "sc1", "standard"]
TABLE_PATH = pathlib.Path(__file__).with_name("table.json")

# The Price List API does not expose the standard public IPv4 charge. The
# "IP Address" product family under AmazonEC2 is entirely Wavelength CarrierIP,
# and AmazonVPC has no matching family. AWS publishes this rate as a flat
# $0.005/hour for every public IPv4 address in every commercial region, so it
# is recorded here as a constant and flagged in the table's metadata rather
# than guessed per-region.
PUBLIC_IPV4_HOURLY_USD = 0.005
PUBLIC_IPV4_SOURCE = (
    "https://aws.amazon.com/vpc/pricing/ -- flat rate, not available via the Price List API"
)


def _paginate(client: Any, **kwargs: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    token = None
    while True:
        page = client.get_products(**kwargs, **({"NextToken": token} if token else {}))
        out.extend(json.loads(s) for s in page["PriceList"])
        token = page.get("NextToken")
        if not token:
            return out


def _usd_rate(entry: dict[str, Any], unit: str) -> float | None:
    """Highest positive USD rate in this entry for the given unit, or None.

    An entry can carry several price dimensions, and taking the first one is
    wrong twice over. Volume-tiered products (S3 storage) list every tier, and
    the order is not guaranteed. Products with a free allowance (DynamoDB
    provisioned capacity) list a $0.00 dimension beside the real rate, and
    reading that one makes the resource look free -- or, with a `price <= 0`
    guard, drops the region from the table entirely.

    The highest rate is what an account pays before tiers and allowances kick
    in, which is the right basis for "what is this costing you".
    """
    rates = [
        float(usd)
        for term in entry.get("terms", {}).get("OnDemand", {}).values()
        for dim in term.get("priceDimensions", {}).values()
        # China regions price in CNY and have no USD dimension. Skip them
        # rather than crashing or silently treating CNY as dollars.
        if dim.get("unit") == unit and (usd := dim.get("pricePerUnit", {}).get("USD")) is not None
    ]
    positive = [r for r in rates if r > 0]
    return max(positive) if positive else None


def fetch_ebs_volumes(client: Any) -> dict[str, dict[str, float]]:
    """{region: {volume_type: usd_per_gb_month}}"""
    table: dict[str, dict[str, float]] = {}
    for vol_type in VOLUME_TYPES:
        entries = _paginate(
            client,
            ServiceCode="AmazonEC2",
            Filters=[
                {"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Storage"},
                {"Type": "TERM_MATCH", "Field": "volumeApiName", "Value": vol_type},
            ],
        )
        for entry in entries:
            attrs = entry["product"]["attributes"]
            region = attrs.get("regionCode")
            price = _usd_rate(entry, "GB-Mo")
            if not region or price is None:
                continue
            table.setdefault(region, {})[vol_type] = price
    return table


def fetch_snapshots(client: Any) -> dict[str, float]:
    """{region: usd_per_gb_month} for standard-tier EBS snapshots."""
    entries = _paginate(
        client,
        ServiceCode="AmazonEC2",
        Filters=[
            {"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Storage Snapshot"},
        ],
    )
    table: dict[str, float] = {}
    for entry in entries:
        attrs = entry["product"]["attributes"]
        region = attrs.get("regionCode")
        # Archive-tier snapshots are priced separately; keep the standard tier.
        if not region or "Archive" in attrs.get("usagetype", ""):
            continue
        price = _usd_rate(entry, "GB-Mo")
        if price is None:
            continue
        table[region] = price
    return table


def fetch_nat_gateway_hours(client: Any) -> dict[str, float]:
    """{region: usd_per_hour} for NAT gateway uptime (not data processing)."""
    entries = _paginate(
        client,
        ServiceCode="AmazonEC2",
        Filters=[{"Type": "TERM_MATCH", "Field": "productFamily", "Value": "NAT Gateway"}],
    )
    table: dict[str, float] = {}
    for entry in entries:
        attrs = entry["product"]["attributes"]
        region = attrs.get("regionCode")
        usagetype = attrs.get("usagetype", "")
        # Several dimensions share this family: per-GB data processing and
        # provisioned-bandwidth charges among them. We want plain uptime hours.
        if not region or "Bytes" in usagetype or "Prvd" in usagetype:
            continue
        price = _usd_rate(entry, "Hrs")
        if price is None:
            continue
        table[region] = price
    return table


def fetch_load_balancer_hours(client: Any) -> dict[str, dict[str, float]]:
    """{region: {alb|nlb: usd_per_hour}} for load balancer uptime.

    Excludes LCU charges, which scale with traffic -- an idle load balancer
    by definition is not accruing them.
    """
    table: dict[str, dict[str, float]] = {}
    for family, key in [("Load Balancer-Application", "alb"), ("Load Balancer-Network", "nlb")]:
        entries = _paginate(
            client,
            ServiceCode="AmazonEC2",
            Filters=[{"Type": "TERM_MATCH", "Field": "productFamily", "Value": family}],
        )
        for entry in entries:
            attrs = entry["product"]["attributes"]
            region = attrs.get("regionCode")
            if not region or "LCU" in attrs.get("usagetype", ""):
                continue
            price = _usd_rate(entry, "Hrs")
            if price is None:
                continue
            table.setdefault(region, {})[key] = price
    return table


def fetch_log_storage(client: Any) -> dict[str, float]:
    """{region: usd_per_gb_month} for standard-class CloudWatch Logs storage."""
    entries = _paginate(
        client,
        ServiceCode="AmazonCloudWatch",
        Filters=[{"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Storage Snapshot"}],
    )
    table: dict[str, float] = {}
    for entry in entries:
        attrs = entry["product"]["attributes"]
        region = attrs.get("regionCode")
        usagetype = attrs.get("usagetype", "")
        # Infrequent Access (-IA-) and Archive (-AIA-) tiers are cheaper and
        # priced separately. A log group with no retention is Standard class.
        if not region or not usagetype.endswith("TimedStorage-ByteHrs"):
            continue
        if "-IA-" in usagetype or "-AIA-" in usagetype:
            continue
        price = _usd_rate(entry, "GB-Mo")
        if price is None:
            continue
        table[region] = price
    return table


def fetch_vpc_endpoint_hours(client: Any) -> dict[str, float]:
    """{region: usd_per_hour} per interface VPC endpoint ENI.

    Gateway endpoints (S3, DynamoDB) are free and have no entry here.
    """
    entries = _paginate(
        client,
        ServiceCode="AmazonVPC",
        Filters=[{"Type": "TERM_MATCH", "Field": "productFamily", "Value": "VpcEndpoint"}],
    )
    table: dict[str, float] = {}
    for entry in entries:
        attrs = entry["product"]["attributes"]
        region = attrs.get("regionCode")
        # Several dimensions share this family: data processing, Gateway Load
        # Balancer endpoints, tunnel and resource endpoints. Plain interface
        # endpoint uptime is the one ending VpcEndpoint-Hours.
        if not region or not attrs.get("usagetype", "").endswith("VpcEndpoint-Hours"):
            continue
        price = _usd_rate(entry, "Hrs")
        if price is None:
            continue
        table[region] = price
    return table


def fetch_classic_lb_hours(client: Any) -> dict[str, float]:
    """{region: usd_per_hour} for Classic (ELBv1) load balancers."""
    entries = _paginate(
        client,
        ServiceCode="AmazonEC2",
        Filters=[{"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Load Balancer"}],
    )
    table: dict[str, float] = {}
    for entry in entries:
        attrs = entry["product"]["attributes"]
        region = attrs.get("regionCode")
        if not region or "LoadBalancerUsage" not in attrs.get("usagetype", ""):
            continue
        price = _usd_rate(entry, "Hrs")
        if price is None:
            continue
        table[region] = price
    return table


# RDS reports storage as gp2/gp3/io1/io2/standard; the price list describes the
# same media in prose. Prices are consistent across engines for a given
# volume type and deployment, so engine is not part of the key.
_RDS_VOLUME_KEYS = {
    "General Purpose": "gp2",
    "General Purpose-GP3": "gp3",
    "Provisioned IOPS": "io1",
    "Provisioned IOPS (SSD)": "io1",
    "Provisioned IOPS-IO2": "io2",
    "Magnetic": "standard",
}


def fetch_rds_storage(client: Any) -> dict[str, dict[str, dict[str, float]]]:
    """{region: {single|multi: {gp2|gp3|io1|io2|standard: usd_per_gb_month}}}"""
    table: dict[str, dict[str, dict[str, float]]] = {}
    entries = _paginate(
        client,
        ServiceCode="AmazonRDS",
        Filters=[{"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Database Storage"}],
    )
    for entry in entries:
        attrs = entry["product"]["attributes"]
        region = attrs.get("regionCode")
        key = _RDS_VOLUME_KEYS.get(attrs.get("volumeType", ""))
        deployment = attrs.get("deploymentOption", "")
        if not region or not key or not deployment:
            continue
        # "Multi-AZ", "Multi-AZ (SQL Server)", "Multi-AZ (readable standbys)"
        # all bill at the Multi-AZ rate.
        az_key = "multi" if deployment.startswith("Multi-AZ") else "single"
        price = _usd_rate(entry, "GB-Mo")
        if price is None:
            continue
        table.setdefault(region, {}).setdefault(az_key, {}).setdefault(key, price)
    return table


def _flat_rate_by_region(client: Any, service: str, family: str, unit: str) -> dict[str, float]:
    """{region: usd} for a service billed at one flat rate per thing per month."""
    entries = _paginate(
        client,
        ServiceCode=service,
        Filters=[{"Type": "TERM_MATCH", "Field": "productFamily", "Value": family}],
    )
    table: dict[str, float] = {}
    for entry in entries:
        region = entry["product"]["attributes"].get("regionCode")
        price = _usd_rate(entry, unit)
        if not region or price is None:
            continue
        table[region] = price
    return table


def fetch_kms_keys(client: Any) -> dict[str, float]:
    """{region: usd_per_key_month} for customer managed KMS keys."""
    return _flat_rate_by_region(client, "awskms", "Encryption Key", "Keys")


def fetch_secrets(client: Any) -> dict[str, float]:
    """{region: usd_per_secret_month} for Secrets Manager secrets."""
    return _flat_rate_by_region(client, "AWSSecretsManager", "Secret", "Secrets")


def fetch_ecr_storage(client: Any) -> dict[str, float]:
    """{region: usd_per_gb_month} for ECR image storage.

    The "EC2 Container Registry" family also prices archive retrieval (per GB)
    and image signing (per Count), so the GB-Mo unit is what picks out the
    TimedStorage-ByteHrs rate rather than one of those.
    """
    return _flat_rate_by_region(client, "AmazonECR", "EC2 Container Registry", "GB-Mo")


def fetch_efs_storage(client: Any) -> dict[str, float]:
    """{region: usd_per_gb_month} for EFS Standard regional storage."""
    entries = _paginate(
        client,
        ServiceCode="AmazonEFS",
        Filters=[{"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Storage"}],
    )
    table: dict[str, float] = {}
    for entry in entries:
        attrs = entry["product"]["attributes"]
        region = attrs.get("regionCode")
        usagetype = attrs.get("usagetype", "")
        # Infrequent Access, Archive, One Zone (-Z-) and Elastic Throughput
        # variants are all priced separately. Standard regional storage is the
        # plain TimedStorage-ByteHrs entry.
        if not region or not usagetype.endswith("TimedStorage-ByteHrs"):
            continue
        if any(marker in usagetype for marker in ("IA", "Archive", "-Z-", "ET")):
            continue
        price = _usd_rate(entry, "GB-Mo")
        if price is None:
            continue
        table[region] = price
    return table


def fetch_s3_storage(client: Any) -> dict[str, float]:
    """{region: usd_per_gb_month} for S3 Standard.

    S3 storage is tiered by monthly volume. The first tier is what an account
    with modest storage actually pays, and taking the highest published rate
    keeps the estimate from flattering itself.
    """
    entries = _paginate(
        client,
        ServiceCode="AmazonS3",
        Filters=[
            {"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Storage"},
            {"Type": "TERM_MATCH", "Field": "storageClass", "Value": "General Purpose"},
        ],
    )
    table: dict[str, float] = {}
    for entry in entries:
        attrs = entry["product"]["attributes"]
        region = attrs.get("regionCode")
        if not region or attrs.get("usagetype", "").endswith("Annotation-TimedStorage-ByteHrs"):
            continue
        if not attrs.get("usagetype", "").endswith("TimedStorage-ByteHrs"):
            continue
        price = _usd_rate(entry, "GB-Mo")
        if price is None:
            continue
        table[region] = max(table.get(region, 0.0), price)
    return table


def fetch_rds_snapshot_storage(client: Any) -> dict[str, float]:
    """{region: usd_per_gb_month} for RDS backup and snapshot storage."""
    entries = _paginate(
        client,
        ServiceCode="AmazonRDS",
        Filters=[{"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Storage Snapshot"}],
    )
    table: dict[str, float] = {}
    for entry in entries:
        attrs = entry["product"]["attributes"]
        region = attrs.get("regionCode")
        usagetype = attrs.get("usagetype", "")
        # RDS Custom is a separate product with its own snapshot billing.
        if not region or "RDSCustom" in usagetype:
            continue
        price = _usd_rate(entry, "GB-Mo")
        if price is None:
            continue
        table[region] = price
    return table


def fetch_dynamodb_capacity(client: Any) -> dict[str, dict[str, float]]:
    """{region: {read|write: usd_per_capacity_unit_hour}} for provisioned tables."""
    entries = _paginate(
        client,
        ServiceCode="AmazonDynamoDB",
        Filters=[{"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Provisioned IOPS"}],
    )
    table: dict[str, dict[str, float]] = {}
    for entry in entries:
        attrs = entry["product"]["attributes"]
        region = attrs.get("regionCode")
        usagetype = attrs.get("usagetype", "")
        # Standard-IA tables are priced separately; the free-tier rows are $0.
        if not region or "IA-" in usagetype:
            continue
        # The unit name differs per direction, and each entry carries a $0.00
        # free-allowance dimension beside the real rate.
        if usagetype.endswith("ReadCapacityUnit-Hrs"):
            key, unit = "read", "ReadCapacityUnit-Hrs"
        elif usagetype.endswith("WriteCapacityUnit-Hrs"):
            key, unit = "write", "WriteCapacityUnit-Hrs"
        else:
            continue
        price = _usd_rate(entry, unit)
        if price is None:
            continue
        table.setdefault(region, {})[key] = price
    return table


# Lightsail products leave ``productFamily`` null and carry the meaningful
# grouping in ``group`` instead, so these are bucketed by that attribute.
LIGHTSAIL_GROUPS = {
    "Lightsail Block Storage": ("lightsail_disk_gb_month", "GB-Mo"),
    "Lightsail unused static IP": ("lightsail_static_ip_hour", "Hrs"),
    "Lightsail Load Balancer": ("lightsail_load_balancer_hour", "Hrs"),
    "Lightsail Snapshot": ("lightsail_snapshot_gb_month", "GB-Mo"),
    "Lightsail Instance Snapshot": ("lightsail_snapshot_gb_month", "GB-Mo"),
}


def fetch_lightsail_rates(client: Any) -> dict[str, dict[str, float]]:
    """Per-region Lightsail commodity rates, in one pass over the service.

    Six groups are wanted and the service is small, so bucketing a single
    listing beats filtering it once per group.
    """
    table: dict[str, dict[str, float]] = {key: {} for key, _ in LIGHTSAIL_GROUPS.values()}
    for entry in _paginate(client, ServiceCode="AmazonLightsail"):
        attrs = entry["product"]["attributes"]
        group = attrs.get("group")
        if group not in LIGHTSAIL_GROUPS:
            continue
        key, unit = LIGHTSAIL_GROUPS[group]
        region = attrs.get("regionCode")
        price = _usd_rate(entry, unit)
        if not region or price is None:
            continue
        # Instance and disk snapshots share a rate under two group names.
        table[key][region] = max(table[key].get(region, 0.0), price)
    return table


def fetch_lightsail_bundles(session: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """{region: {bundle_id: usd_month}}, {region: {power: usd_month}} from Lightsail.

    Deliberately *not* the Price List API. Lightsail's own GetBundles returns a
    monthly price keyed by the exact ``bundleId`` that GetInstances reports,
    and GetContainerServicePowers does the same for container power names. The
    Price List carries the same rates keyed by a usagetype string
    ("USE1-BundleUsage:1GB") that would have to be mapped back to a bundle id
    by guesswork -- and a wrong mapping silently prices the wrong machine.

    It also prices per month directly, so it does not inherit the 730-hour
    assumption: Lightsail bills containers over a 744-hour month, and
    multiplying its hourly rate by 730 understates every one of them.
    """
    probe = session.client("lightsail", region_name="us-east-1")
    regions = [r["name"] for r in probe.get_regions()["regions"] if r.get("name")]

    bundles: dict[str, Any] = {}
    powers: dict[str, Any] = {}
    for region in sorted(regions):
        client = session.client("lightsail", region_name=region)
        try:
            found = {}
            for page in client.get_paginator("get_bundles").paginate():
                for bundle in page.get("bundles", []):
                    if bundle.get("isActive") and bundle.get("bundleId"):
                        found[bundle["bundleId"]] = float(bundle.get("price", 0.0))
            if found:
                bundles[region] = dict(sorted(found.items()))

            service_powers = {
                power["name"]: float(power.get("price", 0.0))
                for power in client.get_container_service_powers().get("powers", [])
                if power.get("name")
            }
            if service_powers:
                powers[region] = dict(sorted(service_powers.items()))
        except botocore.exceptions.BotoCoreError:
            # A region Lightsail does not serve has no endpoint at all.
            continue
        except botocore.exceptions.ClientError:
            continue
    return bundles, powers


def fetch_route53_health_checks(client: Any) -> dict[str, float]:
    """{aws|non_aws: usd_per_month}. Route 53 is global, so there is no region."""
    entries = _paginate(
        client,
        ServiceCode="AmazonRoute53",
        Filters=[{"Type": "TERM_MATCH", "Field": "productFamily", "Value": "DNS Health Check"}],
    )
    table: dict[str, float] = {}
    for entry in entries:
        usagetype = entry["product"]["attributes"].get("usagetype", "")
        # "Option" entries are per optional feature (latency, string matching),
        # not per health check.
        if "Option" in usagetype:
            continue
        price = _usd_rate(entry, "Mo")
        if price is None:
            continue
        if usagetype.endswith("Health-Check-Non-AWS"):
            table["non_aws"] = price
        elif usagetype.endswith("Health-Check-AWS"):
            table["aws"] = price
    return table


def main() -> None:
    client = boto3.Session().client("pricing", region_name="us-east-1")
    print("fetching EBS volume prices...")
    volumes = fetch_ebs_volumes(client)
    print(f"  {len(volumes)} regions")
    print("fetching snapshot prices...")
    snapshots = fetch_snapshots(client)
    print(f"  {len(snapshots)} regions")
    print("fetching NAT gateway prices...")
    nat = fetch_nat_gateway_hours(client)
    print(f"  {len(nat)} regions")
    print("fetching load balancer prices...")
    load_balancers = fetch_load_balancer_hours(client)
    print(f"  {len(load_balancers)} regions")
    print("fetching CloudWatch Logs storage prices...")
    logs = fetch_log_storage(client)
    print(f"  {len(logs)} regions")
    print("fetching VPC endpoint prices...")
    endpoints = fetch_vpc_endpoint_hours(client)
    print(f"  {len(endpoints)} regions")
    print("fetching Classic load balancer prices...")
    classic = fetch_classic_lb_hours(client)
    print(f"  {len(classic)} regions")
    print("fetching RDS storage prices...")
    rds = fetch_rds_storage(client)
    print(f"  {len(rds)} regions")
    print("fetching KMS key prices...")
    kms = fetch_kms_keys(client)
    print(f"  {len(kms)} regions")
    print("fetching Secrets Manager prices...")
    secrets = fetch_secrets(client)
    print(f"  {len(secrets)} regions")
    print("fetching ECR storage prices...")
    ecr = fetch_ecr_storage(client)
    print(f"  {len(ecr)} regions")
    print("fetching EFS storage prices...")
    efs = fetch_efs_storage(client)
    print(f"  {len(efs)} regions")
    print("fetching S3 storage prices...")
    s3 = fetch_s3_storage(client)
    print(f"  {len(s3)} regions")
    print("fetching RDS snapshot prices...")
    rds_snap = fetch_rds_snapshot_storage(client)
    print(f"  {len(rds_snap)} regions")
    print("fetching DynamoDB capacity prices...")
    ddb = fetch_dynamodb_capacity(client)
    print(f"  {len(ddb)} regions")
    print("fetching Lightsail commodity rates...")
    lightsail = fetch_lightsail_rates(client)
    for key, values in lightsail.items():
        print(f"  {key}: {len(values)} regions")
    print("fetching Lightsail bundles and container powers (Lightsail API)...")
    ls_bundles, ls_powers = fetch_lightsail_bundles(boto3.Session())
    print(f"  {len(ls_bundles)} bundle regions, {len(ls_powers)} container-power regions")
    print("fetching Route 53 health check prices...")
    r53 = fetch_route53_health_checks(client)
    print(f"  {len(r53)} rates (global)")

    payload = {
        "_meta": {
            "source": "AWS Price List API (pricing:GetProducts), on-demand USD list prices",
            "generated": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "note": "Regenerate with: uv run python -m zombiescan.pricing.refresh",
        },
        "fallback_region": "us-east-1",
        "hours_per_month": 730,
        "public_ipv4_hour": {
            "_value": PUBLIC_IPV4_HOURLY_USD,
            "_source": PUBLIC_IPV4_SOURCE,
        },
        "ebs_gb_month": dict(sorted(volumes.items())),
        "snapshot_gb_month": dict(sorted(snapshots.items())),
        "nat_gateway_hour": dict(sorted(nat.items())),
        "load_balancer_hour": dict(sorted(load_balancers.items())),
        "log_storage_gb_month": dict(sorted(logs.items())),
        "vpc_endpoint_hour": dict(sorted(endpoints.items())),
        "classic_lb_hour": dict(sorted(classic.items())),
        "rds_storage_gb_month": dict(sorted(rds.items())),
        "kms_key_month": dict(sorted(kms.items())),
        "secret_month": dict(sorted(secrets.items())),
        "ecr_gb_month": dict(sorted(ecr.items())),
        "efs_gb_month": dict(sorted(efs.items())),
        "s3_gb_month": dict(sorted(s3.items())),
        "rds_snapshot_gb_month": dict(sorted(rds_snap.items())),
        "dynamodb_capacity_hour": dict(sorted(ddb.items())),
        "lightsail_bundle_month": dict(sorted(ls_bundles.items())),
        "lightsail_container_power_month": dict(sorted(ls_powers.items())),
        **{key: dict(sorted(values.items())) for key, values in sorted(lightsail.items())},
        "route53_health_check_month": r53,
    }
    TABLE_PATH.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")
    print(f"wrote {TABLE_PATH}")


if __name__ == "__main__":
    main()
