"""Rate specifications: how a section of the price table becomes a number.

The table itself is data, but turning a section into a monthly USD figure
needs a few facts the JSON does not carry -- whether the stored rate is
hourly, whether the section is keyed by a variant (a volume type, a bundle
id), and what to fall back to when the variant is unknown. Those facts used
to live in a hand-written ``PriceTable`` method per section, which meant a
pack could not price anything the core did not already know about.

They live here instead, as ``RateSpec`` records in an open registry. A pack
registers the specs for its own sections at import time and then prices its
findings through ``ctx.pricing.rate(...)`` like anything else.

Four shapes cover almost everything:

    flat        {region: price}                     -- ECR storage
    hourly      {region: price}, x hours_per_month  -- NAT gateway
    variants    {region: {variant: price}}          -- EBS by volume type
    global      price with no region at all         -- Route 53 health checks

Anything stranger -- RDS storage is keyed by deployment *and* type, DynamoDB
multiplies two rates by two quantities -- registers a resolver function
instead with ``register_resolver``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from zombiescan.pricing import PriceTable

# A resolver gets the table and whatever keyword arguments the caller passed,
# and returns the same (price, approximate) pair every lookup returns.
Resolver = Callable[..., tuple[float, bool]]


@dataclass(frozen=True)
class RateSpec:
    """How to read one section of the price table."""

    key: str
    section: str
    # Stored rates are per hour and must be multiplied by hours_per_month.
    per_hour: bool = False
    # The section holds {region: {variant: price}} rather than {region: price}.
    variants: bool = False
    # Which variant to use when the requested one is missing. None means a
    # missing variant is unpriceable: return 0.0 marked approximate rather
    # than silently pricing it as something else.
    default_variant: str | None = None
    # "global" sections are a bare value with no region layer.
    scope: str = "regional"
    # Which pack registered this, for `zombiescan rates` and error messages.
    pack: str = "core"

    @property
    def is_global(self) -> bool:
        return self.scope == "global"


RATES: dict[str, RateSpec] = {}
RESOLVERS: dict[str, Resolver] = {}


def register_rate(spec: RateSpec) -> RateSpec:
    """Register a rate spec. Raises on a duplicate key.

    Duplicates are a bug rather than an override: two packs quietly claiming
    the same rate key would make the price of a finding depend on import
    order, which is the least debuggable failure this code could have.
    """
    if spec.key in RATES or spec.key in RESOLVERS:
        owner = RATES[spec.key].pack if spec.key in RATES else "a resolver"
        raise ValueError(f"duplicate rate key {spec.key!r} (already registered by {owner})")
    RATES[spec.key] = spec
    return spec


def register_resolver(key: str, fn: Resolver, pack: str = "core") -> Resolver:
    """Register a custom resolver for a rate whose shape ``RateSpec`` cannot express."""
    if key in RATES or key in RESOLVERS:
        raise ValueError(f"duplicate rate key {key!r}")
    RESOLVERS[key] = fn
    return fn


def resolve(table: PriceTable, key: str, **kwargs: Any) -> tuple[float, bool]:
    """Look up ``key`` in ``table``. Returns ``(usd_per_month, approximate)``."""
    if key in RESOLVERS:
        return RESOLVERS[key](table, **kwargs)
    spec = RATES.get(key)
    if spec is None:
        known = ", ".join(sorted(set(RATES) | set(RESOLVERS)))
        raise KeyError(f"unknown rate key {key!r}. Registered: {known}")
    return table.apply(spec, **kwargs)


def _register_core_rates() -> None:
    """The rate specs for the checks that ship in this package."""
    for spec in (
        # --- flat per-region storage rates ---------------------------------
        RateSpec("snapshot.gb_month", "snapshot_gb_month"),
        RateSpec("log.storage_gb_month", "log_storage_gb_month"),
        RateSpec("kms.key_month", "kms_key_month"),
        RateSpec("secret.month", "secret_month"),
        RateSpec("efs.gb_month", "efs_gb_month"),
        RateSpec("ecr.gb_month", "ecr_gb_month"),
        RateSpec("s3.gb_month", "s3_gb_month"),
        RateSpec("rds.snapshot_gb_month", "rds_snapshot_gb_month"),
        # --- hourly rates billed by uptime ---------------------------------
        RateSpec("nat_gateway.month", "nat_gateway_hour", per_hour=True),
        RateSpec("vpc_endpoint.month", "vpc_endpoint_hour", per_hour=True),
        RateSpec("classic_lb.month", "classic_lb_hour", per_hour=True),
        # --- keyed by a variant --------------------------------------------
        # An unknown volume type is priced as gp3, the modern default; an
        # unknown load balancer kind (gateway, or whatever comes next) as alb.
        RateSpec("ebs.gb_month", "ebs_gb_month", variants=True, default_variant="gp3"),
        RateSpec(
            "load_balancer.month",
            "load_balancer_hour",
            per_hour=True,
            variants=True,
            default_variant="alb",
        ),
    ):
        register_rate(spec)


_register_core_rates()


def _rds_storage(table: Any, region: str, storage_type: str, multi_az: bool) -> tuple[float, bool]:
    """RDS storage is keyed by deployment *and* by type, two layers deep."""
    by_deployment, approximate = table.lookup_section("rds_storage_gb_month", region)
    if not by_deployment:
        return 0.0, True
    rates = by_deployment.get("multi" if multi_az else "single", {})
    if storage_type in rates:
        return rates[storage_type], approximate
    # Unknown storage type: gp2 is RDS's long-standing default.
    return rates.get("gp2", 0.0), True


def _dynamodb_capacity(
    table: Any, region: str, read_units: int, write_units: int
) -> tuple[float, bool]:
    """Two rates multiplied by two quantities, which no single lookup expresses."""
    rates, approximate = table.lookup_section("dynamodb_capacity_hour", region)
    if not rates:
        return 0.0, True
    hourly = read_units * rates.get("read", 0.0) + write_units * rates.get("write", 0.0)
    return hourly * table.hours_per_month, approximate


def _public_ipv4(table: Any) -> tuple[float, bool]:
    """A documented constant rather than a table lookup, so never approximate.

    The Price List API does not expose public IPv4 pricing -- the EC2 'IP
    Address' family is Wavelength CarrierIP only -- so this rate is hardcoded
    by ``refresh.PUBLIC_IPV4_SOURCE`` and is exact for every commercial region.
    """
    entry = table.section("public_ipv4_hour") or {}
    hourly = entry.get("_value", 0.0) if isinstance(entry, dict) else float(entry)
    return hourly * table.hours_per_month, False


def _register_irregular_rates() -> None:
    register_resolver("rds.storage_gb_month", _rds_storage)
    register_resolver("dynamodb.capacity_month", _dynamodb_capacity)
    register_resolver("public_ipv4.month", _public_ipv4)
    # Health checks are billed per check with no region layer at all, and
    # priced differently depending on whether the endpoint is an AWS one.
    register_rate(
        RateSpec(
            "route53.health_check_month",
            "route53_health_check_month",
            scope="global",
            variants=True,
        )
    )


_register_irregular_rates()
