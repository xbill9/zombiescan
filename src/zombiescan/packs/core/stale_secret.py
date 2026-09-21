"""Secrets Manager secrets nothing has read in a long time.

Every secret costs $0.40 a month whether or not anything retrieves it. Secrets
outlive the applications that needed them: the service is deleted, the secret
is not, and nothing ever errors.

This check reports *staleness*, not certainty. A deliberately dormant secret --
break-glass credentials, a disaster-recovery account -- looks exactly like an
abandoned one from here. The finding is a prompt to look, not a verdict.

A secret nothing has ever read is judged on its age, not on the absence of a
read. Every secret spends its first minutes never having been retrieved, and
one written this morning for a service that ships on Friday is not waste.
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


def _dormant_days(secret: dict[str, Any]) -> int | None:
    """Days since anything last touched this secret, for one never retrieved.

    Written as well as created: a secret whose value was replaced last week is
    being looked after by somebody, even if nothing has read it back yet.
    """
    stamps = [
        moment
        for moment in (secret.get("CreatedDate"), secret.get("LastChangedDate"))
        if moment is not None
    ]
    if not stamps:
        # Neither timestamp: nothing to judge age by, so treat it as stale
        # rather than invent a date. The reason says which case it is.
        return None
    return _days_since(max(stamps))


def build_finding(ctx: ScanContext, secret: dict[str, Any], idle_days: int | None) -> Finding:
    name = secret["Name"]
    price, approximate = ctx.pricing.secret_month(ctx.region)
    created = _days_since(secret.get("CreatedDate"))

    if idle_days is None:
        dormant = _dormant_days(secret)
        reason = "Secret has never been retrieved"
        if dormant is not None:
            written = _days_since(secret.get("LastChangedDate"))
            since = "written" if written is not None and written == dormant else "created"
            reason += f" in the {dormant} days since it was {since}"
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
            if idle_days is None:
                # Never retrieved. Wait until it has had as long to be read as
                # a stale one has had to be read again -- otherwise every
                # secret is waste for the first ninety days of its life.
                dormant = _dormant_days(secret)
                if dormant is not None and dormant < STALE_DAYS:
                    continue
            elif idle_days < STALE_DAYS:
                continue
            yield build_finding(ctx, secret, idle_days)
