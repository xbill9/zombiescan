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


def _usd_per_unit(entry: dict[str, Any]) -> tuple[str, float] | None:
    """Pull the single on-demand price dimension out of a price list entry."""
    for term in entry.get("terms", {}).get("OnDemand", {}).values():
        for dim in term.get("priceDimensions", {}).values():
            # China regions price in CNY and have no USD dimension. Skip them
            # rather than crashing or silently treating CNY as dollars.
            usd = dim.get("pricePerUnit", {}).get("USD")
            if usd is None:
                continue
            return dim["unit"], float(usd)
    return None


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
            priced = _usd_per_unit(entry)
            if not region or not priced:
                continue
            unit, price = priced
            if unit != "GB-Mo" or price <= 0:
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
        priced = _usd_per_unit(entry)
        if not priced:
            continue
        unit, price = priced
        if unit != "GB-Mo" or price <= 0:
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
        priced = _usd_per_unit(entry)
        if not priced:
            continue
        unit, price = priced
        if unit != "Hrs" or price <= 0:
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
            priced = _usd_per_unit(entry)
            if not priced:
                continue
            unit, price = priced
            if unit != "Hrs" or price <= 0:
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
        priced = _usd_per_unit(entry)
        if not priced:
            continue
        unit, price = priced
        if unit != "GB-Mo" or price <= 0:
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
        priced = _usd_per_unit(entry)
        if not priced:
            continue
        unit, price = priced
        if unit != "Hrs" or price <= 0:
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
        priced = _usd_per_unit(entry)
        if not priced:
            continue
        unit, price = priced
        if unit != "Hrs" or price <= 0:
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
        priced = _usd_per_unit(entry)
        if not priced:
            continue
        unit, price = priced
        if unit != "GB-Mo" or price <= 0:
            continue
        table.setdefault(region, {}).setdefault(az_key, {}).setdefault(key, price)
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
    }
    TABLE_PATH.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")
    print(f"wrote {TABLE_PATH}")


if __name__ == "__main__":
    main()
