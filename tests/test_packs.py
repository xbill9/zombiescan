"""Pack discovery, attribution and compatibility.

The pack seam is only worth having if it is the same seam a third party would
use. These tests treat the built-in packs as ordinary packs and check the
things that would silently break a marketplace: a pack that claims another
pack's name, one built against a different API version, one that fails to
import, and a rate key that two packs both answer to.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from zombiescan import engine, packs
from zombiescan.pricing import PriceTable
from zombiescan.pricing.rates import RATES, RESOLVERS, RateSpec, register_rate
from zombiescan.registry import CHECKS


def test_the_builtin_checks_all_belong_to_a_loaded_pack():
    """A check attributed to a pack nobody loaded cannot be reported honestly."""
    engine.load_packs()
    assert CHECKS, "discovery registered no checks at all"
    for name, spec in CHECKS.items():
        assert spec.pack in packs.PACKS, f"{name} claims pack {spec.pack!r}, which is not loaded"


def test_lightsail_is_a_separate_pack():
    """The extraction is the proof the seam carries a whole service."""
    engine.load_packs()
    assert "lightsail" in packs.PACKS
    lightsail = [name for name, spec in CHECKS.items() if spec.pack == "lightsail"]
    assert len(lightsail) == 6, lightsail


def test_discovery_is_repeatable():
    """The CLI loads packs per command and the test suite loads them once.

    Importing twice must not raise a duplicate-registration error, or the
    second scan in any long-lived process would fail.
    """
    first = engine.load_packs()
    second = engine.load_packs()
    assert {p.name for p in first.loaded} == {p.name for p in second.loaded}
    assert second.failed == []


def test_a_pack_cannot_claim_a_name_twice():
    engine.load_packs()
    with pytest.raises(ValueError, match="duplicate pack name"):
        packs.register_pack("core", version="9.9.9")


def test_a_pack_built_against_another_api_version_is_refused():
    """Refusing beats half-loading: a pack that mispriced findings would be worse."""
    with pytest.raises(packs.IncompatiblePack, match="pack API"):
        packs.register_pack(
            "from-the-future", version="1.0.0", api_version=packs.PACK_API_VERSION + 1
        )
    assert "from-the-future" not in packs.PACKS


def test_a_disabled_pack_contributes_no_checks():
    engine.load_packs()
    selected = engine.select_checks((), disabled_packs=frozenset({"lightsail"}))
    assert selected, "disabling one pack removed everything"
    assert all(spec.pack != "lightsail" for spec in selected)


def test_naming_a_check_from_a_disabled_pack_is_an_error():
    """Silently honouring one flag and ignoring the other is the worst option."""
    engine.load_packs()
    with pytest.raises(ValueError, match="disabled by --disable-pack"):
        engine.select_checks(
            ("lightsail-stopped-instance",), disabled_packs=frozenset({"lightsail"})
        )


# --- rates ---------------------------------------------------------------


def test_two_packs_cannot_register_the_same_rate_key():
    """Import order must never decide what a finding costs."""
    engine.load_packs()
    with pytest.raises(ValueError, match="duplicate rate key"):
        register_rate(RateSpec("ebs.gb_month", "somewhere_else"))


def test_every_rate_key_resolves(pricing: PriceTable):
    """A registered key that raises on lookup is a broken pack, found late."""
    engine.load_packs()
    for key, spec in RATES.items():
        kwargs = {} if spec.is_global else {"region": "us-east-1"}
        if spec.variants:
            kwargs["variant"] = "definitely-not-a-real-variant"
        price, approximate = pricing.rate(key, **kwargs)
        assert price >= 0.0
        if spec.variants and spec.default_variant is None:
            # An unknown variant with no fallback must say it does not know
            # rather than price the resource as something else.
            assert price == 0.0 and approximate is True, key


def test_an_unknown_rate_key_names_the_registered_ones(pricing: PriceTable):
    with pytest.raises(KeyError, match="unknown rate key"):
        pricing.rate("nothing.like_this", region="us-east-1")


def test_lightsail_rates_are_registered_by_the_lightsail_pack():
    engine.load_packs()
    owners = {key: spec.pack for key, spec in RATES.items() if key.startswith("lightsail.")}
    assert owners, "the Lightsail pack registered no rates"
    assert set(owners.values()) == {"lightsail"}, owners
    assert not any(key.startswith("lightsail.") for key in RESOLVERS)


# --- the refresher -------------------------------------------------------


def test_every_table_section_has_exactly_one_fetcher():
    """A section nobody fetches goes stale silently; two fetchers race.

    This is the invariant that lets a pack own its own rates: the refresher
    rebuilds the whole table from whatever packs are installed, so a missing
    fetcher would quietly drop a pack's prices on the next refresh.
    """
    engine.load_packs()
    from zombiescan.pricing.refresh import FETCHERS

    table = PriceTable.load()._data
    sections = set(table) - {"_meta", "fallback_region", "hours_per_month"}

    declared: dict[str, str] = {}
    for fetcher in FETCHERS:
        for section in fetcher.sections:
            assert section not in declared, f"{section} claimed twice"
            declared[section] = fetcher.pack

    assert sections == set(declared), {
        "in table, no fetcher": sorted(sections - set(declared)),
        "fetcher, not in table": sorted(set(declared) - sections),
    }


def test_running_the_refresher_as_main_uses_the_registry_packs_write_to():
    """`python -m zombiescan.pricing.refresh` loads this module twice.

    The __main__ copy gets a FETCHERS list of its own, while packs register
    into the canonical one, so running __main__'s own main() rebuilds the
    table from core's sections alone and drops every pack's rates -- silently,
    because the result is a valid table with prices of zero. Run exactly as the
    docs say to, with main() replaced so nothing reaches AWS.
    """
    script = textwrap.dedent(
        """
        import runpy
        import zombiescan.pricing.refresh as canonical
        from zombiescan import packs

        def fake_main():
            packs.discover()
            print(sorted({fetcher.pack for fetcher in canonical.FETCHERS}))

        canonical.main = fake_main
        runpy.run_module("zombiescan.pricing.refresh", run_name="__main__")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr
    assert "lightsail" in result.stdout, result.stdout


