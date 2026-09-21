"""Where Lightsail's prices come from.

Lightsail is the reason the refresher has a registry rather than a hardcoded
list of calls: its rates do not all come from the Price List API, so the core
refresher could never have fetched them without knowing what Lightsail is.
It registers two fetchers, one against each source, and core stays ignorant.
"""

from __future__ import annotations

from typing import Any

import botocore.exceptions

from zombiescan.pricing.refresh import RefreshContext, paginate, price_fetcher, usd_rate

LIGHTSAIL_GROUPS = {
    "Lightsail Block Storage": ("lightsail_disk_gb_month", "GB-Mo"),
    "Lightsail unused static IP": ("lightsail_static_ip_hour", "Hrs"),
    "Lightsail Load Balancer": ("lightsail_load_balancer_hour", "Hrs"),
    "Lightsail Snapshot": ("lightsail_snapshot_gb_month", "GB-Mo"),
    "Lightsail Instance Snapshot": ("lightsail_snapshot_gb_month", "GB-Mo"),
}


@price_fetcher(
    *sorted({key for key, _ in LIGHTSAIL_GROUPS.values()}),
    label="Lightsail commodity rates",
    pack="lightsail",
)
def fetch_lightsail_rates(ctx: RefreshContext) -> dict[str, dict[str, float]]:
    """Per-region Lightsail commodity rates, in one pass over the service.

    Six groups are wanted and the service is small, so bucketing a single
    listing beats filtering it once per group.
    """
    table: dict[str, dict[str, float]] = {key: {} for key, _ in LIGHTSAIL_GROUPS.values()}
    for entry in paginate(ctx.pricing, ServiceCode="AmazonLightsail"):
        attrs = entry["product"]["attributes"]
        group = attrs.get("group")
        if group not in LIGHTSAIL_GROUPS:
            continue
        key, unit = LIGHTSAIL_GROUPS[group]
        region = attrs.get("regionCode")
        price = usd_rate(entry, unit)
        if not region or price is None:
            continue
        # Instance and disk snapshots share a rate under two group names.
        table[key][region] = max(table[key].get(region, 0.0), price)
    return table


@price_fetcher(
    "lightsail_bundle_month",
    "lightsail_container_power_month",
    label="Lightsail bundles and container powers (Lightsail API)",
    pack="lightsail",
)
def fetch_lightsail_bundles(ctx: RefreshContext) -> dict[str, Any]:
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
    session = ctx.session
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
    return {
        "lightsail_bundle_month": bundles,
        "lightsail_container_power_month": powers,
    }
