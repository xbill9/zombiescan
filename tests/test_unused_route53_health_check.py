from __future__ import annotations

import pytest

from tests.conftest import load_fixture
from zombiescan.checks.unused_route53_health_check import unused_route53_health_check
from zombiescan.registry import CHECKS


@pytest.fixture
def findings(make_context):
    data = load_fixture("unused_route53_health_check")
    records = data["RecordSetsByZone"]
    ctx, _ = make_context(
        {
            "list_health_checks": [{"HealthChecks": data["HealthChecks"]}],
            "list_hosted_zones": [{"HostedZones": data["HostedZones"]}],
            "list_resource_record_sets": lambda HostedZoneId: [
                {"ResourceRecordSets": records.get(HostedZoneId, [])}
            ],
        }
    )
    return {f.resource_id: f for f in unused_route53_health_check(ctx)}


def test_flags_the_health_check_no_record_names(findings):
    assert set(findings) == {"abc-orphaned"}


def test_health_check_on_a_record_is_kept(findings):
    assert "def-in-use" not in findings


def test_health_check_referenced_by_an_alias_target_is_kept(findings):
    """AliasTarget carries its own HealthCheckId; missing it would delete a live check."""
    assert "ghi-alias-target" not in findings


def test_findings_are_labelled_global_not_regional(findings):
    assert findings["abc-orphaned"].region == "global"


def test_priced_at_the_aws_endpoint_rate_and_says_so(findings):
    finding = findings["abc-orphaned"]
    assert finding.monthly_cost == pytest.approx(0.50)
    assert finding.approximate_cost is True
    assert "non-AWS endpoints" in finding.details["note"]


def test_cloudwatch_alarm_blind_spot_is_disclosed(findings):
    assert "CloudWatch alarm" in findings["abc-orphaned"].details["note"]


def test_the_check_is_registered_as_global():
    assert CHECKS["unused-route53-health-check"].is_global
