"""Cleanup planning and execution.

The tests that matter here are not "does it delete things" but "does it
refuse to delete things at the wrong moment": after a failed backup, on the
strength of a failed scan, or against the wrong account.
"""

from __future__ import annotations

import boto3
import pytest

from tests.conftest import TEST_PRICES
from zombiescan import clean
from zombiescan.cleaners import CLEANERS, UNCLEANABLE
from zombiescan.models import Finding
from zombiescan.pricing import PriceTable
from zombiescan.registry import CHECKS


class Recorder:
    """A client that records calls instead of making them.

    ``fail_on`` makes one operation raise, which is how the ordering
    guarantees get tested.
    """

    def __init__(self, log, service, fail_on=None):
        self._log = log
        self._service = service
        self._fail_on = fail_on

    def __getattr__(self, operation):
        def call(**params):
            self._log.append((self._service, operation, params))
            if operation == self._fail_on:
                raise RuntimeError(f"{operation} refused")
            return {"ResponseMetadata": {"HTTPStatusCode": 200}, "SnapshotId": "snap-new"}

        return call


class FakeSession:
    def __init__(self, fail_on=None):
        self.log: list[tuple] = []
        self.regions: list[str] = []
        self._fail_on = fail_on

    def client(self, service, region_name=None):
        self.regions.append(region_name)
        return Recorder(self.log, service, self._fail_on)


@pytest.fixture
def pricing():
    return PriceTable(TEST_PRICES)


def _finding(check, rid="r-1", region="us-east-1", cost=10.0, details=None):
    return Finding(
        check=check,
        resource_id=rid,
        resource_type="thing",
        region=region,
        reason="because",
        monthly_cost=cost,
        remediation="aws ...",
        details=details or {},
    )


# --- planning ------------------------------------------------------------


def test_every_check_either_has_a_cleaner_or_says_why_not():
    """A check with no cleaner and no explanation is an oversight, not a decision."""
    for name in CHECKS:
        assert name in CLEANERS or name in UNCLEANABLE, f"{name} has neither"


def test_a_volume_is_snapshotted_before_it_is_deleted(pricing):
    outcome = clean.plan_for(
        FakeSession(), _finding("unattached-ebs", "vol-1"), pricing, "us-east-1"
    )
    assert [s.operation for s in outcome.steps] == ["create_snapshot", "delete_volume"]
    assert outcome.steps[0].irreversible is False


def test_an_instance_is_imaged_before_it_is_terminated(pricing):
    outcome = clean.plan_for(
        FakeSession(), _finding("stopped-instance", "i-1"), pricing, "us-east-1"
    )
    assert [s.operation for s in outcome.steps] == ["create_image", "terminate_instances"]
    assert outcome.steps[-1].irreversible is True


def test_a_database_is_deleted_with_a_final_snapshot(pricing):
    outcome = clean.plan_for(
        FakeSession(), _finding("stopped-rds-instance", "db-1"), pricing, "us-east-1"
    )
    (step,) = outcome.steps
    assert step.params["SkipFinalSnapshot"] is False
    assert step.params["FinalDBSnapshotIdentifier"].startswith("db-1-zombiescan-")


def test_an_ami_takes_its_snapshots_with_it(pricing):
    """Deregistering and leaving the snapshots is the bug the finding reports."""
    finding = _finding("unused-ami", "ami-1", details={"snapshot_ids": ["snap-a", "snap-b"]})
    outcome = clean.plan_for(FakeSession(), finding, pricing, "us-east-1")
    assert [s.operation for s in outcome.steps] == [
        "deregister_image",
        "delete_snapshot",
        "delete_snapshot",
    ]


def test_classic_elastic_ip_is_released_by_ip_not_allocation_id(pricing):
    finding = _finding("unassociated-eip", "203.0.113.9", details={"public_ip": "203.0.113.9"})
    (step,) = clean.plan_for(FakeSession(), finding, pricing, "us-east-1").steps
    assert step.params == {"PublicIp": "203.0.113.9"}


