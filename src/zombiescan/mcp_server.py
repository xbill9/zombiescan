"""MCP server: the scan engine driven by an agent instead of a terminal.

Every tool here is read-only. There is no tool that deletes anything --
``plan_cleanup`` builds and prices the same plan ``zombiescan clean`` shows on
a dry run and stops there. Applying a plan stays something a person does at
their own terminal with ``zombiescan clean --apply``, where the per-resource
prompt and the irreversible-step warning live.

Tools hand back numbers that are already computed: totals, counts, per-check
and per-region breakdowns, the cheapest and the costliest row. A tool that
returns rows and leaves the adding up to the caller produces totals nobody can
reproduce, so every one of these answers the question it was given and says
which filter it applied to get there.

The protocol is JSON-RPC 2.0 over stdio, one message per line, implemented
against the standard library alone. zombiescan's install has one job -- to
scan an AWS account -- and an MCP SDK pinned into it is a second thing to keep
current for the sake of about a hundred lines.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
import tempfile
import time
from collections.abc import Callable
from typing import Any, TextIO

from zombiescan import __version__

# What we answer an `initialize` with when the client asks for something we do
# not recognise. Clients negotiate down; an unknown version is the client's
# newer one, not a broken one.
PROTOCOL_VERSION = "2025-06-18"
KNOWN_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = (
    "zombiescan finds AWS resources nobody is using and prices them. Every tool "
    "is read-only; nothing here deletes anything. Run scan_account once, then "
    "ask estimate_savings for any total, count or breakdown of the report it "
    "writes -- do not add up findings yourself. Costs are list-price estimates, "
    "not the account's bill."
)

Handler = Callable[[dict[str, Any]], dict[str, Any]]
TOOLS: list[dict[str, Any]] = []
HANDLERS: dict[str, Handler] = {}


def tool(name: str, description: str, schema: dict[str, Any]) -> Callable[[Handler], Handler]:
    """Register one MCP tool: its advertised shape and the function behind it."""

    def decorator(fn: Handler) -> Handler:
        TOOLS.append({"name": name, "description": description, "inputSchema": schema})
        HANDLERS[name] = fn
        return fn

    return decorator


# --------------------------------------------------------------------------
# shared helpers


def _strings(value: Any) -> list[str]:
    """A list argument, tolerating the single string a client may send instead."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _session(profile: str | None) -> Any:
    # Imported here rather than at module scope: `tools/list` and `initialize`
    # must answer without boto3, credentials or a price table.
    import boto3

    return boto3.Session(profile_name=profile) if profile else boto3.Session()


def _load_report(path: str) -> dict[str, Any]:
    """A report written by scan_account or by `zombiescan scan --json`."""
    from zombiescan import report as report_module

    try:
        document = json.loads(pathlib.Path(path).read_text())
    except FileNotFoundError:
        raise ToolError(
            f"No report at {path}. Run scan_account first, or pass the path of a "
            "report written by `zombiescan scan --json`."
        ) from None
    except json.JSONDecodeError as exc:
        raise ToolError(f"{path} is not valid JSON: {exc}") from None

    version = document.get("schema_version")
    if version != report_module.SCHEMA_VERSION:
        raise ToolError(
            f"{path} has schema_version {version}; this build reads "
            f"{report_module.SCHEMA_VERSION}. Re-run the scan."
        )
    return document


class ToolError(Exception):
    """A tool could not answer. The message is shown to the caller verbatim."""


def _money(value: float) -> float:
    return round(value, 2)


