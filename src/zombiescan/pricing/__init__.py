"""Bundled AWS price table.

Prices ship with the package so a scan works offline and does not add a
``pricing:GetProducts`` call (and its latency and permission) to every run.
Regenerate with ``uv run python -m zombiescan.pricing.refresh``.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

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

    def ebs_gb_month(self, region: str, volume_type: str) -> tuple[float, bool]:
        """USD per provisioned GB-month. Returns ``(price, approximate)``."""
        prices, approximate = self._lookup("ebs_gb_month", region)
        if not prices:
            return 0.0, True
        if volume_type in prices:
            return prices[volume_type], approximate
        # Unknown volume type: gp3 is the modern default and the safest stand-in.
        return prices.get("gp3", 0.0), True

    def snapshot_gb_month(self, region: str) -> tuple[float, bool]:
        price, approximate = self._lookup("snapshot_gb_month", region)
        return (price or 0.0), approximate

    def nat_gateway_month(self, region: str) -> tuple[float, bool]:
        """USD per month for NAT gateway uptime. Excludes data processing."""
        hourly, approximate = self._lookup("nat_gateway_hour", region)
        if not hourly:
            return 0.0, True
        return hourly * self._hours, approximate

    def public_ipv4_month(self) -> tuple[float, bool]:
        """USD per month for one public IPv4 address.

        Flat across commercial regions, so there is no region argument. Not
        sourced from the Price List API -- see ``refresh.PUBLIC_IPV4_SOURCE``.
        """
        entry = self._data.get("public_ipv4_hour") or {}
        hourly = entry.get("_value", 0.0) if isinstance(entry, dict) else float(entry)
        return hourly * self._hours, False

    def load_balancer_month(self, region: str, kind: str) -> tuple[float, bool]:
        """USD per month of uptime for an ``alb`` or ``nlb``. Excludes LCU charges."""
        rates, approximate = self._lookup("load_balancer_hour", region)
        if not rates:
            return 0.0, True
        if kind in rates:
            return rates[kind] * self._hours, approximate
        # Gateway load balancers and anything new price differently; fall back
        # to the ALB rate and mark it rather than reporting zero.
        return rates.get("alb", 0.0) * self._hours, True

    def log_storage_gb_month(self, region: str) -> tuple[float, bool]:
        """USD per GB-month of Standard-class CloudWatch Logs storage."""
        price, approximate = self._lookup("log_storage_gb_month", region)
        return (price or 0.0), approximate

    def vpc_endpoint_month(self, region: str) -> tuple[float, bool]:
        """USD per month for one interface VPC endpoint ENI. Excludes data processing."""
        hourly, approximate = self._lookup("vpc_endpoint_hour", region)
        if not hourly:
            return 0.0, True
        return hourly * self._hours, approximate

    def classic_lb_month(self, region: str) -> tuple[float, bool]:
        """USD per month of uptime for a Classic (ELBv1) load balancer."""
        hourly, approximate = self._lookup("classic_lb_hour", region)
        if not hourly:
            return 0.0, True
        return hourly * self._hours, approximate

    def rds_storage_gb_month(
        self, region: str, storage_type: str, multi_az: bool
    ) -> tuple[float, bool]:
        """USD per GB-month of RDS storage for this type and deployment."""
        by_deployment, approximate = self._lookup("rds_storage_gb_month", region)
        if not by_deployment:
            return 0.0, True
        rates = by_deployment.get("multi" if multi_az else "single", {})
        if storage_type in rates:
            return rates[storage_type], approximate
        # Unknown storage type: gp2 is RDS's long-standing default.
        return rates.get("gp2", 0.0), True

    def kms_key_month(self, region: str) -> tuple[float, bool]:
        """USD per month for one customer managed KMS key."""
        price, approximate = self._lookup("kms_key_month", region)
        return (price or 0.0), approximate

    def secret_month(self, region: str) -> tuple[float, bool]:
        """USD per month for one Secrets Manager secret. Excludes API calls."""
        price, approximate = self._lookup("secret_month", region)
        return (price or 0.0), approximate

    def efs_gb_month(self, region: str) -> tuple[float, bool]:
        """USD per GB-month of EFS Standard regional storage."""
        price, approximate = self._lookup("efs_gb_month", region)
        return (price or 0.0), approximate

    def ecr_gb_month(self, region: str) -> tuple[float, bool]:
        """USD per GB-month of ECR image storage."""
        price, approximate = self._lookup("ecr_gb_month", region)
        return (price or 0.0), approximate

    def s3_gb_month(self, region: str) -> tuple[float, bool]:
        """USD per GB-month of S3 Standard storage."""
        price, approximate = self._lookup("s3_gb_month", region)
        return (price or 0.0), approximate

    def rds_snapshot_gb_month(self, region: str) -> tuple[float, bool]:
        """USD per GB-month of RDS snapshot and backup storage."""
        price, approximate = self._lookup("rds_snapshot_gb_month", region)
        return (price or 0.0), approximate

    def dynamodb_capacity_month(
        self, region: str, read_units: int, write_units: int
    ) -> tuple[float, bool]:
        """USD per month for this much provisioned read and write capacity."""
        rates, approximate = self._lookup("dynamodb_capacity_hour", region)
        if not rates:
            return 0.0, True
        hourly = read_units * rates.get("read", 0.0) + write_units * rates.get("write", 0.0)
        return hourly * self._hours, approximate

    def lightsail_bundle_month(self, region: str, bundle_id: str) -> tuple[float, bool]:
        """USD per month for a Lightsail instance bundle.

        A Lightsail instance bills its whole bundle whether it is running or
        stopped, so this is the full price either way.
        """
        bundles, approximate = self._lookup("lightsail_bundle_month", region)
        if not bundles or bundle_id not in bundles:
            # Bundle ids are versioned (``micro_3_0``) and a new generation
            # appears before the table is refreshed. Reporting zero would hide
            # the finding, so say nothing about the price instead.
            return 0.0, True
        return bundles[bundle_id], approximate

    def lightsail_container_power_month(self, region: str, power: str) -> tuple[float, bool]:
        """USD per month for one container service node at this power."""
        powers, approximate = self._lookup("lightsail_container_power_month", region)
        if not powers or power not in powers:
            return 0.0, True
        return powers[power], approximate

    def lightsail_disk_gb_month(self, region: str) -> tuple[float, bool]:
        """USD per GB-month of Lightsail block storage."""
        price, approximate = self._lookup("lightsail_disk_gb_month", region)
        return (price or 0.0), approximate

    def lightsail_static_ip_month(self, region: str) -> tuple[float, bool]:
        """USD per month for a static IP attached to nothing.

        An attached static IP is free; this rate only applies once it is not.
        """
        hourly, approximate = self._lookup("lightsail_static_ip_hour", region)
        if not hourly:
            return 0.0, True
        return hourly * self._hours, approximate

    def lightsail_load_balancer_month(self, region: str) -> tuple[float, bool]:
        """USD per month of Lightsail load balancer uptime."""
        hourly, approximate = self._lookup("lightsail_load_balancer_hour", region)
        if not hourly:
            return 0.0, True
        return hourly * self._hours, approximate

    def lightsail_snapshot_gb_month(self, region: str) -> tuple[float, bool]:
        """USD per GB-month of Lightsail instance or disk snapshot storage."""
        price, approximate = self._lookup("lightsail_snapshot_gb_month", region)
        return (price or 0.0), approximate

    def route53_health_check_month(self, aws_endpoint: bool = True) -> tuple[float, bool]:
        """USD per month for one health check. Global, so no region argument."""
        rates = self._data.get("route53_health_check_month") or {}
        price = rates.get("aws" if aws_endpoint else "non_aws", 0.0)
        return price, not bool(rates)
