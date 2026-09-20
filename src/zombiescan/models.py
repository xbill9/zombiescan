"""Core data types shared by every check.

Costs are estimates. They are computed from a bundled price table rather than
from your actual bill, so they will not match Cost Explorer to the cent. A
finding marked ``approximate_cost`` fell back to us-east-1 pricing because the
table has no entry for its region.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    import boto3

    from zombiescan.pricing import PriceTable


@dataclass(frozen=True)
class Finding:
    """One resource that appears to be waste."""

    check: str
    resource_id: str
    resource_type: str
    region: str
    reason: str
    monthly_cost: float
    remediation: str
    approximate_cost: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "resource_id": self.resource_id,
            "resource_type": self.resource_type,
            "region": self.region,
            "reason": self.reason,
            "monthly_cost": round(self.monthly_cost, 2),
            "approximate_cost": self.approximate_cost,
            "remediation": self.remediation,
            "details": self.details,
        }


@dataclass
class ScanContext:
    """What a check gets handed: a session, a region, and the price table."""

    session: boto3.Session
    region: str
    pricing: PriceTable

    def client(self, service: str) -> Any:
        return self.session.client(service, region_name=self.region)