def _totals(findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Every number a caller might otherwise try to work out from the rows."""
    monthly = sum(f["monthly_cost"] for f in findings)
    totals: dict[str, Any] = {
        "count": len(findings),
        "monthly_cost": _money(monthly),
        "annual_cost": _money(monthly * 12),
        "free_count": sum(1 for f in findings if f["monthly_cost"] < 0.01),
        "approximate_count": sum(1 for f in findings if f.get("approximate_cost")),
    }
    if findings:
        costliest = max(findings, key=lambda f: f["monthly_cost"])
        cheapest = min(findings, key=lambda f: f["monthly_cost"])
        totals["costliest"] = _brief(costliest)
        totals["cheapest"] = _brief(cheapest)
    return totals


def _brief(finding: dict[str, Any]) -> dict[str, Any]:
    return {
        "check": finding["check"],
        "resource_id": finding["resource_id"],
        "region": finding["region"],
        "monthly_cost": finding["monthly_cost"],
    }


def _group(findings: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for finding in findings:
        row = rows.setdefault(str(finding.get(key, "unknown")), {"count": 0, "monthly_cost": 0.0})
        row["count"] += 1
        row["monthly_cost"] += finding["monthly_cost"]
    grouped = [
        {key: name, "count": row["count"], "monthly_cost": _money(row["monthly_cost"])}
        for name, row in rows.items()
    ]
    grouped.sort(key=lambda r: (-r["monthly_cost"], r[key]))
    return grouped


def _filtered(
    findings: list[dict[str, Any]], args: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply the caller's filter, and report exactly what was applied.

    The echo is not decoration. A filter naming a check that is not in the
    report returns an exact, sourced zero, which reads like good news; saying
    which terms matched nothing is what makes a mistyped filter visible.
    """
    checks = set(_strings(args.get("checks")))
    regions = set(_strings(args.get("regions")))
    types = set(_strings(args.get("resource_types")))
    min_cost = float(args.get("min_cost") or 0.0)
    max_cost = args.get("max_cost")
    max_cost = float(max_cost) if max_cost is not None else None

    kept = [
        f
        for f in findings
        if (not checks or f["check"] in checks)
        and (not regions or f["region"] in regions)
        and (not types or f.get("resource_type") in types)
        and f["monthly_cost"] >= min_cost
        and (max_cost is None or f["monthly_cost"] <= max_cost)
    ]

    applied: dict[str, Any] = {
        "checks": sorted(checks) or "any",
        "regions": sorted(regions) or "any",
        "resource_types": sorted(types) or "any",
        "min_monthly_cost": min_cost,
        "max_monthly_cost": max_cost,
        "findings_in_report": len(findings),
        "findings_matched": len(kept),
    }
    for label, wanted, column in (
        ("checks", checks, "check"),
        ("regions", regions, "region"),
        ("resource_types", types, "resource_type"),
    ):
        missing = sorted(wanted - {str(f.get(column)) for f in findings})
        if missing:
            applied[f"no_such_{label}_in_report"] = missing
    return kept, applied


# --------------------------------------------------------------------------
# tools

_SCAN_SCHEMA = {
    "type": "object",
    "properties": {
        "profile": {"type": "string", "description": "AWS profile to use."},
        "regions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Regions to scan. Defaults to the configured region.",
        },
        "all_regions": {
            "type": "boolean",
            "description": "Scan every region the account has enabled. Slower, and where "
            "forgotten resources usually are.",
        },
        "us_only": {"type": "boolean", "description": "Narrow the regions to the us-* ones."},
        "checks": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Run only these checks. list_checks gives the names.",
        },
        "disable_packs": {"type": "array", "items": {"type": "string"}},
        "min_cost": {
            "type": "number",
            "description": "Drop findings cheaper than this many USD per month.",
        },
        "limit": {
            "type": "integer",
            "description": "How many of the costliest findings to return inline. Default 10; "
            "the full set is always written to the report file.",
        },
        "report_path": {
            "type": "string",
            "description": "Where to write the full JSON report. Defaults to a temp file.",
        },
    },
}


