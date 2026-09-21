"""ECR repositories nothing has pushed to in a long time.

Container images are the easiest storage to forget. Every CI run pushes
another tag, nothing ever deletes the old ones, and a repository belonging to
a service that was decommissioned months ago keeps billing at full rate. There
is no "unattached" state to notice here, and no screen in the console totals
it up -- the repository list shows a tag count, not a bill.

Two honest limits on the number this reports:

- **ECR bills for unique layers.** Images within a repository share base
  layers, and repositories built from the same base share them with each
  other, so adding up ``imageSizeInBytes`` counts the same stored bytes more
  than once. The cost here is an upper bound, sometimes a generous one.
- **The push date says nothing about pulls.** A repository nothing has pushed
  to in months may still be pulled from on every deploy. Staleness means the
  images stopped changing, not that they stopped being used.

The durable fix is a lifecycle policy rather than a one-off delete: without
one, the tags accumulate again over the next few weeks of builds.
"""

from __future__ import annotations

import datetime as dt
import shlex
from collections.abc import Iterator
from typing import Any

from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "ecr-stale-images"
STALE_DAYS = 90
BYTES_PER_GB = 1024**3


def _days_since(moment: dt.datetime | None) -> int | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.UTC)
    return (dt.datetime.now(dt.UTC) - moment).days


def _repository_images(client: Any, name: str) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    pages = client.get_paginator("describe_images").paginate(repositoryName=name)
    for page in pages:
        images.extend(page.get("imageDetails", []))
    return images


def build_finding(
    ctx: ScanContext,
    repository: dict[str, Any],
    images: list[dict[str, Any]],
    newest_days: int,
    oldest_days: int,
) -> Finding:
    name = repository["repositoryName"]
    # A manifest list (multi-arch index) carries no size of its own, so this
    # has to tolerate the key being absent rather than assume every entry
    # reports bytes.
    total_bytes = sum(int(image.get("imageSizeInBytes", 0)) for image in images)
    stored_gb = total_bytes / BYTES_PER_GB
    price, approximate = ctx.pricing.ecr_gb_month(ctx.region)
    untagged = sum(1 for image in images if not image.get("imageTags"))

    reason = (
        f"{len(images)} image(s) holding up to {stored_gb:,.2f} GB; "
        f"nothing pushed in {newest_days} days"
    )
    if untagged:
        reason += f", {untagged} untagged"

    return Finding(
        check=CHECK_NAME,
        resource_id=name,
        resource_type="ecr-repository",
        region=ctx.region,
        reason=reason,
        monthly_cost=stored_gb * price,
        remediation=(
            f"aws ecr describe-images --repository-name {shlex.quote(name)} "
            f"--region {ctx.region}  # review, then delete the tags you do not "
            f"need and add a lifecycle policy so they do not come back"
        ),
        # Always approximate: layers shared between images are stored once but
        # counted once per image here, so the true bill is lower.
        approximate_cost=True,
        details={
            "image_count": len(images),
            "untagged_count": untagged,
            "total_gb": round(stored_gb, 3),
            "newest_push_days": newest_days,
            "oldest_push_days": oldest_days,
            "repository_uri": repository.get("repositoryUri"),
            "note": (
                "upper bound: ECR bills for unique layers, and images sharing a base "
                "layer are counted once each here. A stale repository may still be "
                "pulled from -- the push date says nothing about pulls"
            ),
            "approximate_reason": "shared-layers" if not approximate else "region-fallback",
        },
    )


@check(
    CHECK_NAME,
    "ECR repositories nothing has pushed to in months",
    uncleanable=(
        # Staleness here means nothing has been *pushed*, which says nothing
        # about pulls: a repository untouched for a year may still be pulled on
        # every deploy. There is no snapshot or recovery window for a deleted
        # image either, so a wrong guess is unrecoverable and breaks the next
        # rollout.
        "a stale repository may still be pulled from on every deploy, and a "
        "deleted image has no recovery window -- decide which tags to drop, "
        "then encode that decision as a lifecycle policy"
    ),
)
def ecr_stale_images(ctx: ScanContext) -> Iterator[Finding]:
    client = ctx.client("ecr")

    repositories: list[dict[str, Any]] = []
    for page in client.get_paginator("describe_repositories").paginate():
        repositories.extend(page.get("repositories", []))

    for repository in repositories:
        name = repository.get("repositoryName")
        if not name:
            continue
        images = _repository_images(client, name)
        # An empty repository stores nothing and bills nothing. It is clutter,
        # not waste, and this check is about the bytes.
        if not images:
            continue
        ages = [
            days
            for days in (_days_since(image.get("imagePushedAt")) for image in images)
            if days is not None
        ]
        if not ages:
            continue
        # The most recent push is what decides staleness: one fresh tag means
        # the repository is still in service, however old the rest of it is.
        newest = min(ages)
        if newest < STALE_DAYS:
            continue
        yield build_finding(ctx, repository, images, newest, max(ages))
