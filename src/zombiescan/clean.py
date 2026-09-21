"""Executing cleanup plans.

Everything here is built so that a dry run and a real run take the same path
and produce the same plan. ``apply`` gates one thing: whether a planned step
is sent to AWS. If the dry run is wrong, the real run is wrong in the same
way, which is the only way a preview is worth anything.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

import boto3
import botocore.exceptions

from zombiescan.cleaners import CLEANERS, Step
from zombiescan.models import Finding, ScanContext
from zombiescan.pricing import PriceTable
from zombiescan.registry import CHECKS

PLANNED = "planned"
APPLIED = "applied"
SKIPPED = "skipped"
FAILED = "failed"
UNSUPPORTED = "unsupported"


@dataclass
class Outcome:
    finding: Finding
    steps: list[Step] = field(default_factory=list)
    status: str = PLANNED
    error: str | None = None
    results: list[dict[str, Any]] = field(default_factory=list)

    @property
    def irreversible(self) -> bool:
        return any(s.irreversible for s in self.steps)

    @property
    def monthly_saving(self) -> float:
        return self.finding.monthly_cost if self.status == APPLIED else 0.0


def _client(session: boto3.Session, service: str, region: str, home: str) -> Any:
    # Findings from global checks carry region "global", which is not somewhere
    # a client can be pointed.
    return session.client(service, region_name=home if region == "global" else region)


def plan_for(session: boto3.Session, finding: Finding, pricing: PriceTable, home: str) -> Outcome:
    """Work out the calls that would resolve this finding. Makes no changes."""
    spec = CHECKS.get(finding.check)
    if spec is not None and spec.uncleanable:
        return Outcome(finding=finding, status=UNSUPPORTED, error=spec.uncleanable)

    planner = CLEANERS.get(finding.check)
    if planner is None:
        # A --from report can name a check this build does not have, because
        # the pack that produced it is not installed here. Say so: "no cleaner"
        # would send the operator looking for a bug that is really a missing
        # dependency.
        if spec is None:
            return Outcome(
                finding=finding,
                status=UNSUPPORTED,
                error=(
                    f"no check named {finding.check} is installed, so its findings "
                    "cannot be planned -- install the pack that produced this report"
                ),
            )
        return Outcome(
            finding=finding,
            status=UNSUPPORTED,
            error=f"no cleaner is implemented for {finding.check}",
        )

    region = finding.region if finding.region != "global" else home
    ctx = ScanContext(session=session, region=region, pricing=pricing)
    try:
        steps = list(planner(ctx, finding))
    except Exception as exc:  # noqa: BLE001 - planning must not abort the run
        return Outcome(
            finding=finding, status=FAILED, error=f"could not plan: {type(exc).__name__}: {exc}"
        )

    if not steps:
        return Outcome(finding=finding, status=UNSUPPORTED, error="nothing to do for this finding")
    return Outcome(finding=finding, steps=steps, status=PLANNED)


def apply_outcome(outcome: Outcome, session: boto3.Session, home: str) -> Outcome:
    """Execute a planned outcome, stopping that finding at its first failure.

    Steps within a finding are ordered and dependent -- snapshot before
    delete, image before terminate -- so a failed step must not be followed by
    the destructive one that assumed it succeeded.
    """
    for step in outcome.steps:
        client = _client(session, step.service, outcome.finding.region, home)
        try:
            response = getattr(client, step.operation)(**step.params)
        except botocore.exceptions.ClientError as exc:
            outcome.status = FAILED
            outcome.error = f"{step.operation}: {exc.response.get('Error', {}).get('Message', exc)}"
            return outcome
        except Exception as exc:  # noqa: BLE001
            outcome.status = FAILED
            outcome.error = f"{step.operation}: {type(exc).__name__}: {exc}"
            return outcome
        outcome.results.append(
            {
                "operation": step.operation,
                "service": step.service,
                "description": step.description,
                # Response metadata is noise; keep the identifiers a reader
                # would need to undo or audit this.
                "returned": {
                    k: v
                    for k, v in (response or {}).items()
                    if k != "ResponseMetadata" and isinstance(v, (str, int, float, bool))
                },
            }
        )
    outcome.status = APPLIED
    return outcome


def audit_document(
    outcomes: list[Outcome], applied: bool, caller_arn: str | None = None
) -> dict[str, Any]:
    """A record of what was done, for the person who asks later."""
    counted = {}
    for outcome in outcomes:
        counted[outcome.status] = counted.get(outcome.status, 0) + 1
    return {
        "schema_version": 1,
        "generated": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": "apply" if applied else "dry-run",
        "caller_arn": caller_arn,
        "counts": counted,
        "monthly_saving": round(sum(o.monthly_saving for o in outcomes), 2),
        "actions": [
            {
                "check": o.finding.check,
                "resource_id": o.finding.resource_id,
                "region": o.finding.region,
                "monthly_cost": round(o.finding.monthly_cost, 2),
                "status": o.status,
                "irreversible": o.irreversible,
                "error": o.error,
                "steps": [
                    {
                        "description": s.description,
                        "service": s.service,
                        "operation": s.operation,
                        "params": s.params,
                        "irreversible": s.irreversible,
                    }
                    for s in o.steps
                ],
                "results": o.results,
            }
            for o in outcomes
        ],
    }