def test_a_refresh_that_would_lose_rates_is_refused():
    """An empty section is a failed fetch, not a price of zero."""
    from zombiescan.pricing.refresh import regressions

    existing = {
        "_meta": {"generated": "2026-09-21T00:00:00Z"},
        "ebs_gb_month": {"us-east-1": {"gp3": 0.08}},
        "lightsail_bundle_month": {"us-east-1": {"nano_3_0": 5.0}},
    }
    unchanged = {"ebs_gb_month": existing["ebs_gb_month"]}

    assert regressions({**unchanged, "lightsail_bundle_month": {}}, existing) == [
        "lightsail_bundle_month: empty (had 1 entries)"
    ]
    assert regressions(unchanged, existing) == ["lightsail_bundle_month: gone (had 1 entries)"]
    assert regressions({**existing}, existing) == []


def test_lightsail_sections_are_fetched_by_the_lightsail_pack():
    engine.load_packs()
    from zombiescan.pricing.refresh import FETCHERS

    for fetcher in FETCHERS:
        for section in fetcher.sections:
            if section.startswith("lightsail_"):
                assert fetcher.pack == "lightsail", section


# --- third-party packs ---------------------------------------------------
#
# These write a real module to a temp directory and hand discovery a real
# entry point pointing at it, so the path exercised is the one a pip-installed
# pack would take -- not a mock of it.


class FakeEntryPoint:
    def __init__(self, name: str, value: str) -> None:
        self.name = name
        self.value = value
        self.dist = None


@pytest.fixture
def third_party(tmp_path, monkeypatch):
    """Put a writable package directory on sys.path and drive discovery at it."""
    import sys

    monkeypatch.syspath_prepend(str(tmp_path))
    registered: list[FakeEntryPoint] = []
    monkeypatch.setattr(packs, "_installed_entry_points", lambda: list(registered))

    def install(module_name: str, source: str) -> None:
        (tmp_path / f"{module_name}.py").write_text(source)
        registered.append(FakeEntryPoint(module_name.replace("_", "-"), module_name))

    yield install

    for name in list(sys.modules):
        if name.startswith("zsp_"):
            del sys.modules[name]
    for pack_name in [p for p in packs.PACKS if p.startswith("zsp-")]:
        del packs.PACKS[pack_name]
    for check_name in [c for c in CHECKS if c.startswith("zsp-")]:
        del CHECKS[check_name]
    # Rates outlive the module that registered them, so a later discovery of
    # the same pack would hit the duplicate-key guard.
    for rate_key in [r for r in RATES if r.startswith("zsp.")]:
        del RATES[rate_key]


def test_an_installed_pack_is_discovered_and_its_checks_attributed(third_party):
    third_party(
        "zsp_good",
        """
from collections.abc import Iterator

from zombiescan.models import Finding, ScanContext
from zombiescan.packs import register_pack
from zombiescan.registry import check

register_pack("zsp-good", version="2.1.0", description="a third-party pack")


@check("zsp-good-widget", "Third-party widgets")
def widget(ctx: ScanContext) -> Iterator[Finding]:
    yield from ()
""",
    )
    report = packs.discover()

    assert "zsp-good" in packs.PACKS
    assert packs.PACKS["zsp-good"].version == "2.1.0"
    assert CHECKS["zsp-good-widget"].pack == "zsp-good"
    assert "zsp-good" in {p.name for p in report.loaded}


def test_a_pack_that_fails_to_import_does_not_take_the_scan_with_it(third_party):
    third_party("zsp_broken", "raise RuntimeError('boom')\n")
    before = len(CHECKS)

    report = packs.discover()

    assert len(CHECKS) == before, "a broken pack cost us another pack's checks"
    failure = next(f for f in report.failed if f.name == "zsp-broken")
    assert "boom" in failure.message
    assert {"core", "lightsail"} <= {p.name for p in report.loaded}


def test_a_module_that_never_registers_is_reported_not_ignored(third_party):
    """Silence here looks exactly like a pack with no checks in it."""
    third_party("zsp_silent", "x = 1\n")

    report = packs.discover()

    failure = next(f for f in report.failed if f.name == "zsp-silent")
    assert "never called register_pack" in failure.message


def test_an_installed_pack_can_price_with_its_own_rate(third_party, pricing):
    """The point of the seam: a pack teaches the table a rate core never knew."""
    third_party(
        "zsp_priced",
        """
from zombiescan.packs import register_pack
from zombiescan.pricing.rates import RateSpec, register_rate

register_pack("zsp-priced", version="1.0.0")
register_rate(RateSpec("zsp.widget_month", "zsp_widget_month", pack="zsp-priced"))
""",
    )
    packs.discover()

    table = PriceTable(
        {
            "fallback_region": "us-east-1",
            "hours_per_month": 730,
            "zsp_widget_month": {"us-east-1": 1.25},
        }
    )
    assert table.rate("zsp.widget_month", region="us-east-1") == (1.25, False)
