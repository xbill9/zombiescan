"""Lightsail's rate specs.

These are registered by the Lightsail pack, not by the core price table, which
is what makes the pack seam worth having: a pack teaches zombiescan how to read
its own sections of the table, and core never learns the word "bundle".

Two of them deliberately have no ``default_variant``. Bundle ids are versioned
(``micro_3_0``) and a new generation appears in the API before the bundled price
table has been refreshed. Pricing an unknown bundle as some other bundle would
put a confident wrong dollar figure on a finding; returning zero marked
approximate says "this is waste, I cannot tell you what it costs", which is
true.
"""

from __future__ import annotations

from zombiescan.pricing.rates import RateSpec, register_rate

PACK = "lightsail"


def _register() -> None:
    for spec in (
        # Monthly already, straight from GetBundles / GetContainerServicePowers.
        # Lightsail bills a stopped instance its whole bundle, so this is the
        # full price whatever state the instance is in.
        RateSpec("lightsail.bundle_month", "lightsail_bundle_month", variants=True, pack=PACK),
        RateSpec(
            "lightsail.container_power_month",
            "lightsail_container_power_month",
            variants=True,
            pack=PACK,
        ),
        # These four come from the Price List API, bucketed by the `group`
        # attribute because Lightsail products leave productFamily null.
        RateSpec("lightsail.disk_gb_month", "lightsail_disk_gb_month", pack=PACK),
        RateSpec("lightsail.snapshot_gb_month", "lightsail_snapshot_gb_month", pack=PACK),
        # A static IP attached to something is free; these rates only apply
        # once it is attached to nothing.
        RateSpec("lightsail.static_ip_month", "lightsail_static_ip_hour", per_hour=True, pack=PACK),
        RateSpec(
            "lightsail.load_balancer_month",
            "lightsail_load_balancer_hour",
            per_hour=True,
            pack=PACK,
        ),
    ):
        register_rate(spec)


_register()
