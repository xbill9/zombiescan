from __future__ import annotations

import pytest

from tests.conftest import load_fixture
from zombiescan.checks.unused_security_group import unused_security_group


@pytest.fixture
def findings(make_context):
    data = load_fixture("unused_security_group")
    ctx, _ = make_context(
        {
            "describe_security_groups": [{"SecurityGroups": data["SecurityGroups"]}],
            "describe_network_interfaces": [{"NetworkInterfaces": data["NetworkInterfaces"]}],
        }
    )
    return {f.resource_id: f for f in unused_security_group(ctx)}


def test_flags_the_genuinely_unused_groups(findings):
    assert set(findings) == {
        "sg-00000000000000001",
        "sg-00000000000000004",
        "sg-00000000000000006",
    }


def test_group_attached_to_an_interface_is_kept(findings):
    assert "sg-00000000000000002" not in findings


def test_group_referenced_by_another_groups_rules_is_kept(findings):
    """Deleting it would fail; AWS refuses while a rule still names it."""
    assert "sg-00000000000000003" not in findings


def test_default_group_is_never_reported(findings):
    """The default group cannot be deleted, so reporting it is pure noise."""
    assert "sg-00000000000000005" not in findings


def test_self_reference_does_not_count_as_use(findings):
    """A group naming only itself is still used by nothing."""
    assert "sg-00000000000000006" in findings


def test_reported_as_free(findings):
    assert all(f.monthly_cost == 0.0 for f in findings.values())
