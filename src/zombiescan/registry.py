"""Check registry.

Every check registers itself with ``@check(...)``. Discovering packs
(``zombiescan.packs.discover``) imports their check modules, which populates
this registry as a side effect.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

from zombiescan import packs
from zombiescan.models import Finding, ScanContext

CheckFn = Callable[[ScanContext], Iterator[Finding]]


@dataclass(frozen=True)
class CheckSpec:
    name: str
    title: str
    fn: CheckFn
    # "global" checks describe account-wide resources with no region of their
    # own -- Route 53 health checks, IAM, CloudFront. Running them once per
    # region would report the same resource seventeen times.
    scope: str = "regional"
    # Which pack registered this check, for reporting and for --disable-pack.
    pack: str = "core"
    # Why this finding cannot be cleaned automatically, if it cannot. A check
    # must have either a cleaner or a reason here: "refuse rather than guess"
    # only works if the refusal explains itself.
    uncleanable: str | None = None

    @property
    def is_global(self) -> bool:
        return self.scope == "global"


CHECKS: dict[str, CheckSpec] = {}


def check(
    name: str,
    title: str,
    scope: str = "regional",
    uncleanable: str | None = None,
) -> Callable[[CheckFn], CheckFn]:
    """Register a check under ``name``.

    Checks must make Describe/List/Get calls only. A check that mutates
    anything is a bug, not a feature request.

    ``uncleanable`` states why this finding cannot be removed automatically.
    Set it instead of writing a cleaner when the safe action genuinely cannot
    be worked out from a describe call -- it is reported to the operator
    verbatim.
    """

    if scope not in ("regional", "global"):
        raise ValueError(f"scope must be 'regional' or 'global', got {scope!r}")

    def decorator(fn: CheckFn) -> CheckFn:
        if name in CHECKS:
            raise ValueError(f"duplicate check name: {name}")
        CHECKS[name] = CheckSpec(
            name=name,
            title=title,
            fn=fn,
            scope=scope,
            pack=packs.current_pack(),
            uncleanable=uncleanable,
        )
        return fn

    return decorator
