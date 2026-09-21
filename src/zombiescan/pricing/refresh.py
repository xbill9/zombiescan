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
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import boto3


@dataclass
class RefreshContext:
    """What a fetcher is handed: a session, and a Price List client.

    The Price List API is served only from us-east-1 and ap-south-1, so
    ``pricing`` is pinned regardless of the caller's default region. A fetcher
    whose rates come from somewhere else -- Lightsail's own API, say -- uses
    ``session`` and ignores ``pricing`` entirely.
    """

    session: Any
    pricing: Any


@dataclass(frozen=True)
class Fetcher:
    """One registered source of price-table sections."""

    sections: tuple[str, ...]
    fn: Callable[[RefreshContext], dict[str, Any]]
    label: str
    pack: str = "core"


FETCHERS: list[Fetcher] = []


def price_fetcher(
    *sections: str, label: str, pack: str = "core"
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a fetcher for one or more table sections.

    The function returns ``{section: data}``. Declaring the sections up front
    lets ``main`` report what a pack contributed, and lets a refresh that skips
    a pack leave that pack's existing rates in the table untouched rather than
    dropping them.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        claimed = {s for f in FETCHERS for s in f.sections} & set(sections)
        if claimed:
            raise ValueError(f"sections already claimed by another fetcher: {sorted(claimed)}")
        FETCHERS.append(Fetcher(tuple(sections), fn, label, pack))
        return fn

    return decorator


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


def paginate(client: Any, **kwargs: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    token = None
    while True:
        page = client.get_products(**kwargs, **({"NextToken": token} if token else {}))
        out.extend(json.loads(s) for s in page["PriceList"])
        token = page.get("NextToken")
        if not token:
            return out


def usd_rate(entry: dict[str, Any], unit: str) -> float | None:
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
        entries = paginate(
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
            price = usd_rate(entry, "GB-Mo")
            if not region or price is None:
                continue
            table.setdefault(region, {})[vol_type] = price
    return table


def fetch_snapshots(client: Any) -> dict[str, float]:
    """{region: usd_per_gb_month} for standard-tier EBS snapshots."""
    entries = paginate(
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
        price = usd_rate(entry, "GB-Mo")
        if price is None:
            continue
        table[region] = price
    return table


def fetch_nat_gateway_hours(client: Any) -> dict[str, float]:
    """{region: usd_per_hour} for NAT gateway uptime (not data processing)."""
    entries = paginate(
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
        price = usd_rate(entry, "Hrs")
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
        entries = paginate(
            client,
            ServiceCode="AmazonEC2",
            Filters=[{"Type": "TERM_MATCH", "Field": "productFamily", "Value": family}],
        )
        for entry in entries:
            attrs = entry["product"]["attributes"]
            region = attrs.get("regionCode")
            if not region or "LCU" in attrs.get("usagetype", ""):
                continue
            price = usd_rate(entry, "Hrs")
            if price is None:
                continue
            table.setdefault(region, {})[key] = price
    return table


def fetch_log_storage(client: Any) -> dict[str, float]:
    """{region: usd_per_gb_month} for standard-class CloudWatch Logs storage."""
    entries = paginate(
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
        price = usd_rate(entry, "GB-Mo")
        if price is None:
            continue
        table[region] = price
    return table


def fetch_vpc_endpoint_hours(client: Any) -> dict[str, float]:
    """{region: usd_per_hour} per interface VPC endpoint ENI.

    Gateway endpoints (S3, DynamoDB) are free and have no entry here.
    """
    entries = paginate(
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
        price = usd_rate(entry, "Hrs")
        if price is None:
            continue
        table[region] = price
    return table


def fetch_classic_lb_hours(client: Any) -> dict[str, float]:
    """{region: usd_per_hour} for Classic (ELBv1) load balancers."""
    entries = paginate(
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
        price = usd_rate(entry, "Hrs")
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
    entries = paginate(
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
        price = usd_rate(entry, "GB-Mo")
        if price is None:
            continue
        table.setdefault(region, {}).setdefault(az_key, {}).setdefault(key, price)
    return table


def _flat_rate_by_region(client: Any, service: str, family: str, unit: str) -> dict[str, float]:
    """{region: usd} for a service billed at one flat rate per thing per month."""
    entries = paginate(
        client,
        ServiceCode=service,
        Filters=[{"Type": "TERM_MATCH", "Field": "productFamily", "Value": family}],
    )
    table: dict[str, float] = {}
    for entry in entries:
        region = entry["product"]["attributes"].get("regionCode")
        price = usd_rate(entry, unit)
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
    entries = paginate(
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
        price = usd_rate(entry, "GB-Mo")
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
    entries = paginate(
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
        price = usd_rate(entry, "GB-Mo")
        if price is None:
            continue
        table[region] = max(table.get(region, 0.0), price)
    return table


def fetch_rds_snapshot_storage(client: Any) -> dict[str, float]:
    """{region: usd_per_gb_month} for RDS backup and snapshot storage."""
    entries = paginate(
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
        price = usd_rate(entry, "GB-Mo")
        if price is None:
            continue
        table[region] = price
    return table


def fetch_dynamodb_capacity(client: Any) -> dict[str, dict[str, float]]:
    """{region: {read|write: usd_per_capacity_unit_hour}} for provisioned tables."""
    entries = paginate(
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
        price = usd_rate(entry, unit)
        if price is None:
            continue
        table.setdefault(region, {})[key] = price
    return table


# Lightsail products leave ``productFamily`` null and carry the meaningful
# grouping in ``group`` instead, so these are bucketed by that attribute.


def fetch_route53_health_checks(client: Any) -> dict[str, float]:
    """{aws|non_aws: usd_per_month}. Route 53 is global, so there is no region."""
    entries = paginate(
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
        price = usd_rate(entry, "Mo")
        if price is None:
            continue
        if usagetype.endswith("Health-Check-Non-AWS"):
            table["non_aws"] = price
        elif usagetype.endswith("Health-Check-AWS"):
            table["aws"] = price
    return table


def _register_core_fetchers() -> None:
    """Register the sections core knows how to fetch.

    A loop rather than a decorator on each function, because every one of these
    has the same shape: hand it the Price List client, get back one section.
    Anything that does not have that shape -- Lightsail -- registers itself.
    """
    simple: list[tuple[str, Any, str]] = [
        ("ebs_gb_month", fetch_ebs_volumes, "EBS volume"),
        ("snapshot_gb_month", fetch_snapshots, "snapshot"),
        ("nat_gateway_hour", fetch_nat_gateway_hours, "NAT gateway"),
        ("load_balancer_hour", fetch_load_balancer_hours, "load balancer"),
        ("log_storage_gb_month", fetch_log_storage, "CloudWatch Logs storage"),
        ("vpc_endpoint_hour", fetch_vpc_endpoint_hours, "VPC endpoint"),
        ("classic_lb_hour", fetch_classic_lb_hours, "Classic load balancer"),
        ("rds_storage_gb_month", fetch_rds_storage, "RDS storage"),
        ("kms_key_month", fetch_kms_keys, "KMS key"),
        ("secret_month", fetch_secrets, "Secrets Manager"),
        ("ecr_gb_month", fetch_ecr_storage, "ECR storage"),
        ("efs_gb_month", fetch_efs_storage, "EFS storage"),
        ("s3_gb_month", fetch_s3_storage, "S3 storage"),
        ("rds_snapshot_gb_month", fetch_rds_snapshot_storage, "RDS snapshot"),
        ("dynamodb_capacity_hour", fetch_dynamodb_capacity, "DynamoDB capacity"),
        ("route53_health_check_month", fetch_route53_health_checks, "Route 53 health check"),
    ]
    for section, fn, label in simple:
        price_fetcher(section, label=f"{label} prices")(
            lambda ctx, fn=fn, section=section: {section: fn(ctx.pricing)}
        )

    # Not a fetch at all: a documented constant the Price List does not carry.
    # Registered anyway so the table is assembled in exactly one place.
    price_fetcher("public_ipv4_hour", label="public IPv4 rate (constant)")(
        lambda ctx: {
            "public_ipv4_hour": {
                "_value": PUBLIC_IPV4_HOURLY_USD,
                "_source": PUBLIC_IPV4_SOURCE,
            }
        }
    )


_register_core_fetchers()


def build_table(ctx: RefreshContext) -> dict[str, Any]:
    """Run every registered fetcher and assemble the table."""
    payload: dict[str, Any] = {
        "_meta": {
            "source": "AWS Price List API (pricing:GetProducts), on-demand USD list prices",
            "generated": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "note": "Regenerate with: python -m zombiescan.pricing.refresh",
            "packs": sorted({f.pack for f in FETCHERS}),
        },
        "fallback_region": "us-east-1",
        "hours_per_month": 730,
    }
    for fetcher in FETCHERS:
        print(f"fetching {fetcher.label}...")
        produced = fetcher.fn(ctx)
        unexpected = set(produced) - set(fetcher.sections)
        if unexpected:
            # A fetcher writing sections it never declared would silently
            # overwrite another pack's rates.
            raise ValueError(
                f"fetcher {fetcher.label!r} produced undeclared sections: {sorted(unexpected)}"
            )
        for section, data in produced.items():
            payload[section] = dict(sorted(data.items())) if isinstance(data, dict) else data
            print(f"  {section}: {len(data)} entries")
    return payload


def regressions(payload: dict[str, Any], existing: dict[str, Any]) -> list[str]:
    """Sections the new table would lose against the one already on disk.

    A fetcher that returns nothing is not a price of zero, it is a refresh
    that failed, and writing its result would silently make every finding it
    prices free. Checked against the previous table rather than against a
    hardcoded list, so a pack's sections are protected the moment it ships one.
    """
    problems = []
    for section, previous in sorted(existing.items()):
        if section.startswith("_") or not isinstance(previous, dict) or not previous:
            continue
        fresh = payload.get(section)
        if fresh is None:
            problems.append(f"{section}: gone (had {len(previous)} entries)")
        elif isinstance(fresh, dict) and not fresh:
            problems.append(f"{section}: empty (had {len(previous)} entries)")
    return problems


def main() -> None:
    # Pack fetchers only exist once their packs are imported.
    from zombiescan import packs

    packs.discover()

    session = boto3.Session()
    ctx = RefreshContext(
        session=session, pricing=session.client("pricing", region_name="us-east-1")
    )
    payload = build_table(ctx)

    existing = json.loads(TABLE_PATH.read_text()) if TABLE_PATH.exists() else {}
    problems = regressions(payload, existing)
    if problems:
        raise SystemExit(
            "refusing to write the price table -- this refresh would lose rates:\n  "
            + "\n  ".join(problems)
            + "\nThe table on disk is unchanged. A section that vanishes usually means "
            "its fetcher never registered, not that AWS stopped charging for it."
        )

    TABLE_PATH.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")
    print(f"wrote {TABLE_PATH}")


if __name__ == "__main__":
    # `python -m zombiescan.pricing.refresh` executes this file a second time,
    # as __main__, with a FETCHERS list of its own. Packs register into the
    # canonical zombiescan.pricing.refresh copy, so running __main__'s own
    # main() would fetch core's sections and none of any pack's. Hand over to
    # the canonical module instead.
    from zombiescan.pricing.refresh import main as canonical_main

    canonical_main()
