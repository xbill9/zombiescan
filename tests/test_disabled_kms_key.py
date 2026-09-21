from __future__ import annotations

import pytest

from tests.conftest import load_fixture
from zombiescan.checks.disabled_kms_key import disabled_kms_key


@pytest.fixture
def findings(make_context):
    data = load_fixture("disabled_kms_key")
    metadata = data["KeyMetadataById"]
    ctx, _ = make_context(
        pages={
            "list_keys": [{"Keys": data["Keys"]}],
            "list_aliases": [{"Aliases": data["Aliases"]}],
        },
        direct={"describe_key": lambda KeyId: {"KeyMetadata": metadata[KeyId]}},
    )
    return {f.resource_id: f for f in disabled_kms_key(ctx)}


def test_flags_the_disabled_customer_key(findings):
    assert set(findings) == {"11111111-1111-1111-1111-111111111111"}


def test_enabled_key_is_kept(findings):
    assert "22222222-2222-2222-2222-222222222222" not in findings


def test_key_already_scheduled_for_deletion_is_not_reported(findings):
    """It is leaving on a timer; reporting it is noise."""
    assert "33333333-3333-3333-3333-333333333333" not in findings


def test_aws_managed_key_is_never_reported(findings):
    """AWS managed keys are free, so a disabled one costs nothing."""
    assert "44444444-4444-4444-4444-444444444444" not in findings


def test_costs_the_full_monthly_key_charge(findings):
    assert findings["11111111-1111-1111-1111-111111111111"].monthly_cost == pytest.approx(1.0)


def test_alias_is_used_as_the_human_label(findings):
    finding = findings["11111111-1111-1111-1111-111111111111"]
    assert "alias/retired-app" in finding.reason
    assert finding.details["aliases"] == ["alias/retired-app"]


def test_irreversibility_is_spelled_out(findings):
    note = findings["11111111-1111-1111-1111-111111111111"].details["note"]
    assert "irreversible" in note and "unrecoverable" in note
