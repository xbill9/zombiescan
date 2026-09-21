"""Hosted zones that publish nothing.

Every hosted zone bills monthly from the moment it is created, whether or not
anything resolves against it. A zone holding only the SOA and NS records that
Route 53 creates with it answers no useful query: it is a namespace somebody
reserved and never filled, usually left behind by a service that was torn down
or a private zone whose VPC moved on.

The apex SOA and NS record sets cannot be deleted -- Route 53 refuses -- so a
zone reporting exactly two record sets is holding exactly those two, and no
second call is needed to know it.

Route 53 is a global service, so this check runs once per scan rather than
once per region.
"""

from __future__ import annotations

import shlex
from collections.abc import Iterator
from typing import Any

from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "unused-route53-zone"

# SOA plus the apex NS set: what every zone is born with and cannot lose.
DEFAULT_RECORD_SETS = 2

# The price breaks after the 25th zone in the account. Which zones are "the
# first 25" is not a property of any zone, so the tier is chosen from how many
# the account holds -- see _marginal_variant.
FREE_TIER_ZONES = 25

# Cloud Map stamps the namespace it created a zone for into the zone's comment.
# Those zones belong to the namespace: deleting one directly leaves the
# namespace pointing at nothing, so the remediation has to name the namespace.
CLOUD_MAP_MARKER = "Created by AWS Cloud Map namespace"


def zone_id(zone: dict[str, Any]) -> str:
    """The bare zone id. The API returns it as "/hostedzone/Z123"."""
    return (zone.get("Id") or "").rsplit("/", 1)[-1]


def cloud_map_namespace(zone: dict[str, Any]) -> tuple[str, str] | None:
    """``(namespace id, region)`` when Cloud Map owns this zone, else None.

    The comment carries the namespace ARN, so the owner is knowable from the
    same call that found the zone.
    """
    comment = (zone.get("Config") or {}).get("Comment") or ""
    if CLOUD_MAP_MARKER not in comment:
        return None
    arn = comment.split()[-1]
    parts = arn.split(":")
    namespace = arn.rsplit("/", 1)[-1]
    if not namespace.startswith("ns-") or len(parts) < 4:
        return None
    return namespace, parts[3]


def _marginal_variant(zone_count: int) -> str:
    """Which rate one zone is worth, given how many the account has.

    Deleting a zone saves what the *last* zone costs, not the average: an
    account with 30 of them is paying $0.10 for the 30th, so removing one
    saves $0.10 however the other 29 are priced. Reporting the first-tier rate
    there would overstate the saving fivefold.
    """
    return "first_25" if zone_count <= FREE_TIER_ZONES else "additional"


def build_finding(ctx: ScanContext, zone: dict[str, Any], zone_count: int) -> Finding:
    identifier = zone_id(zone)
    name = zone.get("Name") or identifier
    private = bool((zone.get("Config") or {}).get("PrivateZone"))
    records = int(zone.get("ResourceRecordSetCount") or 0)
    price, approximate = ctx.pricing.rate(
        "route53.hosted_zone_month", variant=_marginal_variant(zone_count)
    )

    kind = "Private" if private else "Public"
    owner = cloud_map_namespace(zone)

    reason = (
        f"{kind} hosted zone {name} holds only the {records} record set(s) Route 53 "
        f"creates with a zone, so it resolves nothing"
    )
    note = (
        "a zone created moments ago looks the same as one abandoned years ago, "
        "because the API reports no creation time. Deleting a public zone gives up "
        "its name servers: if the domain is delegated to them at a registrar, "
        "recreating the zone means updating the delegation"
    )
    details: dict[str, Any] = {
        "name": name,
        "private": private,
        "record_set_count": records,
        "zones_in_account": zone_count,
        "priced_as": _marginal_variant(zone_count),
        "comment": (zone.get("Config") or {}).get("Comment"),
    }

    if owner is None:
        remediation = f"aws route53 delete-hosted-zone --id {shlex.quote(identifier)}"
    else:
        namespace, namespace_region = owner
        # An empty zone under a namespace means no service has registered
        # anything with it. The namespace is what to delete; the zone goes
        # with it, and deleting the zone on its own would orphan it.
        reason += f"; it belongs to Cloud Map namespace {namespace}, which registers nothing"
        details["cloud_map_namespace"] = namespace
        details["cloud_map_region"] = namespace_region
        note = (
            "Cloud Map owns this zone. Delete the namespace, not the zone: "
            "delete-hosted-zone leaves the namespace behind pointing at nothing. "
            "delete-namespace refuses while any service is still registered in it, "
            "even one with no instances"
        )
        remediation = (
            f"aws servicediscovery delete-namespace --id {shlex.quote(namespace)} "
            f"--region {namespace_region}"
        )

    details["note"] = note
    return Finding(
        check=CHECK_NAME,
        resource_id=identifier,
        resource_type="route53-hosted-zone",
        region="global",
        reason=reason,
        monthly_cost=price,
        remediation=remediation,
        approximate_cost=approximate,
        details=details,
    )


@check(CHECK_NAME, "Hosted zones with no records in them", scope="global")
def unused_route53_zone(ctx: ScanContext) -> Iterator[Finding]:
    client = ctx.client("route53")
    zones: list[dict[str, Any]] = []
    for page in client.get_paginator("list_hosted_zones").paginate():
        zones.extend(page.get("HostedZones", []))

    for zone in zones:
        if int(zone.get("ResourceRecordSetCount") or 0) > DEFAULT_RECORD_SETS:
            continue
        if not zone_id(zone):
            continue
        yield build_finding(ctx, zone, len(zones))
