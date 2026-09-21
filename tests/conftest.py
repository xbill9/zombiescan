"""Shared test fixtures.

Tests run offline against recorded API shapes. The price table is pinned here
rather than loaded from the bundled one, so regenerating real prices
(`python -m zombiescan.pricing.refresh`) cannot break the assertions.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
from typing import Any

import pytest

from zombiescan import engine
from zombiescan.models import ScanContext
from zombiescan.pricing import PriceTable

# Importing a check module registers that one check. The registry-wide tests
# ("every check has a cleaner or says why not") need every pack loaded, so do
# it once here rather than leaving it to whichever test imported what first.
engine.load_packs()

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

TEST_PRICES = {
    "fallback_region": "us-east-1",
    "hours_per_month": 730,
    "public_ipv4_hour": {"_value": 0.005},
    "ebs_gb_month": {
        "us-east-1": {"gp3": 0.08, "gp2": 0.10, "io1": 0.125},
        "eu-west-1": {"gp3": 0.088, "gp2": 0.11, "io1": 0.138},
    },
    "snapshot_gb_month": {"us-east-1": 0.05, "eu-west-1": 0.05},
    "nat_gateway_hour": {"us-east-1": 0.045, "eu-west-1": 0.048},
    "load_balancer_hour": {
        "us-east-1": {"alb": 0.0225, "nlb": 0.0225},
        "eu-west-1": {"alb": 0.0252, "nlb": 0.0252},
    },
    "log_storage_gb_month": {"us-east-1": 0.03, "eu-west-1": 0.03},
    "vpc_endpoint_hour": {"us-east-1": 0.01, "eu-west-1": 0.011},
    "classic_lb_hour": {"us-east-1": 0.025, "eu-west-1": 0.028},
    "s3_gb_month": {"us-east-1": 0.023, "eu-west-1": 0.024},
    "ecr_gb_month": {"us-east-1": 0.10, "eu-west-1": 0.10},
    "rds_snapshot_gb_month": {"us-east-1": 0.095, "eu-west-1": 0.105},
    "dynamodb_capacity_hour": {"us-east-1": {"read": 0.00013, "write": 0.00065}},
    "route53_health_check_month": {"aws": 0.50, "non_aws": 0.75},
    "route53_hosted_zone_month": {"first_25": 0.50, "additional": 0.10},
    "lightsail_bundle_month": {"us-east-1": {"nano_3_0": 5.0, "micro_3_0": 7.0, "small_3_0": 12.0}},
    "lightsail_container_power_month": {"us-east-1": {"nano": 7.0, "micro": 10.0, "small": 15.0}},
    "lightsail_disk_gb_month": {"us-east-1": 0.10},
    "lightsail_static_ip_hour": {"us-east-1": 0.005},
    "lightsail_load_balancer_hour": {"us-east-1": 0.024193548},
    "lightsail_snapshot_gb_month": {"us-east-1": 0.05},
    "kms_key_month": {"us-east-1": 1.0, "eu-west-1": 1.0},
    "secret_month": {"us-east-1": 0.40, "eu-west-1": 0.40},
    "efs_gb_month": {"us-east-1": 0.30, "eu-west-1": 0.33},
    "rds_storage_gb_month": {
        "us-east-1": {
            "single": {"gp2": 0.115, "gp3": 0.115, "io1": 0.125},
            "multi": {"gp2": 0.23, "gp3": 0.23, "io1": 0.25},
        }
    },
}

# Keys whose values boto3 hands back as datetimes rather than strings.
_DATETIME_KEYS = (
    "CreateTime",
    "StartTime",
    "LaunchTime",
    "LastAccessedDate",
    "LastChangedDate",
    "CreatedDate",
    "DeletedDate",
    "Initiated",
    "SnapshotCreateTime",
    "imagePushedAt",
)


def _revive_datetimes(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            key: (
                dt.datetime.fromisoformat(value)
                if key in _DATETIME_KEYS and isinstance(value, str)
                else _revive_datetimes(value)
            )
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_revive_datetimes(item) for item in node]
    return node


def load_fixture(name: str) -> dict[str, Any]:
    """Load a recorded API response, restoring datetimes the way boto3 hands them over."""
    return _revive_datetimes(json.loads((FIXTURES / f"{name}.json").read_text()))


def _resolve(spec: Any, kwargs: dict[str, Any]) -> Any:
    """A response spec is either a literal or a callable of the call's kwargs.

    The callable form is what lets one fake answer differently per argument --
    describe_target_health returns different targets for each target group.
    """
    return spec(**kwargs) if callable(spec) else spec


class FakePaginator:
    def __init__(self, pages: Any, operation: str, client: FakeClient) -> None:
        self._pages = pages
        self._operation = operation
        self._client = client

    def paginate(self, **kwargs: Any) -> list[dict[str, Any]]:
        self._client.record(self._operation, kwargs)
        return _resolve(self._pages, kwargs)


class FakeClient:
    """Stands in for a boto3 client.

    ``pages`` maps a paginated operation to the pages it yields; ``direct``
    maps a non-paginated operation to its response. Either value may be a
    callable taking the call's kwargs. Calls are recorded in ``calls`` (last
    kwargs per operation) and ``call_log`` (every call, in order) so tests can
    assert on server-side filtering.
    """

    def __init__(
        self,
        pages: dict[str, Any] | None = None,
        direct: dict[str, Any] | None = None,
    ) -> None:
        self._pages = pages or {}
        self._direct = direct or {}
        self.calls: dict[str, dict[str, Any]] = {}
        self.call_log: list[tuple[str, dict[str, Any]]] = []

    def record(self, operation: str, kwargs: dict[str, Any]) -> None:
        self.calls[operation] = kwargs
        self.call_log.append((operation, kwargs))

    def can_paginate(self, operation: str) -> bool:
        """As boto3 does: whether this operation has a paginator.

        Checks built with ``zombiescan.building`` ask before paginating, so the
        fake has to answer. An operation configured under ``direct`` is one the
        real client would not paginate either.
        """
        return operation in self._pages or operation not in self._direct

    def get_paginator(self, operation: str) -> FakePaginator:
        return FakePaginator(self._pages.get(operation, [{}]), operation, self)

    def __getattr__(self, name: str) -> Any:
        # Only reached for attributes not set in __init__, i.e. API operations.
        direct = self.__dict__.get("_direct", {})
        if name not in direct:
            raise AttributeError(
                f"FakeClient has no response configured for {name!r}; "
                f"add it to the 'direct' mapping"
            )

        def call(**kwargs: Any) -> Any:
            self.record(name, kwargs)
            return _resolve(direct[name], kwargs)

        return call


@pytest.fixture
def pricing() -> PriceTable:
    return PriceTable(TEST_PRICES)


@pytest.fixture
def make_context(pricing: PriceTable):
    """Build a ScanContext wired to a FakeClient, and expose the client."""

    def _make(
        pages: dict[str, list[dict[str, Any]]] | None = None,
        direct: dict[str, dict[str, Any]] | None = None,
        region: str = "us-east-1",
    ) -> tuple[ScanContext, FakeClient]:
        client = FakeClient(pages, direct)
        ctx = ScanContext(session=None, region=region, pricing=pricing)  # type: ignore[arg-type]
        ctx.client = lambda _service: client  # type: ignore[method-assign]
        return ctx, client

    return _make
