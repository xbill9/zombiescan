"""Interface VPC endpoints in VPCs with nothing running.

An interface endpoint is a billed ENI in every subnet it serves, charged by
the hour whether anything resolves through it or not. They are usually created
in bulk by a module or a blueprint, and they outlive the workload they were
created for.

Gateway endpoints (S3, DynamoDB) are free and never reported.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from zombiescan.checks._shared import name_tag, vpcs_with_workloads
from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "unused-vpc-endpoint"


def build_finding(ctx: ScanContext, endpoint: dict[str, Any]) -> Finding:
    endpoint_id = endpoint["VpcEndpointId"]
    # Billed per ENI, which means per availability zone the endpoint spans.
    interfaces = endpoint.get("NetworkInterfaceIds") or []
    eni_count = max(1, len(interfaces))
    per_eni, approximate = ctx.pricing.vpc_endpoint_month(ctx.region)
    service = endpoint.get("ServiceName", "unknown service")

    return Finding(
        check=CHECK_NAME,
        resource_id=endpoint_id,
        resource_type="vpc-endpoint",
        region=ctx.region,
        reason=(
            f"Interface endpoint for {service.rsplit('.', 1)[-1]} across {eni_count} AZ(s) "
            f"in a VPC with no workloads"
        ),
        monthly_cost=per_eni * eni_count,
        remediation=(
            f"aws ec2 delete-vpc-endpoints --vpc-endpoint-ids {endpoint_id} --region {ctx.region}"
        ),
        approximate_cost=approximate,
        details={
            "name": name_tag(endpoint),
            "service_name": service,
            "vpc_id": endpoint.get("VpcId"),
            "endpoint_type": endpoint.get("VpcEndpointType"),
            "az_count": eni_count,
            "usd_per_eni_month": per_eni,
            "note": "uptime charge only; data processing not included",
        },
    )


@check(CHECK_NAME, "Interface VPC endpoints with no workloads")
def unused_vpc_endpoint(ctx: ScanContext) -> Iterator[Finding]:
    client = ctx.client("ec2")
    pages = client.get_paginator("describe_vpc_endpoints").paginate()
    endpoints: list[dict[str, Any]] = []
    for page in pages:
        for endpoint in page.get("VpcEndpoints", []):
            # Gateway endpoints are free. Reporting them as waste would be wrong.
            if endpoint.get("VpcEndpointType") == "Gateway":
                continue
            if endpoint.get("State") != "available":
                continue
            endpoints.append(endpoint)

    if not endpoints:
        return

    busy = vpcs_with_workloads(client)
    for endpoint in endpoints:
        if endpoint.get("VpcId") in busy:
            continue
        yield build_finding(ctx, endpoint)
