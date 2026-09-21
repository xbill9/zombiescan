from __future__ import annotations

import pytest

from tests.conftest import load_fixture
from zombiescan.packs.core.unmounted_efs import unmounted_efs


@pytest.fixture
def findings(make_context):
    ctx, _ = make_context({"describe_file_systems": [load_fixture("unmounted_efs")]})
    return {f.resource_id: f for f in unmounted_efs(ctx)}


def test_flags_the_file_system_nothing_can_reach(findings):
    assert set(findings) == {"fs-00000000000000001"}


def test_mounted_file_system_is_kept(findings):
    assert "fs-00000000000000002" not in findings


def test_file_system_still_being_created_is_skipped(findings):
    assert "fs-00000000000000003" not in findings


def test_only_standard_tier_bytes_are_priced(findings):
    """Infrequent Access bytes are billed at a different rate, so counting the
    total would overstate the saving."""
    standard_gb = 85899345920 / 1024**3
    assert findings["fs-00000000000000001"].monthly_cost == pytest.approx(standard_gb * 0.30)
    assert findings["fs-00000000000000001"].details["total_bytes"] == 107374182400


def test_tiering_gap_is_disclosed(findings):
    assert "Infrequent Access" in findings["fs-00000000000000001"].details["note"]
