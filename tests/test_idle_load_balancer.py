from __future__ import annotations

import pytest

from tests.conftest import load_fixture
from zombiescan.checks.idle_load_balancer import idle_load_balancer


@pytest.fixture
def result(make_context):
    data = load_fixture("idle_load_balancer")
    groups_by_lb = data["TargetGroupsByLb"]
    health_by_tg = data["TargetHealthByTg"]

    ctx, client = make_context(
        pages={
            "describe_load_balancers": [{"LoadBalancers": data["LoadBalancers"]}],
            # Answers per load balancer, the way the real API does.
            "describe_target_groups": lambda LoadBalancerArn: [
                {"TargetGroups": groups_by_lb.get(LoadBalancerArn, [])}
            ],
        },
        direct={
            "describe_target_health": lambda TargetGroupArn: {
                "TargetHealthDescriptions": health_by_tg.get(TargetGroupArn, [])
            }
        },
    )
    return {f.resource_id: f for f in idle_load_balancer(ctx)}, client


def test_flags_balancers_with_no_registered_targets(result):
    findings, _ = result
    assert set(findings) == {"idle-alb", "bare-nlb"}


def test_balancer_with_a_registered_target_is_kept(result):
    """The target is unhealthy, which is an outage -- not waste. Must not flag."""
    findings, _ = result
    assert "busy-nlb" not in findings


def test_balancer_still_provisioning_is_skipped(result):
    findings, _ = result
    assert "coming-up" not in findings


def test_alb_and_nlb_are_priced_by_their_own_rate(result):
    findings, _ = result
    assert findings["idle-alb"].monthly_cost == pytest.approx(0.0225 * 730)
    assert findings["bare-nlb"].monthly_cost == pytest.approx(0.0225 * 730)


def test_no_target_groups_is_described_differently(result):
    findings, _ = result
    assert "no target groups attached" in findings["bare-nlb"].reason
    assert "none with a registered target" in findings["idle-alb"].reason


def test_classic_elb_gap_is_disclosed(result):
    findings, _ = result
    assert "Classic" in findings["idle-alb"].details["note"]


def test_remediation_uses_the_arn(result):
    findings, _ = result
    assert findings["idle-alb"].details["arn"] in findings["idle-alb"].remediation
