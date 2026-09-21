from __future__ import annotations

import pytest

from tests.conftest import load_fixture
from zombiescan.checks.stale_secret import stale_secret


@pytest.fixture
def findings(make_context):
    ctx, _ = make_context({"list_secrets": [load_fixture("stale_secret")]})
    return {f.resource_id: f for f in stale_secret(ctx)}


def test_flags_secrets_stale_or_never_read(findings):
    assert set(findings) == {"prod/retired-service/db", "never/used"}


def test_recently_read_secret_is_kept(findings):
    assert "prod/active/api-key" not in findings


def test_secret_already_scheduled_for_deletion_is_skipped(findings):
    assert "already/going" not in findings


def test_never_accessed_reads_differently_from_merely_stale(findings):
    assert "never been retrieved" in findings["never/used"].reason
    assert findings["never/used"].details["days_since_access"] is None
    assert "not been retrieved in" in findings["prod/retired-service/db"].reason


def test_costs_the_flat_per_secret_charge(findings):
    assert findings["never/used"].monthly_cost == pytest.approx(0.40)


def test_deletion_keeps_a_recovery_window(findings):
    """A 30-day window is the difference between a cleanup and an outage."""
    assert "--recovery-window-in-days 30" in findings["never/used"].remediation


def test_dormant_by_design_caveat_is_stated(findings):
    note = findings["never/used"].details["note"]
    assert "break-glass" in note and "disaster-recovery" in note
