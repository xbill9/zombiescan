"""The declarative check builder.

The two checks built with ``simple_check`` are covered by their own test files
already -- those tests did not change when the checks were converted, which is
the real evidence the builder produces the same findings as hand-written code.
What is tested here is the builder's own behaviour: the parts a pack author
would get wrong.
"""

from __future__ import annotations

import pytest

from zombiescan.building import paginated, simple_check
from zombiescan.registry import CHECKS


@pytest.fixture(autouse=True)
def _drop_test_checks():
    """Registering a check is global; these are not real ones."""
    before = set(CHECKS)
    yield
    for name in set(CHECKS) - before:
        del CHECKS[name]


def _widgets(make_context, items):
    """A context whose only API call returns these widgets."""
    return make_context({"describe_widgets": [{"Widgets": items}]})


def test_it_pages_and_filters(make_context):
    check = simple_check(
        "t-filter",
        "Widgets",
        service="widgets",
        operation="describe_widgets",
        result_key="Widgets",
        id_key="WidgetId",
        resource_type="widget",
        where=lambda w: w["State"] == "idle",
        reason="idle",
        remediation="delete {id}",
    )
    ctx, _ = _widgets(
        make_context,
        [
            {"WidgetId": "w-1", "State": "idle"},
            {"WidgetId": "w-2", "State": "busy"},
        ],
    )

    findings = list(check(ctx))

    assert [f.resource_id for f in findings] == ["w-1"]


def test_templates_see_the_item_the_id_and_the_region(make_context):
    check = simple_check(
        "t-template",
        "Widgets",
        service="widgets",
        operation="describe_widgets",
        result_key="Widgets",
        id_key="WidgetId",
        resource_type="widget",
        reason="{Colour} widget is unused",
        remediation="aws widgets delete --id {id} --region {region}",
    )
    ctx, _ = _widgets(make_context, [{"WidgetId": "w-1", "Colour": "green"}])

    finding = next(iter(check(ctx)))

    assert finding.reason == "green widget is unused"
    assert finding.remediation == "aws widgets delete --id w-1 --region us-east-1"


def test_cost_is_quantity_times_rate(make_context):
    check = simple_check(
        "t-cost",
        "Volumes",
        service="widgets",
        operation="describe_widgets",
        result_key="Widgets",
        id_key="WidgetId",
        resource_type="widget",
        rate="snapshot.gb_month",
        quantity="Size",
        reason="unused",
        remediation="delete {id}",
    )
    ctx, _ = _widgets(make_context, [{"WidgetId": "w-1", "Size": 100}])

    finding = next(iter(check(ctx)))

    # 100 GB at the pinned test rate of $0.05/GB-month.
    assert finding.monthly_cost == pytest.approx(5.0)
    assert finding.approximate_cost is False


def test_a_rate_fallback_marks_the_finding_approximate(make_context):
    """A price borrowed from another region must never be presented as fact."""
    check = simple_check(
        "t-approx",
        "Volumes",
        service="widgets",
        operation="describe_widgets",
        result_key="Widgets",
        id_key="WidgetId",
        resource_type="widget",
        rate="ebs.gb_month",
        quantity="Size",
        variant="VolumeType",
        reason="unused",
        remediation="delete {id}",
    )
    ctx, _ = _widgets(make_context, [{"WidgetId": "w-1", "Size": 10, "VolumeType": "st1"}])

    finding = next(iter(check(ctx)))

    assert finding.approximate_cost is True


def test_no_rate_means_free_not_unknown(make_context):
    check = simple_check(
        "t-free",
        "Hygiene",
        service="widgets",
        operation="describe_widgets",
        result_key="Widgets",
        id_key="WidgetId",
        resource_type="widget",
        reason="clutter",
        remediation="delete {id}",
    )
    ctx, _ = _widgets(make_context, [{"WidgetId": "w-1"}])

    finding = next(iter(check(ctx)))

    assert finding.monthly_cost == 0.0
    assert finding.approximate_cost is False


def test_params_are_passed_to_the_api_call(make_context):
    """Server-side filtering is the difference between one call and thousands."""
    check = simple_check(
        "t-params",
        "Widgets",
        service="widgets",
        operation="describe_widgets",
        result_key="Widgets",
        id_key="WidgetId",
        resource_type="widget",
        params={"Filters": [{"Name": "state", "Values": ["idle"]}]},
        reason="idle",
        remediation="delete {id}",
    )
    ctx, client = _widgets(make_context, [{"WidgetId": "w-1"}])

    list(check(ctx))

    assert client.calls["describe_widgets"] == {"Filters": [{"Name": "state", "Values": ["idle"]}]}


def test_the_registered_check_carries_its_pack_and_reason(make_context):
    simple_check(
        "t-uncleanable",
        "Widgets",
        service="widgets",
        operation="describe_widgets",
        result_key="Widgets",
        id_key="WidgetId",
        resource_type="widget",
        reason="idle",
        remediation="delete {id}",
        uncleanable="deleting a widget cannot be undone",
    )

    spec = CHECKS["t-uncleanable"]
    assert spec.uncleanable == "deleting a widget cannot be undone"
    assert spec.pack == "core"


def test_paginated_falls_back_when_the_operation_has_no_paginator(make_context):
    """Several of the smaller AWS APIs have no paginator for a list call."""
    ctx, _ = make_context(direct={"describe_widgets": {"Widgets": [{"WidgetId": "w-9"}]}})

    items = list(paginated(ctx, "widgets", "describe_widgets", "Widgets"))

    assert [i["WidgetId"] for i in items] == ["w-9"]
