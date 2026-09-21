"""Building checks declaratively.

Most of what a check does is the same every time: page through one describe
call, drop the items that are in use, and turn the rest into findings. The
part that is never the same is the judgement -- what makes this resource
waste, how to say so, and what it costs.

``simple_check`` takes the first part as data and leaves the second to you.
Use it when a check really is "list these, keep the ones matching this, price
them at this rate". Reach for a plain ``@check`` function the moment the check
needs to cross-reference a second API call, sum over sub-resources, or make a
judgement a predicate cannot express -- most of the checks in this repository
do, and forcing them through here would make them harder to read, not easier.

    from zombiescan.building import simple_check

    unused_widget = simple_check(
        "unused-widget",
        "Widgets nobody is using",
        service="widgets",
        operation="describe_widgets",
        result_key="Widgets",
        id_key="WidgetId",
        resource_type="widget",
        where=lambda w: w.get("State") == "idle",
        reason=lambda w, ctx: f"Widget has been idle since {w['IdleSince']}",
        remediation="aws widgets delete-widget --widget-id {id} --region {region}",
        rate="widget.month",
    )
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from zombiescan.models import Finding, ScanContext
from zombiescan.registry import CheckFn, check

# A value that may be read straight off the API item by key, or computed.
Getter = str | Callable[[dict[str, Any]], Any] | None


def paginated(
    ctx: ScanContext, service: str, operation: str, result_key: str, **params: Any
) -> Iterator[dict[str, Any]]:
    """Every item from a paginated describe call.

    Falls back to calling the operation directly when the service has no
    paginator for it, which is common for the smaller APIs.
    """
    client = ctx.client(service)
    if client.can_paginate(operation):
        for page in client.get_paginator(operation).paginate(**params):
            yield from page.get(result_key, [])
        return
    yield from getattr(client, operation)(**params).get(result_key, [])


def _read(item: dict[str, Any], getter: Getter, default: Any = None) -> Any:
    if getter is None:
        return default
    if callable(getter):
        return getter(item)
    return item.get(getter, default)


def simple_check(
    name: str,
    title: str,
    *,
    service: str,
    operation: str,
    result_key: str,
    id_key: Getter,
    resource_type: str,
    reason: str | Callable[[dict[str, Any], ScanContext], str],
    remediation: str | Callable[[dict[str, Any], ScanContext], str],
    rate: str | None = None,
    quantity: Getter = None,
    variant: Getter = None,
    where: Callable[[dict[str, Any]], bool] | None = None,
    details: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    params: dict[str, Any] | None = None,
    scope: str = "regional",
    uncleanable: str | None = None,
) -> CheckFn:
    """Register a check that is one describe call, one filter and one rate.

    ``rate`` is a key registered with ``zombiescan.pricing.rates``; the
    finding's cost is its price multiplied by ``quantity`` (1 by default, so a
    per-resource rate needs no quantity at all). With no ``rate`` the finding
    costs nothing, which is the right answer for the hygiene checks.

    ``reason`` and ``remediation`` are either format strings -- ``{id}`` and
    ``{region}`` are substituted, along with every key of the API item -- or
    callables taking ``(item, ctx)``.
    """

    def build_finding(ctx: ScanContext, item: dict[str, Any]) -> Finding:
        resource_id = _read(item, id_key)
        approximate = False
        monthly_cost = 0.0
        if rate is not None:
            price, approximate = ctx.pricing.rate(
                rate,
                **({"region": ctx.region} if scope != "global" else {}),
                **({"variant": _read(item, variant)} if variant is not None else {}),
            )
            monthly_cost = float(_read(item, quantity, 1.0) or 0.0) * price

        def render(template: str | Callable[[dict[str, Any], ScanContext], str]) -> str:
            if callable(template):
                return template(item, ctx)
            return template.format(id=resource_id, region=ctx.region, **item)

        return Finding(
            check=name,
            resource_id=resource_id,
            resource_type=resource_type,
            region=ctx.region if scope != "global" else "global",
            reason=render(reason),
            monthly_cost=monthly_cost,
            remediation=render(remediation),
            approximate_cost=approximate,
            details=dict(details(item)) if details else {},
        )

    @check(name, title, scope=scope, uncleanable=uncleanable)
    def run(ctx: ScanContext) -> Iterator[Finding]:
        for item in paginated(ctx, service, operation, result_key, **(params or {})):
            if where is None or where(item):
                yield build_finding(ctx, item)

    # Tests and cleaners reach for the finding builder directly, the same way
    # they do with a hand-written check.
    run.build_finding = build_finding  # type: ignore[attr-defined]
    return run
