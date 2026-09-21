"""Check registry.

Every check registers itself with ``@check(...)``. Importing
``zombiescan.checks`` populates the registry as a side effect.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

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

    @property
    def is_global(self) -> bool:
        return self.scope == "global"


CHECKS: dict[str, CheckSpec] = {}


def check(name: str, title: str, scope: str = "regional") -> Callable[[CheckFn], CheckFn]:
    """Register a check under ``name``.

    Checks must make Describe/List/Get calls only. A check that mutates
    anything is a bug, not a feature request.
    """

    if scope not in ("regional", "global"):
        raise ValueError(f"scope must be 'regional' or 'global', got {scope!r}")

    def decorator(fn: CheckFn) -> CheckFn:
        if name in CHECKS:
            raise ValueError(f"duplicate check name: {name}")
        CHECKS[name] = CheckSpec(name=name, title=title, fn=fn, scope=scope)
        return fn

    return decorator
