from __future__ import annotations

import pytest

from tests.conftest import load_fixture
from zombiescan.checks.idle_provisioned_dynamodb import idle_provisioned_dynamodb


@pytest.fixture
def findings(make_context):
    data = load_fixture("idle_provisioned_dynamodb")
    tables = data["TablesByName"]
    ctx, _ = make_context(
        pages={"list_tables": [{"TableNames": data["TableNames"]}]},
        direct={"describe_table": lambda TableName: {"Table": tables[TableName]}},
    )
    return {f.resource_id: f for f in idle_provisioned_dynamodb(ctx)}


def test_flags_the_empty_provisioned_table(findings):
    assert set(findings) == {"empty-prototype"}


def test_table_with_items_is_kept(findings):
    assert "busy-table" not in findings


def test_on_demand_table_is_never_waste_when_empty(findings):
    """On-demand costs nothing when nothing reads or writes it."""
    assert "on-demand-empty" not in findings


def test_table_provisioned_at_zero_costs_nothing(findings):
    assert "zero-capacity" not in findings


def test_cost_is_read_and_write_capacity_for_a_month(findings):
    expected = (25 * 0.00013 + 25 * 0.00065) * 730
    assert findings["empty-prototype"].monthly_cost == pytest.approx(expected)


def test_item_count_staleness_is_disclosed(findings):
    """ItemCount lags by hours, so a freshly filled table can read as empty."""
    assert "six hours" in findings["empty-prototype"].details["note"]


def test_remediation_offers_on_demand_as_well_as_deletion(findings):
    assert "PAY_PER_REQUEST" in findings["empty-prototype"].remediation
