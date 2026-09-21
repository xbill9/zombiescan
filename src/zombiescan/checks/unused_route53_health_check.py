"""Route 53 health checks no DNS record uses.

Health checks bill monthly whether or not anything consults them. They outlive
the failover record sets they were built for, and because they live in a
separate part of the console from the hosted zones, nobody goes looking.

Route 53 is a global service, so this check runs once per scan rather than
once per region.
"""

from __future__ import annotations

import shlex
from collections.abc import Iterator
from typing import Any

from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "unused-route53-health-check"


def _referenced_health_check_ids(client: Any) -> set[str]:
    """Health check ids named by any record set in any hosted zone."""
    referenced: set[str] = set()
    for zone_page in client.get_paginator("list_hosted_zones").paginate():
        for zone in zone_page.get("HostedZones", []):
            pages = client.get_paginator("list_resource_record_sets").paginate(
                HostedZoneId=zone["Id"]
            )
            for page in pages:
                for record in page.get("ResourceRecordSets", []):
                    if record.get("HealthCheckId"):
                        referenced.add(record["HealthCheckId"])
                    alias = record.get("AliasTarget") or {}
                    if alias.get("HealthCheckId"):
                        referenced.add(alias["HealthCheckId"])
    return referenced


def _label(config: dict[str, Any], check_id: str) -> str:
    return config.get("FullyQualifiedDomainName") or config.get("IPAddress") or check_id


def build_finding(ctx: ScanContext, health_check: dict[str, Any]) -> Finding:
    check_id = health_check["Id"]
    config = health_check.get("HealthCheckConfig") or {}
    price, approximate = ctx.pricing.route53_health_check_month(aws_endpoint=True)

    return Finding(
        check=CHECK_NAME,
        resource_id=check_id,
        resource_type="route53-health-check",
        region="global",
        reason=(f"Health check for {_label(config, check_id)} is referenced by no DNS record"),
        monthly_cost=price,
        remediation=(f"aws route53 delete-health-check --health-check-id {shlex.quote(check_id)}"),
        # Priced at the AWS-endpoint rate. A check against an endpoint outside
        # AWS costs more, and optional features add to both.
        approximate_cost=True,
        details={
            "type": config.get("Type"),
            "endpoint": _label(config, check_id),
            "port": config.get("Port"),
            "note": (
                "priced at the AWS-endpoint rate; checks against non-AWS endpoints "
                "and optional features such as string matching cost more. A check "
                "used only by a CloudWatch alarm or a calculated health check will "
                "appear unused here"
            ),
            "approximate_reason": "endpoint-rate-assumed" if not approximate else "no-price-data",
        },
    )


@check(CHECK_NAME, "Route 53 health checks no record uses", scope="global")
def unused_route53_health_check(ctx: ScanContext) -> Iterator[Finding]:
    client = ctx.client("route53")
    checks: list[dict[str, Any]] = []
    for page in client.get_paginator("list_health_checks").paginate():
        checks.extend(page.get("HealthChecks", []))

    if not checks:
        return

    referenced = _referenced_health_check_ids(client)
    for health_check in checks:
        if health_check.get("Id") in referenced:
            continue
        yield build_finding(ctx, health_check)
