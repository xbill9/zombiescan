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
