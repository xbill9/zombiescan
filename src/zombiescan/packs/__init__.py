"""Packs: units of scan coverage that can be shipped and installed separately.

A pack bundles everything one area of AWS needs -- its checks, their cleaners,
the rate specs that price them, and the fetchers that refresh those rates. The
checks that ship in this package are themselves packs (``core``, ``lightsail``),
loaded by exactly the same path as one installed from PyPI, so the seam is
exercised on every run rather than only by third parties.

A third-party pack declares itself with an entry point::

    [project.entry-points."zombiescan.packs"]
    acmecorp = "zombiescan_pack_acmecorp"

and calls ``register_pack`` at import time, before registering its checks::

    from zombiescan.packs import register_pack
    register_pack("acmecorp", version="1.2.0", description="ACME's own waste checks")

**A pack is code, and it runs with your AWS credentials.** Nothing here
sandboxes it or verifies that its checks are read-only; install packs you
trust, on the same judgement you would apply to any other dependency.
"""

from __future__ import annotations

import dataclasses
import importlib
import importlib.metadata
import pkgutil
from dataclasses import dataclass, field

# Bumped when a change to the pack-facing API -- ScanContext, Finding, the
# check/cleaner/rate registries -- stops an older pack from working. A pack
# built against a different major version is refused rather than half-loaded,
# because a pack that imports cleanly but misprices findings is worse than one
# that does not load at all.
PACK_API_VERSION = 1

ENTRY_POINT_GROUP = "zombiescan.packs"


@dataclass(frozen=True)
class Pack:
    """One installed unit of scan coverage."""

    name: str
    version: str
    description: str = ""
    api_version: int = PACK_API_VERSION
    # "built-in" for the packs shipped here, otherwise the distribution that
    # provided the entry point.
    source: str = "built-in"
    homepage: str = ""

    @property
    def compatible(self) -> bool:
        return self.api_version == PACK_API_VERSION


@dataclass
class PackError:
    """A pack that could not be loaded, and why."""

    name: str
    source: str
    message: str


@dataclass
class LoadReport:
    """What one discovery pass found."""

    loaded: list[Pack] = field(default_factory=list)
    failed: list[PackError] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


PACKS: dict[str, Pack] = {}

# The pack currently being imported. ``@check`` and ``@cleaner`` read it to
# attribute what they register, which keeps their signatures free of a
# pack argument that every call site would have to repeat correctly.
_LOADING: str | None = None


def current_pack() -> str:
    return _LOADING or "core"


def register_pack(
    name: str,
    version: str,
    description: str = "",
    api_version: int = PACK_API_VERSION,
    homepage: str = "",
    source: str | None = None,
) -> Pack:
    """Declare a pack. Call this at import time, before registering checks."""
    if name in PACKS:
        raise ValueError(f"duplicate pack name: {name!r}")
    pack = Pack(
        name=name,
        version=version,
        description=description,
        api_version=api_version,
        source=source or (_LOADING_SOURCE or "built-in"),
        homepage=homepage,
    )
    if not pack.compatible:
        raise IncompatiblePack(
            f"pack {name!r} targets zombiescan pack API v{api_version}, "
            f"this build provides v{PACK_API_VERSION}"
        )
    PACKS[name] = pack
    return pack


class IncompatiblePack(RuntimeError):
    """Raised by ``register_pack`` when a pack targets another API version."""


_LOADING_SOURCE: str | None = None


def _load_module(module_name: str, pack_hint: str, source: str) -> None:
    """Import a pack module with ``current_pack()`` pointing at it."""
    global _LOADING, _LOADING_SOURCE
    previous, previous_source = _LOADING, _LOADING_SOURCE
    _LOADING, _LOADING_SOURCE = pack_hint, source
    try:
        importlib.import_module(module_name)
    finally:
        _LOADING, _LOADING_SOURCE = previous, previous_source


def import_pack_modules(package: str, paths: list[str]) -> None:
    """Import every check module in a pack directory.

    A pack's ``__init__`` calls this instead of listing its own modules, so
    adding a check to a pack is adding a file to its directory and nothing
    else. Third-party packs are welcome to use it for the same reason.
    """
    for module_info in pkgutil.iter_modules(paths):
        if not module_info.name.startswith("_"):
            importlib.import_module(f"{package}.{module_info.name}")


def _builtin_pack_modules() -> list[tuple[str, str]]:
    """``(pack name, module)`` for the packs that ship inside this package.

    Every subpackage of ``zombiescan.packs`` is a built-in pack. There is no
    list to keep in step: a new pack directory is discovered by existing.
    """
    return [
        (module_info.name, f"{__name__}.{module_info.name}")
        for module_info in pkgutil.iter_modules(__path__)
        if module_info.ispkg and not module_info.name.startswith("_")
    ]


def discover(disabled: frozenset[str] = frozenset()) -> LoadReport:
    """Import every enabled pack: the built-in ones, then installed ones.

    One broken pack is recorded and skipped rather than allowed to abort the
    scan -- a third-party pack that fails to import must not cost you the
    twelve checks that would have worked.
    """
    report = LoadReport()

    for name, module_name in _builtin_pack_modules():
        if name in disabled:
            report.skipped.append(name)
            continue
        _load_module(module_name, name, "built-in")
        if name in PACKS:
            report.loaded.append(PACKS[name])

    for entry_point in _installed_entry_points():
        name = entry_point.name
        if name in disabled:
            report.skipped.append(name)
            continue
        source = _distribution_of(entry_point)
        try:
            _load_module(entry_point.value, name, source)
        except IncompatiblePack as exc:
            report.failed.append(PackError(name, source, str(exc)))
            continue
        except Exception as exc:  # noqa: BLE001 - a bad pack must not kill the scan
            report.failed.append(PackError(name, source, f"{type(exc).__name__}: {exc}"))
            continue
        if name not in PACKS:
            report.failed.append(
                PackError(
                    name,
                    source,
                    f"imported {entry_point.value!r} but never called register_pack()",
                )
            )
            continue
        report.loaded.append(PACKS[name])

    return report


def _installed_entry_points() -> list[importlib.metadata.EntryPoint]:
    try:
        return list(importlib.metadata.entry_points(group=ENTRY_POINT_GROUP))
    except Exception:  # noqa: BLE001 - a broken installed dist must not kill the scan
        return []


def _distribution_of(entry_point: importlib.metadata.EntryPoint) -> str:
    dist = getattr(entry_point, "dist", None)
    if dist is None:
        return "installed"
    version = getattr(dist, "version", "")
    return f"{dist.name} {version}".strip()


def manifest() -> list[dict[str, object]]:
    """Every loaded pack as plain data, for --json output and `zombiescan packs`."""
    return [dataclasses.asdict(pack) for pack in sorted(PACKS.values(), key=lambda p: p.name)]