def test_an_empty_vpc_is_refused_with_a_reason(pricing):
    outcome = clean.plan_for(FakeSession(), _finding("empty-vpc", "vpc-1"), pricing, "us-east-1")
    assert outcome.status == clean.UNSUPPORTED
    assert "subnets" in outcome.error


def test_an_unknown_check_is_refused_not_guessed(pricing):
    outcome = clean.plan_for(FakeSession(), _finding("invented-check"), pricing, "us-east-1")
    assert outcome.status == clean.UNSUPPORTED


def test_planning_makes_no_mutating_calls(pricing):
    """A dry run that mutates is not a dry run."""
    session = FakeSession()
    for name in CLEANERS:
        clean.plan_for(
            session,
            _finding(name, details={"snapshot_ids": ["s-1"], "arn": "arn:x"}),
            pricing,
            "us-east-1",
        )
    mutating = [
        c
        for c in session.log
        if c[1].split("_")[0]
        in ("delete", "terminate", "release", "deregister", "abort", "put", "schedule", "create")
    ]
    assert mutating == []


# --- execution -----------------------------------------------------------


def test_applying_sends_every_planned_step(pricing):
    session = FakeSession()
    outcome = clean.plan_for(session, _finding("unattached-ebs", "vol-1"), pricing, "us-east-1")
    clean.apply_outcome(outcome, session, "us-east-1")
    assert outcome.status == clean.APPLIED
    assert [op for _, op, _ in session.log] == ["create_snapshot", "delete_volume"]


def test_a_failed_backup_stops_the_deletion(pricing):
    """The point of snapshotting first is lost if the delete runs anyway."""
    session = FakeSession(fail_on="create_snapshot")
    outcome = clean.plan_for(session, _finding("unattached-ebs", "vol-1"), pricing, "us-east-1")
    clean.apply_outcome(outcome, session, "us-east-1")
    assert outcome.status == clean.FAILED
    assert "delete_volume" not in [op for _, op, _ in session.log]


def test_a_failed_image_stops_the_termination(pricing):
    session = FakeSession(fail_on="create_image")
    outcome = clean.plan_for(session, _finding("stopped-instance", "i-1"), pricing, "us-east-1")
    clean.apply_outcome(outcome, session, "us-east-1")
    assert outcome.status == clean.FAILED
    assert "terminate_instances" not in [op for _, op, _ in session.log]


def test_a_failure_is_recorded_not_raised(pricing):
    session = FakeSession(fail_on="delete_security_group")
    outcome = clean.plan_for(
        session, _finding("unused-security-group", "sg-1"), pricing, "us-east-1"
    )
    clean.apply_outcome(outcome, session, "us-east-1")
    assert outcome.status == clean.FAILED
    assert "refused" in outcome.error


def test_global_findings_are_cleaned_in_the_home_region(pricing):
    """ "global" is not a region a client can be pointed at."""
    session = FakeSession()
    finding = _finding("unused-route53-health-check", "hc-1", region="global")
    outcome = clean.plan_for(session, finding, pricing, "eu-west-1")
    clean.apply_outcome(outcome, session, "eu-west-1")
    assert outcome.status == clean.APPLIED
    assert "global" not in session.regions
    assert "eu-west-1" in session.regions


def test_only_applied_findings_count_as_savings(pricing):
    session = FakeSession()
    outcome = clean.plan_for(
        session, _finding("unused-security-group", cost=42.0), pricing, "us-east-1"
    )
    assert outcome.monthly_saving == 0.0
    clean.apply_outcome(outcome, session, "us-east-1")
    assert outcome.monthly_saving == 42.0


# --- audit ---------------------------------------------------------------


def test_audit_records_mode_and_irreversibility(pricing):
    session = FakeSession()
    outcomes = [
        clean.plan_for(session, _finding("unattached-ebs", "vol-1"), pricing, "us-east-1"),
        clean.plan_for(session, _finding("empty-vpc", "vpc-1"), pricing, "us-east-1"),
    ]
    doc = clean.audit_document(outcomes, applied=False, caller_arn="arn:aws:iam::1:root")
    assert doc["mode"] == "dry-run"
    assert doc["counts"] == {clean.PLANNED: 1, clean.UNSUPPORTED: 1}
    assert doc["actions"][0]["steps"][0]["operation"] == "create_snapshot"


