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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Finding:
        """Rebuild a finding from --json output.

        Lets `clean --from report.json` act on exactly the findings someone
        reviewed, rather than on whatever a fresh scan happens to see now.
        """
        return cls(
            check=data["check"],
            resource_id=data["resource_id"],
            resource_type=data.get("resource_type", "unknown"),
            region=data.get("region", "unknown"),
            reason=data.get("reason", ""),
            monthly_cost=float(data.get("monthly_cost", 0.0)),
            remediation=data.get("remediation", ""),
            approximate_cost=bool(data.get("approximate_cost", False)),
            details=data.get("details") or {},
        )

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
