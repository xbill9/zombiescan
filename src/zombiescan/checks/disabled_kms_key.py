"""Customer managed KMS keys that are disabled but not deleted.

A disabled key bills the full monthly key charge and can do nothing: it cannot
encrypt, decrypt, or sign until re-enabled. Disabling is usually the cautious
first step before deletion, and the second step is what gets forgotten.

AWS managed keys are free and never reported. Keys already scheduled for
deletion are on their way out on a timer, so reporting them is noise.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "disabled-kms-key"


def build_finding(ctx: ScanContext, metadata: dict[str, Any], aliases: list[str]) -> Finding:
    key_id = metadata["KeyId"]
    price, approximate = ctx.pricing.kms_key_month(ctx.region)
    label = aliases[0] if aliases else key_id

    return Finding(
        check=CHECK_NAME,
        resource_id=key_id,
        resource_type="kms-key",
        region=ctx.region,
        reason=f"Customer managed key {label} is disabled but still billed monthly",
        monthly_cost=price,
        remediation=(
            f"aws kms schedule-key-deletion --key-id {key_id} "
            f"--pending-window-in-days 30 --region {ctx.region}"
        ),
        approximate_cost=approximate,
        details={
            "aliases": aliases,
            "description": (metadata.get("Description") or "").strip() or None,
            "key_spec": metadata.get("KeySpec"),
            "multi_region": metadata.get("MultiRegion"),
            "note": (
                "deletion is irreversible and anything encrypted with this key "
                "becomes unrecoverable; the 30-day window is the time to find out"
            ),
        },
    )


def _aliases_by_key(client: Any) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for page in client.get_paginator("list_aliases").paginate():
        for alias in page.get("Aliases", []):
            target = alias.get("TargetKeyId")
            name = alias.get("AliasName")
            if target and name:
                grouped.setdefault(target, []).append(name)
    return grouped


@check(CHECK_NAME, "Disabled KMS keys still being billed")
def disabled_kms_key(ctx: ScanContext) -> Iterator[Finding]:
    client = ctx.client("kms")
    aliases = _aliases_by_key(client)

    for page in client.get_paginator("list_keys").paginate():
        for entry in page.get("Keys", []):
            key_id = entry["KeyId"]
            metadata = client.describe_key(KeyId=key_id).get("KeyMetadata", {})
            # AWS managed keys cost nothing, so a disabled one is not waste.
            if metadata.get("KeyManager") != "CUSTOMER":
                continue
            if metadata.get("KeyState") != "Disabled":
                continue
            yield build_finding(ctx, metadata, sorted(aliases.get(key_id, [])))
