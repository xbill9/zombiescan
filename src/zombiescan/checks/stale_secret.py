"""Secrets Manager secrets nothing has read in a long time.

Every secret costs $0.40 a month whether or not anything retrieves it. Secrets
outlive the applications that needed them: the service is deleted, the secret
is not, and nothing ever errors.

This check reports *staleness*, not certainty. A deliberately dormant secret --
break-glass credentials, a disaster-recovery account -- looks exactly like an
abandoned one from here. The finding is a prompt to look, not a verdict.
"""

from __future__ import annotations

import datetime as dt
import shlex
from collections.abc import Iterator
from typing import Any

from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "stale-secret"
STALE_DAYS = 90


def _days_since(moment: dt.datetime | None) -> int | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.UTC)
    return (dt.datetime.now(dt.UTC) - moment).days


def build_finding(ctx: ScanContext, secret: dict[str, Any], idle_days: int | None) -> Finding:
    name = secret["Name"]
    price, approximate = ctx.pricing.secret_month(ctx.region)
    created = _days_since(secret.get("CreatedDate"))

    if idle_days is None:
        reason = "Secret has never been retrieved"
        if created is not None:
            reason += f" in the {created} days since it was created"
    else:
        reason = f"Secret has not been retrieved in {idle_days} days"

    return Finding(
        check=CHECK_NAME,
        resource_id=name,
        resource_type="secret",
        region=ctx.region,
        reason=reason,
        monthly_cost=price,
        remediation=(
            f"aws secretsmanager delete-secret --secret-id {shlex.quote(name)} "
            f"--recovery-window-in-days 30 --region {ctx.region}"
        ),
        approximate_cost=approximate,
        details={
            "arn": secret.get("ARN"),
            "description": (secret.get("Description") or "").strip() or None,
            "days_since_access": idle_days,
            "age_days": created,
            "rotation_enabled": secret.get("RotationEnabled", False),
            "note": (
                "staleness is not proof of disuse: break-glass and disaster-recovery "
                "secrets are dormant by design. Access timestamps have day granularity"
            ),
        },
    )


@check(CHECK_NAME, "Secrets nothing has read in 90 days")
def stale_secret(ctx: ScanContext) -> Iterator[Finding]:
    pages = ctx.client("secretsmanager").get_paginator("list_secrets").paginate()
    for page in pages:
        for secret in page.get("SecretList", []):
            # Already scheduled for deletion: it is leaving on a timer.
            if secret.get("DeletedDate"):
                continue
            idle_days = _days_since(secret.get("LastAccessedDate"))
            if idle_days is not None and idle_days < STALE_DAYS:
                continue
            yield build_finding(ctx, secret, idle_days)
