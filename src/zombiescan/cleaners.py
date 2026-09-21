"""Turning findings into the API calls that remove them.

A cleaner does not execute anything. It *plans*: given a finding, it yields
the mutating calls that would resolve it, in order. Read-only calls needed to
build that plan (listing the parts of a multipart upload, say) happen during
planning, because a dry run that cannot see what it would touch is not a dry
run.

The runner in ``clean.py`` decides whether to execute the plan. That split is
the whole safety design: --apply changes one thing, whether the planned steps
are performed, and nothing about which steps get planned.

This module is only the registry. The cleaners themselves live beside the
checks they clean, in each pack's ``cleaners.py``, so a pack ships the removal
plan for its own findings rather than asking core to carry it.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from zombiescan.models import Finding, ScanContext


@dataclass(frozen=True)
class Step:
    """One mutating API call."""

    description: str
    service: str
    operation: str
    params: dict[str, Any] = field(default_factory=dict)
    # Irreversible means no recovery window, no snapshot, no undo: once this
    # returns, the data is gone or on an unstoppable timer.
    irreversible: bool = False
    # Where to send the call, when that is not the finding's own region. A
    # global finding can still need a regional call -- the hosted zone a Cloud
    # Map namespace created is global, the namespace is not -- and defaulting
    # to the operator's home region would delete from whichever region they
    # happen to be configured for.
    region: str | None = None


PlanFn = Callable[[ScanContext, Finding], Iterator[Step]]
CLEANERS: dict[str, PlanFn] = {}


def cleaner(check: str) -> Callable[[PlanFn], PlanFn]:
    def decorator(fn: PlanFn) -> PlanFn:
        if check in CLEANERS:
            raise ValueError(f"duplicate cleaner for {check}")
        CLEANERS[check] = fn
        return fn

    return decorator


def _stamp() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S")
