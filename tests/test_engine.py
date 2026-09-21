"""Engine scheduling: which checks run where, and how failures are counted."""

from __future__ import annotations

import botocore.exceptions
import pytest

from tests.conftest import TEST_PRICES
from zombiescan.engine import DEFAULT_REGIONS, filter_us_regions, resolve_regions, scan
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


def test_us_only_keeps_the_us_regions():
    assert filter_us_regions(REGIONS) == ["us-east-1"]
    assert filter_us_regions(["us-west-2", "us-gov-west-1", "eu-west-1"]) == [
        "us-west-2",
        "us-gov-west-1",
    ]


def test_us_only_refuses_to_scan_nothing():
    with pytest.raises(ValueError, match="none of eu-west-1, ap-south-1 is a US region"):
        filter_us_regions(["eu-west-1", "ap-south-1"])


def test_a_service_missing_from_a_region_is_not_an_error(pricing):
    """Lightsail has no endpoint in several regions; that is not a scan failure."""

    def fn(ctx):
        raise botocore.exceptions.EndpointConnectionError(
            endpoint_url=f"https://lightsail.{ctx.region}.amazonaws.com/"
        )
        yield  # pragma: no cover - generator marker

    result = scan(None, REGIONS, [CheckSpec(name="ls", title="ls", fn=fn)], pricing)
    assert result.errors == []
    assert result.unavailable == len(REGIONS)


def test_a_scan_that_found_nowhere_to_look_is_still_reported_as_incomplete(pricing):
    """Silently skipping everything must not read as a clean account."""

    def fn(ctx):
        raise botocore.exceptions.EndpointConnectionError(endpoint_url="https://x/")
        yield  # pragma: no cover - generator marker

    result = scan(None, REGIONS, [CheckSpec(name="ls", title="ls", fn=fn)], pricing)
    assert result.completely_failed is True


def test_a_scan_with_no_region_flags_covers_the_us_regions():
    """The default scope is four regions, not whichever one a profile names.

    Resolving it needs no session and no configured region: these four are
    enabled on every account, so nothing has to be asked.
    """
    assert resolve_regions(None, all_regions=False) == [
        "us-east-1",
        "us-east-2",
        "us-west-1",
        "us-west-2",
    ]
    assert list(DEFAULT_REGIONS) == resolve_regions(None, all_regions=False)


def test_all_regions_asks_the_account_which_ones_it_has_enabled():
    """Opted-out regions are excluded by AWS, so the answer is never guessed."""

    class FakeEc2:
        def describe_regions(self):
            return {"Regions": [{"RegionName": "eu-west-1"}, {"RegionName": "us-east-1"}]}

    class FakeSession:
        region_name = "eu-west-1"

        def client(self, service, region_name=None):
            assert service == "ec2"
            return FakeEc2()

    assert resolve_regions(FakeSession(), all_regions=True) == ["eu-west-1", "us-east-1"]
