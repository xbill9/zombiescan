"""Lightsail container services running nothing.

A container service bills for its nodes -- power multiplied by scale -- from
the moment it is created, not from the moment something is deployed to it. A
service created to try something out, or whose deployment was deleted, keeps
charging for capacity that is serving no containers at all.

Only a service with *no current deployment* is reported. A service whose
deployment exists but is failing is a broken deploy, not waste, and calling it
waste would point the operator at the wrong problem.

Disabled services are skipped because disabling is what stops the billing --
a disabled service is already the fixed state, not the broken one.
"""

from __future__ import annotations

import shlex
from collections.abc import Iterator
from typing import Any

from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "lightsail-idle-container-service"

# DISABLED has already stopped the charge; DELETING is on its way out.
NOT_BILLING = {"DISABLED", "DELETING"}


def build_finding(ctx: ScanContext, service: dict[str, Any]) -> Finding:
    name = service["containerServiceName"]
    power = service.get("power") or ""
    scale = int(service.get("scale") or 1)
    node_price, approximate = ctx.pricing.lightsail_container_power_month(ctx.region, power)

    return Finding(
        check=CHECK_NAME,
        resource_id=name,
        resource_type="lightsail-container-service",
        region=ctx.region,
        reason=(
            f"Container service is {service.get('state')} with no deployment, "
            f"billing {scale} x {power or 'unknown'} node(s) to run nothing"
        ),
        monthly_cost=node_price * scale,
        # Disabling stops the charge and keeps the service and its URL, which
        # is the reversible move; deleting is the permanent one.
        remediation=(
            f"aws lightsail update-container-service --service-name {shlex.quote(name)} "
            f"--is-disabled --region {ctx.region}  # or delete-container-service to "
            f"remove it for good"
        ),
        approximate_cost=approximate,
        details={
            "power": power,
            "scale": scale,
            "state": service.get("state"),
            "node_price_month": node_price,
            "created_at": str(service.get("createdAt") or ""),
            "note": (
                "billing starts at creation, not at first deployment; disabling stops "
                "the charge while keeping the service and its URL"
            ),
        },
    )


@check(CHECK_NAME, "Lightsail container services with nothing deployed")
def lightsail_idle_container_service(ctx: ScanContext) -> Iterator[Finding]:
    client = ctx.client("lightsail")
    # GetContainerServices is not pageable -- it has no pageToken and returns
    # every service in the region in one response.
    for service in client.get_container_services().get("containerServices", []):
        if not service.get("containerServiceName"):
            continue
        if (service.get("state") or "").upper() in NOT_BILLING:
            continue
        if service.get("currentDeployment"):
            continue
        yield build_finding(ctx, service)
