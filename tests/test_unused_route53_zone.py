from __future__ import annotations

import pytest

from tests.conftest import load_fixture
from zombiescan.packs.core.unused_route53_zone import unused_route53_zone
from zombiescan.registry import CHECKS


@pytest.fixture
def findings(make_context):
    data = load_fixture("unused_route53_zone")
    ctx, _ = make_context({"list_hosted_zones": [{"HostedZones": data["HostedZones"]}]})
    return {f.resource_id: f for f in unused_route53_zone(ctx)}


def test_flags_the_zones_holding_only_their_default_records(findings):
    assert set(findings) == {"Z00EMPTYPRIVATE", "Z11EMPTYPUBLIC", "Z44CLOUDMAP"}


def test_a_populated_zone_is_kept(findings):
    assert "Z22INUSE" not in findings


def test_one_real_record_is_enough_to_keep_a_zone(findings):
    """SOA, NS and a single TXT is a domain verification, not an abandoned zone."""
    assert "Z33ONERECORD" not in findings


def test_priced_at_the_first_tier_rate_for_a_small_account(findings):
    assert findings["Z11EMPTYPUBLIC"].monthly_cost == pytest.approx(0.50)
    assert findings["Z11EMPTYPUBLIC"].approximate_cost is False


def test_findings_are_labelled_global_not_regional(findings):
    assert findings["Z00EMPTYPRIVATE"].region == "global"


def test_the_reason_names_the_zone_and_whether_it_is_private(findings):
    assert "Private" in findings["Z00EMPTYPRIVATE"].reason
    assert "agent.local." in findings["Z00EMPTYPRIVATE"].reason
    assert "Public" in findings["Z11EMPTYPUBLIC"].reason


def test_the_remediation_uses_the_bare_zone_id(findings):
    """delete-hosted-zone takes the id, not the "/hostedzone/Z..." path."""
    assert findings["Z11EMPTYPUBLIC"].remediation == (
        "aws route53 delete-hosted-zone --id Z11EMPTYPUBLIC"
    )


def test_the_delegation_cost_of_deleting_a_public_zone_is_disclosed(findings):
    assert "delegated" in findings["Z11EMPTYPUBLIC"].details["note"]


def test_an_account_past_the_free_tier_is_priced_at_the_marginal_rate(make_context):
    """The 26th zone costs $0.10, so removing one from a big account saves $0.10."""
    zones = [
        {
            "Id": f"/hostedzone/Z{index:02d}",
            "Name": f"zone-{index}.example.com.",
            "Config": {"PrivateZone": False},
            "ResourceRecordSetCount": 2,
        }
        for index in range(30)
    ]
    ctx, _ = make_context({"list_hosted_zones": [{"HostedZones": zones}]})
    findings = list(unused_route53_zone(ctx))

    assert len(findings) == 30
    assert all(f.monthly_cost == pytest.approx(0.10) for f in findings)
    assert findings[0].details["priced_as"] == "additional"
    assert findings[0].details["zones_in_account"] == 30


def test_a_cloud_map_zone_is_remediated_through_its_namespace(findings):
    """Deleting the zone under a namespace orphans the namespace; delete that instead."""
    finding = findings["Z44CLOUDMAP"]
    assert finding.remediation == (
        "aws servicediscovery delete-namespace --id ns-abcdefghij123456 --region eu-west-1"
    )
    assert finding.details["cloud_map_namespace"] == "ns-abcdefghij123456"
    assert finding.details["cloud_map_region"] == "eu-west-1"
    assert "registers nothing" in finding.reason


def test_the_cloud_map_cleaner_calls_the_namespace_in_its_own_region(findings):
    from zombiescan.packs.core.cleaners import clean_unused_route53_zone

    steps = list(clean_unused_route53_zone(None, findings["Z44CLOUDMAP"]))
    assert len(steps) == 1
    step = steps[0]
    assert (step.service, step.operation) == ("servicediscovery", "delete_namespace")
    assert step.params == {"Id": "ns-abcdefghij123456"}
    # The zone is global, the namespace is not: without this the call would go
    # to whichever region the operator happens to be configured for.
    assert step.region == "eu-west-1"
    assert step.irreversible is True


def test_an_ordinary_zone_is_still_cleaned_through_route53(findings):
    from zombiescan.packs.core.cleaners import clean_unused_route53_zone

    steps = list(clean_unused_route53_zone(None, findings["Z11EMPTYPUBLIC"]))
    assert (steps[0].service, steps[0].operation) == ("route53", "delete_hosted_zone")
    assert steps[0].region is None


def test_the_check_is_registered_as_global():
    assert CHECKS["unused-route53-zone"].is_global
