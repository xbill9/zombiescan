from __future__ import annotations

import pytest

from tests.conftest import load_fixture
from zombiescan.checks.available_eni import available_eni


@pytest.fixture
def result(make_context):
    ctx, client = make_context({"describe_network_interfaces": [load_fixture("available_eni")]})
    return {f.resource_id: f for f in available_eni(ctx)}, client


def test_flags_every_available_interface(result):
    findings, _ = result
    assert set(findings) == {"eni-00000000000000001", "eni-00000000000000002"}


def test_queries_only_available_interfaces(result):
    _, client = result
    assert client.calls["describe_network_interfaces"] == {
        "Filters": [{"Name": "status", "Values": ["available"]}]
    }


def test_reported_as_free_not_guessed(result):
    """These genuinely cost nothing; inventing a number would be dishonest."""
    findings, _ = result
    assert all(f.monthly_cost == 0.0 for f in findings.values())


def test_description_explains_the_origin(result):
    findings, _ = result
    assert "my-deleted-fn" in findings["eni-00000000000000001"].reason


def test_blank_description_is_not_appended(result):
    findings, _ = result
    assert findings["eni-00000000000000002"].reason.endswith("('available')")
    assert findings["eni-00000000000000002"].details["description"] is None
