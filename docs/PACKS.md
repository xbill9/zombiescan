# Writing a zombiescan pack

A **pack** is a unit of scan coverage: a set of checks, the cleaners that
remove what they find, the rates that price them, and the fetchers that refresh
those rates. The checks that ship with zombiescan are packs (`core`,
`lightsail`) and load through exactly the same path as one you install, so
nothing here is a special case reserved for built-ins.

```
$ zombiescan packs

  Pack        Version   Checks   Source
 ─────────────────────────────────────────
  core        0.1.0     23       built-in
  lightsail   0.1.0     6        built-in

pack API v1
```

## Before you write one

**A pack is code, and it runs with your AWS credentials.** Nothing sandboxes
it, and nothing verifies that its checks only read. `zombiescan scan` promises
to make Describe/List/Get calls only; that promise is kept by the people who
write the checks, not by the machinery that runs them. Install packs on the
same judgement you would apply to any other dependency, and read a pack's
checks before you trust it against a production account.

If you are adding coverage to zombiescan itself rather than shipping your own
distribution, you do not need a pack at all — add a module to
`src/zombiescan/packs/core/`. It is picked up by existing.

## The shape

```
zombiescan-pack-acme/
  pyproject.toml
  src/zombiescan_pack_acme/
    __init__.py       registers the pack, then imports the rest
    rates.py          rate specs, if the pack prices anything new
    refresh.py        where those rates come from
    cleaners.py       how to remove what the checks find
    widgets.py        a check
```

Declare the entry point so zombiescan can find it:

```toml
[project.entry-points."zombiescan.packs"]
acme = "zombiescan_pack_acme"

[project.dependencies]
zombiescan = ">=0.1"
```

The entry point **name** is the pack name users will see and pass to
`--disable-pack`. The **value** is the module that registers it.

### `__init__.py`

```python
from zombiescan.packs import import_pack_modules, register_pack

register_pack(
    "acme",
    version="1.0.0",
    description="Waste checks for ACME's own services",
    homepage="https://github.com/acme/zombiescan-pack-acme",
)

from zombiescan_pack_acme import rates  # noqa: E402,F401

import_pack_modules(__name__, __path__)
```

`register_pack` must come first: everything registered afterwards is attributed
to the pack that is currently loading. Rates are imported explicitly before the
checks, because a check prices itself at import time is not a thing — but a
check module that referenced an unregistered rate key would fail on its first
run rather than at load, which is a worse place to find out.

## Writing a check

Either register a function:

```python
from collections.abc import Iterator

from zombiescan.models import Finding, ScanContext
from zombiescan.registry import check

CHECK_NAME = "acme-idle-widget"


@check(CHECK_NAME, "Widgets nobody is using")
def idle_widget(ctx: ScanContext) -> Iterator[Finding]:
    for page in ctx.client("widgets").get_paginator("describe_widgets").paginate():
        for widget in page.get("Widgets", []):
            if widget["State"] != "idle":
                continue
            price, approximate = ctx.pricing.rate("acme.widget_month", region=ctx.region)
            yield Finding(
                check=CHECK_NAME,
                resource_id=widget["WidgetId"],
                resource_type="widget",
                region=ctx.region,
                reason=f"Widget has been idle since {widget['IdleSince']}",
                monthly_cost=price,
                remediation=f"aws widgets delete-widget --widget-id {widget['WidgetId']}",
                approximate_cost=approximate,
                details={"idle_since": widget["IdleSince"]},
            )
```

…or, when the check really is one call and one filter, build it declaratively:

```python
from zombiescan.building import simple_check

idle_widget = simple_check(
    "acme-idle-widget",
    "Widgets nobody is using",
    service="widgets",
    operation="describe_widgets",
    result_key="Widgets",
    id_key="WidgetId",
    resource_type="widget",
    where=lambda w: w["State"] == "idle",
    reason="Widget has been idle since {IdleSince}",
    remediation="aws widgets delete-widget --widget-id {id} --region {region}",
    rate="acme.widget_month",
)
```

Use `simple_check` when the whole check fits it. Reach for a plain function as
soon as you need a second API call, a sum over sub-resources, or a judgement a
predicate cannot express — most real checks do, and forcing them through the
builder makes them harder to read, not easier.

Two rules are not negotiable:

- **Checks read. Never write.** Describe, List, Get, Head. A check that mutates
  anything is a bug.
- **Pass `scope="global"`** for account-wide resources with no region of their
  own, or the same resource is reported once per region and the waste total is
  seventeen times too large.

## Pricing

A rate is a key plus a description of how to read a section of the price table:

```python
from zombiescan.pricing.rates import RateSpec, register_rate

register_rate(RateSpec("acme.widget_month", "acme_widget_month", pack="acme"))
```

Every lookup returns `(usd_per_month, approximate)`. `approximate=True` means
the number is a stand-in — another region's rate, or a variant the table has
never heard of — and the report marks it, so never return it for a figure you
are confident in. Four shapes are built in:

