"""Bundled AWS price table.

Prices ship with the package so a scan works offline and does not add a
``pricing:GetProducts`` call (and its latency and permission) to every run.
Regenerate with ``uv run python -m zombiescan.pricing.refresh``.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

from zombiescan.pricing.rates import RateSpec, resolve

_TABLE_PATH = pathlib.Path(__file__).with_name("table.json")


class PriceTable:
    """Region-aware lookups with an explicit fallback.

    Any lookup that falls back to the default region returns
    ``approximate=True`` so the report can mark the number rather than
    quietly presenting a guess as fact.
    """

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data
        self._fallback = data.get("fallback_region", "us-east-1")
        self._hours = data.get("hours_per_month", 730)

    @classmethod
    def load(cls) -> PriceTable:
        return cls(json.loads(_TABLE_PATH.read_text()))

    @property
    def generated(self) -> str:
        return self._data.get("_meta", {}).get("generated", "unknown")

    def _lookup(self, section: str, region: str) -> tuple[Any, bool]:
        table = self._data.get(section, {})
        if region in table:
            return table[region], False
        return table.get(self._fallback), True

    @property
    def hours_per_month(self) -> int:
        """Hours the table bills a month as. Lightsail uses 744, most rates 730."""
        return self._hours

    def section(self, name: str) -> Any:
        """One raw section of the table, or None. For resolvers with odd shapes."""
        return self._data.get(name)

    def lookup_section(self, name: str, region: str) -> tuple[Any, bool]:
        """A section's entry for ``region``, falling back to the default region."""
        return self._lookup(name, region)

    def _monthly(self, value: float, spec: RateSpec) -> float:
        return value * self._hours if spec.per_hour else value

    def apply(self, spec: RateSpec, region: str | None = None, variant: str | None = None):
        """Read one section of the table according to its ``RateSpec``."""
        if spec.is_global:
            rates = self._data.get(spec.section) or {}
            if spec.variants:
                if variant not in rates:
                    # Same rule as the regional branch: a variant the table has
                    # never heard of is unpriced, not free.
                    return 0.0, True
                return self._monthly(rates[variant], spec), False
            value = rates if not isinstance(rates, dict) else rates.get("_value", 0.0)
            return self._monthly(float(value or 0.0), spec), not bool(rates)

        value, approximate = self._lookup(spec.section, region or self._fallback)

        if spec.variants:
            if not value:
                return 0.0, True
            if variant in value:
                return self._monthly(value[variant], spec), approximate
            # Unknown variant: fall back if the spec names one, and mark the
            # number approximate either way -- it is not the rate that was asked
            # for. With no fallback the rate is simply unknown, and reporting
            # zero silently would hide the finding's cost rather than flag it.
            if spec.default_variant and spec.default_variant in value:
                return self._monthly(value[spec.default_variant], spec), True
            return 0.0, True

        if not value:
            # An hourly section with no rate cannot be multiplied out, so the
            # answer is explicitly a guess. A flat section keeps the lookup's
            # own verdict, which is the long-standing behaviour of these rates.
            return (0.0, True) if spec.per_hour else (0.0, approximate)
        return self._monthly(value, spec), approximate

    def rate(self, key: str, **kwargs: Any) -> tuple[float, bool]:
        """Look up a registered rate by key. Returns ``(usd_per_month, approximate)``.

        This is the entry point a pack prices through: every rate any pack has
        registered is reachable here, so a pack never needs a method added to
        this class.
        """
        return resolve(self, key, **kwargs)

    def ebs_gb_month(self, region: str, volume_type: str) -> tuple[float, bool]:
        """USD per provisioned GB-month. Returns ``(price, approximate)``."""
        return self.rate("ebs.gb_month", region=region, variant=volume_type)

    def snapshot_gb_month(self, region: str) -> tuple[float, bool]:
        return self.rate("snapshot.gb_month", region=region)

    def nat_gateway_month(self, region: str) -> tuple[float, bool]:
        """USD per month for NAT gateway uptime. Excludes data processing."""
        return self.rate("nat_gateway.month", region=region)

    def public_ipv4_month(self) -> tuple[float, bool]:
        """USD per month for one public IPv4 address.

        Flat across commercial regions, so there is no region argument. Not
        sourced from the Price List API -- see ``refresh.PUBLIC_IPV4_SOURCE``.
        """
        return self.rate("public_ipv4.month")

    def load_balancer_month(self, region: str, kind: str) -> tuple[float, bool]:
        """USD per month of uptime for an ``alb`` or ``nlb``. Excludes LCU charges."""
        return self.rate("load_balancer.month", region=region, variant=kind)

    def log_storage_gb_month(self, region: str) -> tuple[float, bool]:
        """USD per GB-month of Standard-class CloudWatch Logs storage."""
        return self.rate("log.storage_gb_month", region=region)

    def vpc_endpoint_month(self, region: str) -> tuple[float, bool]:
        """USD per month for one interface VPC endpoint ENI. Excludes data processing."""
        return self.rate("vpc_endpoint.month", region=region)

    def classic_lb_month(self, region: str) -> tuple[float, bool]:
        """USD per month of uptime for a Classic (ELBv1) load balancer."""
        return self.rate("classic_lb.month", region=region)

    def rds_storage_gb_month(
        self, region: str, storage_type: str, multi_az: bool
    ) -> tuple[float, bool]:
        """USD per GB-month of RDS storage for this type and deployment."""
        return self.rate(
            "rds.storage_gb_month", region=region, storage_type=storage_type, multi_az=multi_az
        )

    def kms_key_month(self, region: str) -> tuple[float, bool]:
        """USD per month for one customer managed KMS key."""
        return self.rate("kms.key_month", region=region)

    def secret_month(self, region: str) -> tuple[float, bool]:
        """USD per month for one Secrets Manager secret. Excludes API calls."""
        return self.rate("secret.month", region=region)

    def efs_gb_month(self, region: str) -> tuple[float, bool]:
        """USD per GB-month of EFS Standard regional storage."""
        return self.rate("efs.gb_month", region=region)

    def ecr_gb_month(self, region: str) -> tuple[float, bool]:
        """USD per GB-month of ECR image storage."""
        return self.rate("ecr.gb_month", region=region)

    def s3_gb_month(self, region: str) -> tuple[float, bool]:
        """USD per GB-month of S3 Standard storage."""
        return self.rate("s3.gb_month", region=region)

    def rds_snapshot_gb_month(self, region: str) -> tuple[float, bool]:
        """USD per GB-month of RDS snapshot and backup storage."""
        return self.rate("rds.snapshot_gb_month", region=region)

    def dynamodb_capacity_month(
        self, region: str, read_units: int, write_units: int
    ) -> tuple[float, bool]:
        """USD per month for this much provisioned read and write capacity."""
        return self.rate(
            "dynamodb.capacity_month", region=region, read_units=read_units, write_units=write_units
        )

    def route53_health_check_month(self, aws_endpoint: bool = True) -> tuple[float, bool]:
        """USD per month for one health check. Global, so no region argument."""
        return self.rate("route53.health_check_month", variant="aws" if aws_endpoint else "non_aws")