@tool(
    "scan_account",
    "Scan an AWS account for unused resources and price them. Read-only: Describe/List/Get "
    "calls only. Writes a full JSON report and returns the totals, the per-check breakdown "
    "and the costliest findings. Pass the returned report_path to estimate_savings, "
    "explain_finding and plan_cleanup rather than scanning again.",
    _SCAN_SCHEMA,
)
def scan_account(args: dict[str, Any]) -> dict[str, Any]:
    from zombiescan import engine
    from zombiescan import report as report_module
    from zombiescan.pricing import PriceTable

    disabled = frozenset(_strings(args.get("disable_packs")))
    load_report = engine.load_packs(disabled=disabled)

    try:
        session = _session(args.get("profile"))
        caller_arn = engine.verify_credentials(session)
        selected = engine.select_checks(tuple(_strings(args.get("checks"))), disabled)
        regions = _strings(args.get("regions")) or engine.resolve_regions(
            session, bool(args.get("all_regions"))
        )
        if args.get("us_only"):
            regions = engine.filter_us_regions(regions)
    except engine.CredentialError as exc:
        raise ToolError(str(exc)) from None
    except ValueError as exc:
        raise ToolError(str(exc)) from None
    except Exception as exc:  # noqa: BLE001 - an unusable profile reads the same way
        raise ToolError(f"{type(exc).__name__}: {exc}") from None

    pricing = PriceTable.load()
    started = time.monotonic()
    result = engine.scan(session, regions, selected, pricing)
    elapsed = time.monotonic() - started

    hidden = 0
    min_cost = float(args.get("min_cost") or 0.0)
    if min_cost > 0:
        kept = [f for f in result.findings if f.monthly_cost >= min_cost]
        hidden = len(result.findings) - len(kept)
        result.findings = kept

    document = report_module.to_json(
        result,
        caller_arn=caller_arn,
        duration_seconds=elapsed,
        pricing_generated=pricing.generated,
    )
    path = args.get("report_path") or str(
        pathlib.Path(tempfile.gettempdir())
        / f"zombiescan-{dt.datetime.now(dt.UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    pathlib.Path(path).write_text(json.dumps(document, indent=2, default=str) + "\n")

    summary = summarise(document, limit=int(args.get("limit") or 10))
    summary["report_path"] = path
    summary["checks_run"] = len(selected)
    summary["packs_failed"] = [
        {"pack": failure.name, "source": failure.source, "message": failure.message}
        for failure in load_report.failed
    ]
    if hidden:
        summary["hidden_by_min_cost"] = hidden
    return summary


def summarise(document: dict[str, Any], limit: int = 10) -> dict[str, Any]:
    """The parts of a report worth putting in front of an agent.

    A scan of a busy account produces hundreds of findings; returning them all
    inline buries the total. The full set stays in the report file, and every
    number here is computed from it rather than left to be added up.
    """
    findings = document.get("findings", [])
    scan_meta = document.get("scan", {})
    totals = document.get("totals", {})

    summary: dict[str, Any] = {
        "account_id": scan_meta.get("account_id"),
        "caller_arn": scan_meta.get("caller_arn"),
        "regions_scanned": scan_meta.get("regions", []),
        "complete": scan_meta.get("complete", True),
        "duration_seconds": scan_meta.get("duration_seconds"),
        "totals": {
            "finding_count": totals.get("finding_count", len(findings)),
            "monthly_cost": totals.get("monthly_cost", 0.0),
            "annual_cost": totals.get("annual_cost", 0.0),
        },
        "by_check": [
            {"check": name, "count": row["count"], "monthly_cost": row["monthly_cost"]}
            for name, row in (totals.get("by_check") or {}).items()
        ],
        "by_region": _group(findings, "region"),
        "top_findings": findings[: max(limit, 0)] if limit else findings,
        "pricing_basis": (document.get("pricing") or {}).get("basis"),
        "errors": {
            "count": len(document.get("errors", [])),
            "first": document.get("errors", [])[:5],
        },
    }
    if not summary["complete"]:
        # Zero findings from a scan where nothing could be reached is not an
        # all-clear, and an agent will report it as one unless told.
        summary["warning"] = (
            f"Every one of the {scan_meta.get('pairs_attempted')} region/check pairs failed. "
            "This is not an all-clear: nothing was successfully scanned."
        )
    return summary


_ESTIMATE_SCHEMA = {
    "type": "object",
    "required": ["report_path"],
    "properties": {
        "report_path": {"type": "string", "description": "A report from scan_account."},
        "checks": {"type": "array", "items": {"type": "string"}},
        "regions": {"type": "array", "items": {"type": "string"}},
        "resource_types": {"type": "array", "items": {"type": "string"}},
        "min_cost": {"type": "number", "description": "Keep findings at or above this USD/month."},
        "max_cost": {"type": "number", "description": "Keep findings at or below this USD/month."},
        "limit": {"type": "integer", "description": "Matching rows to return. Default 5."},
    },
}


@tool(
    "estimate_savings",
    "Total, count and break down the findings in a report, with an optional filter by check, "
    "region, resource type or cost. Use this for every 'how much', 'how many' or 'which is "
    "biggest' question instead of adding up findings yourself. Returns the exact figures and "
    "echoes the filter it applied.",
    _ESTIMATE_SCHEMA,
)
def estimate_savings(args: dict[str, Any]) -> dict[str, Any]:
    document = _load_report(args["report_path"])
    findings = document.get("findings", [])
    matched, applied = _filtered(findings, args)
    limit = int(args.get("limit") or 5)

    unmatched_monthly = sum(f["monthly_cost"] for f in findings) - sum(
        f["monthly_cost"] for f in matched
    )
    return {
        "filter_applied": applied,
        "matched": _totals(matched),
        "not_matched": {
            "count": len(findings) - len(matched),
            "monthly_cost": _money(unmatched_monthly),
        },
        "by_check": _group(matched, "check"),
        "by_region": _group(matched, "region"),
        "by_resource_type": _group(matched, "resource_type"),
        "matched_findings": matched[: max(limit, 0)] if limit else matched,
        "pricing": document.get("pricing"),
        "scanned": (document.get("scan") or {}).get("generated"),
    }


_EXPLAIN_SCHEMA = {
    "type": "object",
    "properties": {
        "check": {"type": "string", "description": "The check name, e.g. unattached-ebs."},
        "resource_id": {
            "type": "string",
            "description": "A resource id from a report, to explain that finding specifically.",
        },
        "report_path": {"type": "string", "description": "Report to look the resource up in."},
    },
}


@tool(
    "explain_finding",
    "Explain why a check treats a resource as waste, how its cost is worked out, and whether "
    "zombiescan can clean it up or refuses to. Give a check name, or a resource_id plus the "
    "report_path to explain one specific finding.",
    _EXPLAIN_SCHEMA,
)
def explain_finding(args: dict[str, Any]) -> dict[str, Any]:
    import inspect

    from zombiescan import engine
    from zombiescan.cleaners import CLEANERS
    from zombiescan.registry import CHECKS

    engine.load_packs()

    check_name = args.get("check")
    finding: dict[str, Any] | None = None

    if args.get("resource_id"):
        if not args.get("report_path"):
            raise ToolError("resource_id needs report_path: the finding is looked up in a report.")
        rows = [
            f
            for f in _load_report(args["report_path"]).get("findings", [])
            if f["resource_id"] == args["resource_id"]
            and (not check_name or f["check"] == check_name)
        ]
        if not rows:
            raise ToolError(
                f"No finding for {args['resource_id']} in {args['report_path']}. "
                "The scan did not flag it, or the report is from another account."
            )
        finding = rows[0]
        check_name = finding["check"]

    if not check_name:
        raise ToolError("Pass a check name, or a resource_id together with report_path.")

    spec = CHECKS.get(check_name)
    if spec is None:
        raise ToolError(f"No check named {check_name}. Installed: {', '.join(sorted(CHECKS))}.")

    module = inspect.getmodule(spec.fn)
    # A check built with simple_check has its finding builder defined in
    # zombiescan.building; the prose that explains it is in the check's own
    # module, and building.py's docstring would explain the wrong thing.
    doc = (module.__doc__ or "").strip() if module else ""
    if module and module.__name__ == "zombiescan.building":
        doc = ""

    explanation: dict[str, Any] = {
        "check": spec.name,
        "title": spec.title,
        "pack": spec.pack,
        "scope": spec.scope,
        "why_it_is_waste": doc or spec.title,
        "cleanable": spec.uncleanable is None and check_name in CLEANERS,
    }
    if spec.uncleanable:
        explanation["refuses_to_clean_because"] = spec.uncleanable
    elif check_name not in CLEANERS:
        explanation["refuses_to_clean_because"] = "no cleaner is implemented for this check"

    if finding is not None:
        explanation["finding"] = finding
        explanation["cost_basis"] = (
            "Approximate: priced from the fallback region, because the table has no "
            "entry for this one."
            if finding.get("approximate_cost")
            else "List price for this region, from the bundled price table."
        )
        explanation["remediation_command"] = finding.get("remediation")
        explanation["remediation_note"] = (
            "This command is printed, never run. zombiescan has no code path that executes it."
        )
    return explanation


_LIST_SCHEMA = {
    "type": "object",
    "properties": {
        "pack": {"type": "string", "description": "Only checks from this pack."},
        "cleanable_only": {"type": "boolean", "description": "Only checks that can be cleaned."},
    },
}


@tool(
    "list_checks",
    "List the installed checks: what each one looks for, which pack ships it, and whether "
    "zombiescan can clean the findings it produces. Needs no AWS credentials.",
    _LIST_SCHEMA,
)
def list_checks(args: dict[str, Any]) -> dict[str, Any]:
    from zombiescan import engine, packs
    from zombiescan.cleaners import CLEANERS
    from zombiescan.registry import CHECKS

    engine.load_packs()
    wanted_pack = args.get("pack")
    cleanable_only = bool(args.get("cleanable_only"))

    rows = []
    for name, spec in sorted(CHECKS.items()):
        cleanable = spec.uncleanable is None and name in CLEANERS
        if wanted_pack and spec.pack != wanted_pack:
            continue
        if cleanable_only and not cleanable:
            continue
        row: dict[str, Any] = {
            "check": name,
            "title": spec.title,
            "pack": spec.pack,
            "scope": spec.scope,
            "cleanable": cleanable,
        }
        if spec.uncleanable:
            row["uncleanable"] = spec.uncleanable
        rows.append(row)

    return {
        "count": len(rows),
        "checks": rows,
        "packs": packs.manifest(),
        "pack_api_version": packs.PACK_API_VERSION,
    }


_PLAN_SCHEMA = {
    "type": "object",
    "required": ["report_path"],
    "properties": {
        "report_path": {"type": "string", "description": "A report from scan_account."},
        "checks": {"type": "array", "items": {"type": "string"}},
        "regions": {"type": "array", "items": {"type": "string"}},
        "min_cost": {"type": "number"},
        "limit": {"type": "integer", "description": "Resources to plan. Default 20."},
        "profile": {"type": "string", "description": "AWS profile to use."},
    },
}


@tool(
    "plan_cleanup",
    "Show exactly what `zombiescan clean` would do to the findings in a report: the API calls "
    "in order, which steps are irreversible, and which findings it refuses to touch and why. "
    "Plans only -- this makes read calls and changes nothing. Applying a plan is the "
    "operator's `zombiescan clean --apply` to run, not this server's.",
    _PLAN_SCHEMA,
)
def plan_cleanup(args: dict[str, Any]) -> dict[str, Any]:
    from zombiescan import clean, engine
    from zombiescan.models import Finding
    from zombiescan.pricing import PriceTable

    engine.load_packs()
    document = _load_report(args["report_path"])
    matched, applied = _filtered(document.get("findings", []), args)
    limit = int(args.get("limit") or 20)

    try:
        session = _session(args.get("profile"))
        caller_arn = engine.verify_credentials(session)
    except engine.CredentialError as exc:
        raise ToolError(str(exc)) from None

    scanned_as = (document.get("scan") or {}).get("caller_arn")
    if scanned_as and scanned_as != caller_arn:
        # Planning one account's cleanup from another account's report names
        # resources that are not there, and whose ids may well belong to
        # something real here.
        raise ToolError(
            f"That report was produced as {scanned_as}, but these credentials are "
            f"{caller_arn}. Re-scan with these credentials before planning."
        )

    pricing = PriceTable.load()
    home = session.region_name or "us-east-1"
    planned: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []

    for row in matched[:limit]:
        outcome = clean.plan_for(session, Finding.from_dict(row), pricing, home)
        if outcome.status == clean.UNSUPPORTED:
            unsupported.append(
                {
                    "check": row["check"],
                    "resource_id": row["resource_id"],
                    "region": row["region"],
                    "monthly_cost": row["monthly_cost"],
                    "reason": outcome.error,
                }
            )
            continue
        planned.append(
            {
                "check": row["check"],
                "resource_id": row["resource_id"],
                "region": row["region"],
                "monthly_cost": row["monthly_cost"],
                "irreversible": outcome.irreversible,
                "steps": [
                    {
                        "description": step.description,
                        "call": f"{step.service}.{step.operation}",
                        "params": step.params,
                        "irreversible": step.irreversible,
                    }
                    for step in outcome.steps
                ],
            }
        )

    freed = sum(row["monthly_cost"] for row in planned)
    return {
        "mode": "dry run — nothing was changed and this server cannot change anything",
        "filter_applied": applied,
        "planned_count": len(planned),
        "unsupported_count": len(unsupported),
        "irreversible_count": sum(1 for row in planned if row["irreversible"]),
        "monthly_cost_if_all_cleaned": _money(freed),
        "annual_cost_if_all_cleaned": _money(freed * 12),
        "not_planned_because_of_limit": max(len(matched) - limit, 0),
        "planned": planned,
        "unsupported": unsupported,
        "to_apply": (
            "The operator runs this themselves: "
            f"zombiescan clean --from {args['report_path']} --apply"
        ),
    }


# --------------------------------------------------------------------------
# protocol

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603


def _text_result(payload: dict[str, Any], is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2, default=str)}],
        "isError": is_error,
    }


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Run one tool, turning any failure into a tool error rather than a crash.

    A tool error is reported in the result, not as a protocol error: the agent
    needs to read "run aws login" and act on it, and a JSON-RPC error would
    tell it only that the call failed.
    """
    handler = HANDLERS.get(name)
    if handler is None:
        return _text_result(
            {"error": f"No tool named {name}. Available: {', '.join(sorted(HANDLERS))}."},
            is_error=True,
        )
    try:
        return _text_result(handler(arguments or {}))
    except ToolError as exc:
        return _text_result({"error": str(exc)}, is_error=True)
    except KeyError as exc:
        return _text_result({"error": f"missing required argument: {exc}"}, is_error=True)
    except Exception as exc:  # noqa: BLE001 - one bad call must not kill the server
        return _text_result({"error": f"{type(exc).__name__}: {exc}"}, is_error=True)


def _initialize(params: dict[str, Any]) -> dict[str, Any]:
    requested = params.get("protocolVersion")
    return {
        "protocolVersion": requested if requested in KNOWN_PROTOCOLS else PROTOCOL_VERSION,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": "zombiescan", "version": __version__},
        "instructions": INSTRUCTIONS,
    }


def dispatch(message: dict[str, Any]) -> dict[str, Any] | None:
    """Handle one JSON-RPC message. Returns None for a notification."""
    method = message.get("method")
    params = message.get("params") or {}
    request_id = message.get("id")
    is_notification = "id" not in message

    if method == "initialize":
        result: Any = _initialize(params)
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        result = call_tool(params.get("name", ""), params.get("arguments") or {})
    elif method and method.startswith("notifications/"):
        return None
    else:
        if is_notification:
            return None
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": METHOD_NOT_FOUND, "message": f"unknown method: {method}"},
        }

    if is_notification:
        return None
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve(stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
    """Read requests from stdin and write responses to stdout, one per line."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            _write(
                stdout,
                {"jsonrpc": "2.0", "id": None, "error": {"code": PARSE_ERROR, "message": str(exc)}},
            )
            continue
        if not isinstance(message, dict):
            _write(
                stdout,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": INVALID_REQUEST, "message": "expected a JSON object"},
                },
            )
            continue
        try:
            response = dispatch(message)
        except Exception as exc:  # noqa: BLE001 - the server outlives a bad request
            response = {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "error": {"code": INTERNAL_ERROR, "message": f"{type(exc).__name__}: {exc}"},
            }
        if response is not None:
            _write(stdout, response)


def _write(stdout: TextIO, payload: dict[str, Any]) -> None:
    stdout.write(json.dumps(payload, default=str) + "\n")
    stdout.flush()


def main() -> None:
    serve()


if __name__ == "__main__":
    main()