| Shape | Section in the table | Spec |
| --- | --- | --- |
| Flat per-region | `{region: price}` | `RateSpec(key, section)` |
| Hourly per-region | `{region: price}` | `per_hour=True` |
| Keyed by variant | `{region: {variant: price}}` | `variants=True` |
| Global | one value, no region | `scope="global"` |

`default_variant` says what to price an unknown variant as. Leave it unset when
there is no honest answer: a versioned machine size that appears in the API
before the price table knows about it should report zero marked approximate,
not the price of a different machine.

For anything these cannot express, register a resolver:

```python
from zombiescan.pricing.rates import register_resolver


def _tiered(table, region, units):
    rates, approximate = table.lookup_section("acme_tiers", region)
    ...
    return price, approximate


register_resolver("acme.tiered_month", _tiered, pack="acme")
```

### Refreshing those rates

`python -m zombiescan.pricing.refresh` rebuilds the whole table from every
installed pack, so a pack that adds a rate must also say where it comes from,
or its prices are dropped on the next refresh. The test suite enforces this for
the built-in packs: every section of the table has exactly one fetcher.

```python
from zombiescan.pricing.refresh import RefreshContext, paginate, price_fetcher, usd_rate


@price_fetcher("acme_widget_month", label="ACME widget prices", pack="acme")
def fetch_widgets(ctx: RefreshContext) -> dict[str, dict[str, float]]:
    rates = {}
    for entry in paginate(ctx.pricing, ServiceCode="AmazonAcme"):
        ...
    return {"acme_widget_month": rates}
```

A fetcher returns `{section: data}` and may only write sections it declared.
`ctx.pricing` is a Price List client pinned to us-east-1; `ctx.session` is
there for rates that come from somewhere else, as Lightsail's do.

## Cleaning

A cleaner **plans**; it never executes. It yields the mutating calls that would
resolve a finding, and the runner in `zombiescan.clean` decides whether to make
them. `--apply` gates exactly one thing — whether a planned step is sent to AWS
— and must never change which steps get planned.

```python
from collections.abc import Iterator

from zombiescan.cleaners import Step, cleaner
from zombiescan.models import Finding, ScanContext


@cleaner("acme-idle-widget")
def clean_idle_widget(ctx: ScanContext, finding: Finding) -> Iterator[Step]:
    yield Step(
        description=f"snapshot widget {finding.resource_id}",
        service="widgets",
        operation="create_widget_snapshot",
        params={"WidgetId": finding.resource_id},
    )
    yield Step(
        description=f"delete widget {finding.resource_id}",
        service="widgets",
        operation="delete_widget",
        params={"WidgetId": finding.resource_id},
        irreversible=True,
    )
```

- **Back up first where the API allows it**, and order the steps so the backup
  precedes the destruction. A failed step aborts the rest of that finding, so a
  failed snapshot can never be followed by the delete that assumed it.
- **Mark `irreversible=True`** on any step with no recovery window, snapshot or
  undo. It drives what the operator is warned about, so a wrong flag is a
  safety bug.
- **Refuse rather than guess.** If the safe action cannot be worked out from a
  describe call, write no cleaner and pass `uncleanable="..."` to `@check`
  instead. The reason is shown to the operator verbatim, so write it for them:

```python
@check(
    CHECK_NAME,
    "Widgets nobody is using",
    uncleanable=(
        "a widget may still be referenced by a pipeline this tool cannot see, "
        "and deletion has no recovery window"
    ),
)
```

Every check needs either a cleaner or a reason. The built-in suite asserts it,
and it is worth asserting in yours.

## Compatibility

`register_pack` refuses a pack built against a different `api_version` rather
than half-loading it — a pack that imports cleanly but misprices findings is
worse than one that does not load at all. The current version is
`zombiescan.packs.PACK_API_VERSION` (**1**), and it is bumped when a change to
`ScanContext`, `Finding`, or the check/cleaner/rate registries stops an older
pack from working.

A pack that fails to import is reported and skipped, never fatal: a broken pack
of yours must not cost someone the twenty-three checks that would have worked.

The JSON report records every loaded pack and its version under `packs`
(`schema_version` 2 and up). A finding means nothing without knowing which
check produced it, and a check means nothing without knowing which pack — two
packs may both ship an `idle-cluster` check and disagree about what idle means.

## Testing

Test against recorded API responses, not a live account. `zombiescan`'s own
suite runs offline with no credentials, and yours should:

```python
def test_flags_the_idle_widget(make_context):
    ctx, _ = make_context({"describe_widgets": [{"Widgets": [...]}]})
    findings = list(idle_widget(ctx))
    assert [f.resource_id for f in findings] == ["w-1"]
```

Pin your own price table in the fixture rather than loading the bundled one, so
that refreshing real prices cannot break an assertion.
