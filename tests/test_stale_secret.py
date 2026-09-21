from __future__ import annotations

import datetime as dt

import pytest

from tests.conftest import load_fixture
from zombiescan.packs.core.stale_secret import stale_secret

# A secret is judged against a 90-day line, so the cases that sit near it are
# built relative to now. A date written into the fixture would cross the line
# on its own one day and start failing.


def _ago(days: int) -> dt.datetime:
    return dt.datetime.now(dt.UTC) - dt.timedelta(days=days)


RELATIVE_SECRETS = [
    # Created this week and not read yet, which is what every secret looks
    # like for its first few days.
    {
        "Name": "new/never-read",
        "ARN": "arn:aws:secretsmanager:us-east-1:111122223333:secret:e",
        "CreatedDate": _ago(5),
        "LastChangedDate": _ago(5),
    },
    # Old, but its value was replaced last week: somebody is looking after it.
    {
        "Name": "rewritten/never-read",
        "ARN": "arn:aws:secretsmanager:us-east-1:111122223333:secret:f",
        "CreatedDate": _ago(400),
        "LastChangedDate": _ago(10),
    },
    # Never read, and nothing has touched it since it was written either.
    {
        "Name": "old/rewritten-never-read",
        "ARN": "arn:aws:secretsmanager:us-east-1:111122223333:secret:g",
        "CreatedDate": _ago(500),
        "LastChangedDate": _ago(200),
    },
]


@pytest.fixture
def findings(make_context):
    data = load_fixture("stale_secret")
    secrets = [*data["SecretList"], *RELATIVE_SECRETS]
    ctx, _ = make_context({"list_secrets": [{"SecretList": secrets}]})
    return {f.resource_id: f for f in stale_secret(ctx)}


def test_flags_secrets_stale_or_never_read(findings):
    assert set(findings) == {
        "prod/retired-service/db",
        "never/used",
        "old/rewritten-never-read",
    }


def test_a_secret_created_this_week_is_not_waste_for_never_having_been_read(findings):
    """Every secret has never been retrieved at first. Judge it on its age."""
    assert "new/never-read" not in findings


def test_a_secret_written_recently_is_kept_even_if_nothing_reads_it(findings):
    """Replacing the value is somebody looking after it, whoever reads it."""
    assert "rewritten/never-read" not in findings


def test_a_never_read_secret_is_measured_from_when_it_was_last_written(findings):
    finding = findings["old/rewritten-never-read"]
    assert "never been retrieved" in finding.reason
    assert "200 days since it was written" in finding.reason


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
