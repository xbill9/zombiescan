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


CHECKS: dict[str, CheckSpec] = {}


def check(name: str, title: str) -> Callable[[CheckFn], CheckFn]:
    """Register a check under ``name``.

    Checks must make Describe/List/Get calls only. A check that mutates
    anything is a bug, not a feature request.
    """

    def decorator(fn: CheckFn) -> CheckFn:
        if name in CHECKS:
            raise ValueError(f"duplicate check name: {name}")
        CHECKS[name] = CheckSpec(name=name, title=title, fn=fn)
        return fn

    return decorator