def test_a_backed_up_deletion_is_not_flagged_irreversible(pricing):
    """Deleting a volume after snapshotting it is recoverable; deleting the
    snapshot is not. The flag has to tell those apart or it means nothing."""
    session = FakeSession()
    volume = clean.plan_for(session, _finding("unattached-ebs", "vol-1"), pricing, "us-east-1")
    snapshot = clean.plan_for(
        session, _finding("orphaned-snapshot", "snap-1"), pricing, "us-east-1"
    )
    assert volume.irreversible is False
    assert snapshot.irreversible is True


def test_audit_totals_only_what_was_applied(pricing):
    session = FakeSession()
    outcome = clean.plan_for(
        session, _finding("unused-security-group", cost=7.0), pricing, "us-east-1"
    )
    clean.apply_outcome(outcome, session, "us-east-1")
    assert clean.audit_document([outcome], applied=True)["monthly_saving"] == 7.0


# --- round trip ----------------------------------------------------------


def test_a_finding_survives_the_json_round_trip():
    """`clean --from` must act on exactly the report a human reviewed."""
    original = _finding("unused-ami", "ami-1", details={"snapshot_ids": ["snap-a"]})
    restored = Finding.from_dict(original.to_dict())
    assert restored.check == original.check
    assert restored.resource_id == original.resource_id
    assert restored.details["snapshot_ids"] == ["snap-a"]


def test_real_session_is_never_constructed_in_these_tests():
    """Guard against a test accidentally reaching AWS."""
    assert not isinstance(FakeSession(), boto3.Session)


# --- lightsail -----------------------------------------------------------


def test_a_lightsail_instance_is_snapshotted_before_it_is_deleted(pricing):
    """Deleting takes the system disk with it, so the snapshot has to land first."""
    outcome = clean.plan_for(
        FakeSession(), _finding("lightsail-stopped-instance", "retired-blog"), pricing, "us-east-1"
    )
    assert [s.operation for s in outcome.steps] == [
        "create_instance_snapshot",
        "delete_instance",
    ]
    assert outcome.steps[0].irreversible is False


def test_a_lightsail_disk_is_snapshotted_before_it_is_deleted(pricing):
    outcome = clean.plan_for(
        FakeSession(), _finding("lightsail-unattached-disk", "detached-data"), pricing, "us-east-1"
    )
    assert [s.operation for s in outcome.steps] == ["create_disk_snapshot", "delete_disk"]


def test_an_idle_container_service_is_disabled_not_deleted(pricing):
    """Disabling stops the billing and keeps the name and URL; deleting frees them."""
    outcome = clean.plan_for(
        FakeSession(),
        _finding("lightsail-idle-container-service", "never-deployed"),
        pricing,
        "us-east-1",
    )
    assert [s.operation for s in outcome.steps] == ["update_container_service"]
    assert outcome.steps[0].params["isDisabled"] is True
    assert outcome.steps[0].irreversible is False


def test_releasing_a_static_ip_is_marked_irreversible(pricing):
    outcome = clean.plan_for(
        FakeSession(),
        _finding("lightsail-unattached-static-ip", "orphaned-ip"),
        pricing,
        "us-east-1",
    )
    assert outcome.steps[0].operation == "release_static_ip"
    assert outcome.steps[0].irreversible is True


@pytest.mark.parametrize(
    ("kind", "operation", "key"),
    [
        ("instance", "delete_instance_snapshot", "instanceSnapshotName"),
        ("disk", "delete_disk_snapshot", "diskSnapshotName"),
    ],
)
def test_a_lightsail_snapshot_is_deleted_by_its_own_kind(pricing, kind, operation, key):
    """One finding type covers both, so the plan has to pick the matching call."""
    outcome = clean.plan_for(
        FakeSession(),
        _finding("lightsail-orphaned-snapshot", "snap-1", details={"source_kind": kind}),
        pricing,
        "us-east-1",
    )
    assert outcome.steps[0].operation == operation
    assert outcome.steps[0].params == {key: "snap-1"}
    assert outcome.steps[0].irreversible is True
