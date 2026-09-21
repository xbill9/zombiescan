"""Engine scheduling: which checks run where, and how failures are counted."""

from __future__ import annotations

import pytest

from tests.conftest import TEST_PRICES
from zombiescan.engine import scan
from zombiescan.models import Finding
from zombiescan.pricing import PriceTable
from zombiescan.registry import CheckSpec

REGIONS = ["us-east-1", "eu-west-1", "ap-south-1"]


def _spec(name, scope, seen):
    def fn(ctx):
        seen.append((name, ctx.region))
        yield Finding(
            check=name,
            resource_id=f"{name}-1",
            resource_type="t",
            region=ctx.region,
            reason="r",
            monthly_cost=1.0,
            remediation="aws noop",
        )

    return CheckSpec(name=name, title=name, fn=fn, scope=scope)


@pytest.fixture
def pricing():
    return PriceTable(TEST_PRICES)


def test_regional_checks_run_once_per_region(pricing):
    seen = []
    scan(None, REGIONS, [_spec("regional-one", "regional", seen)], pricing)
    assert sorted(r for _, r in seen) == sorted(REGIONS)


def test_global_checks_run_exactly_once(pricing):
    """Running a global check per region reports one resource three times and
    triples the waste total."""
    seen = []
    result = scan(None, REGIONS, [_spec("global-one", "global", seen)], pricing)
    assert len(seen) == 1
    assert len(result.findings) == 1


def test_mixed_scopes_are_counted_correctly(pricing):
    seen = []
    checks = [_spec("regional-one", "regional", seen), _spec("global-one", "global", seen)]
    result = scan(None, REGIONS, checks, pricing)
    # three regional runs plus one global run
    assert result.attempted == 4
    assert len(result.findings) == 4
    assert result.completely_failed is False


def test_a_check_that_raises_becomes_an_error_not_a_crash(pricing):
    def boom(ctx):
        raise RuntimeError("the region ate it")
        yield  # pragma: no cover

    spec = CheckSpec(name="boom", title="boom", fn=boom)
    result = scan(None, REGIONS, [spec], pricing)
    assert len(result.errors) == 3
    assert result.completely_failed is True
    assert "the region ate it" in result.errors[0].message


def test_one_bad_check_does_not_stop_the_others(pricing):
    seen = []

    def boom(ctx):
        raise RuntimeError("nope")
        yield  # pragma: no cover

    checks = [CheckSpec(name="boom", title="boom", fn=boom), _spec("fine", "regional", seen)]
    result = scan(None, REGIONS, checks, pricing)
    assert len(result.findings) == 3
    assert len(result.errors) == 3
    assert result.completely_failed is False


def test_findings_are_sorted_by_cost_descending(pricing):
    def mixed(ctx):
        for cost in (1.0, 99.0, 50.0):
            yield Finding(
                check="m",
                resource_id=f"r{cost}",
                resource_type="t",
                region=ctx.region,
                reason="r",
                monthly_cost=cost,
                remediation="aws noop",
            )

    result = scan(None, ["us-east-1"], [CheckSpec(name="m", title="m", fn=mixed)], pricing)
    assert [f.monthly_cost for f in result.findings] == [99.0, 50.0, 1.0]
