from __future__ import annotations

import pytest

from tests.conftest import load_fixture
from zombiescan.checks.detached_internet_gateway import detached_internet_gateway


@pytest.fixture
def findings(make_context):
    ctx, _ = make_context(
        {"describe_internet_gateways": [load_fixture("detached_internet_gateway")]}
    )
    return {f.resource_id: f for f in detached_internet_gateway(ctx)}


def test_flags_the_detached_gateway(findings):
    assert set(findings) == {"igw-00000000000000001"}


def test_attached_gateway_is_kept(findings):
    assert "igw-00000000000000002" not in findings


def test_reported_as_free(findings):
    assert findings["igw-00000000000000001"].monthly_cost == 0.0


def test_quota_rationale_is_given(findings):
    """It costs nothing, so the reason to care has to be stated explicitly."""
    assert "quota" in findings["igw-00000000000000001"].details["note"]
