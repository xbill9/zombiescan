"""Single-file HTML report.

Self-contained on purpose: no external stylesheet, no font, no script, no
network request. A cost report gets emailed, attached to a ticket and opened
from a laptop with no internet, and one that renders as unstyled text in those
places is worse than no report at all.

The print stylesheet is deliberate too: printing to PDF from a browser is how
most people will produce a PDF, and it avoids taking on a rendering engine as
a dependency.
"""

from __future__ import annotations

import datetime as dt
import html
from typing import Any

from zombiescan import __version__
from zombiescan.engine import ScanResult

_CSS = """
:root {
  --bg: #ffffff; --fg: #1b1f24; --muted: #6a737d; --line: #e1e4e8;
  --accent: #0b5fff; --warn: #9a6700; --zero: #8b949e;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #0d1117; --fg: #e6edf3; --muted: #9198a1; --line: #30363d;
    --accent: #6ea8ff; --warn: #d29922; --zero: #6e7681;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 32px 16px; background: var(--bg); color: var(--fg);
  font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
        Helvetica, Arial, sans-serif;
}
main { max-width: 1100px; margin: 0 auto; }
h1 { font-size: 1.6rem; margin: 0 0 4px; letter-spacing: -0.01em; }
h2 { font-size: 1.05rem; margin: 36px 0 10px; text-transform: uppercase;
     letter-spacing: 0.06em; color: var(--muted); font-weight: 600; }
.sub { color: var(--muted); margin: 0 0 28px; font-size: 0.9rem; }
.headline { font-size: 2.4rem; font-weight: 650; letter-spacing: -0.02em; margin: 0; }
.headline .per { font-size: 1rem; font-weight: 400; color: var(--muted); }
.annual { color: var(--muted); font-size: 0.95rem; margin: 2px 0 0; }
.meta { display: flex; flex-wrap: wrap; gap: 8px 28px; margin: 20px 0 0;
        font-size: 0.85rem; color: var(--muted); }
.meta b { color: var(--fg); font-weight: 600; }
table { border-collapse: collapse; width: 100%; font-size: 0.88rem; }
th { text-align: left; font-weight: 600; color: var(--muted); font-size: 0.75rem;
     text-transform: uppercase; letter-spacing: 0.05em;
     border-bottom: 1px solid var(--line); padding: 8px 10px; }
td { padding: 9px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
td.num, th.num { text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }
tr:last-child td { border-bottom: 0; }
code, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
              font-size: 0.85em; word-break: break-all; }
.zero { color: var(--zero); }
.approx { color: var(--warn); }
details { margin-top: 6px; }
summary { cursor: pointer; color: var(--accent); font-size: 0.8rem; }
pre { background: rgba(127,127,127,0.09); padding: 10px 12px; border-radius: 6px;
      overflow-x: auto; margin: 8px 0 0; font-size: 0.8rem; white-space: pre-wrap;
      word-break: break-all; }
.note { color: var(--muted); font-size: 0.82rem; margin-top: 6px; }
footer { margin-top: 44px; padding-top: 16px; border-top: 1px solid var(--line);
         color: var(--muted); font-size: 0.8rem; }
.banner { border-left: 3px solid var(--warn); padding: 10px 14px; margin: 0 0 24px;
          background: rgba(210,153,34,0.08); font-size: 0.9rem; }
@media print {
  :root { --bg: #fff; --fg: #000; --muted: #444; --line: #bbb; --accent: #000; --zero: #666; }
  body { padding: 0; font-size: 11pt; }
  details[open] summary ~ * { display: block; }
  details > summary { display: none; }
  details { margin-top: 4px; }
  tr, td, th { page-break-inside: avoid; }
  h2 { page-break-after: avoid; }
  footer { page-break-before: avoid; }
}
"""


def _money(value: float, approximate: bool = False) -> str:
    return f"{'~' if approximate else ''}${value:,.2f}"


def _cell(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _summary_rows(result: ScanResult) -> str:
    counts: dict[str, list[Any]] = {}
    for finding in result.findings:
        row = counts.setdefault(finding.check, [0, 0.0])
        row[0] += 1
        row[1] += finding.monthly_cost
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1][1], kv[0]))
    return "\n".join(
        f'<tr><td class="mono">{_cell(name)}</td>'
        f'<td class="num">{count}</td>'
        f'<td class="num{" zero" if cost == 0 else ""}">{_money(cost)}</td>'
        f'<td class="num">{_money(cost * 12)}</td></tr>'
        for name, (count, cost) in ordered
    )


def _finding_rows(result: ScanResult) -> str:
    rows = []
    for finding in result.findings:
        note = (finding.details or {}).get("note")
        classes = "num" + (" approx" if finding.approximate_cost else "")
        if finding.monthly_cost == 0:
            classes += " zero"
        rows.append(
            f"<tr>"
            f"<td>{_cell(finding.region)}</td>"
            f'<td class="mono">{_cell(finding.resource_id)}</td>'
            f'<td class="mono">{_cell(finding.check)}</td>'
            f'<td class="{classes}">{_money(finding.monthly_cost, finding.approximate_cost)}</td>'
            f"<td>{_cell(finding.reason)}"
            + (f'<div class="note">{_cell(note)}</div>' if note else "")
            + "<details><summary>remediation</summary>"
            f"<pre>{_cell(finding.remediation)}</pre></details>"
            f"</td></tr>"
        )
    return "\n".join(rows)


def _error_section(result: ScanResult) -> str:
    if not result.errors:
        return ""
    rows = "\n".join(
        f"<tr><td>{_cell(e.region)}</td><td class='mono'>{_cell(e.check)}</td>"
        f"<td>{_cell(e.message)}</td></tr>"
        for e in result.errors[:50]
    )
    more = (
        f"<p class='note'>and {len(result.errors) - 50} more</p>" if len(result.errors) > 50 else ""
    )
    return (
        f"<h2>Could not scan ({len(result.errors)})</h2>"
        f"<table><thead><tr><th>Region</th><th>Check</th><th>Error</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>{more}"
    )


def to_html(
    result: ScanResult,
    caller_arn: str | None = None,
    pricing_generated: str | None = None,
) -> str:
    total = result.total_monthly_cost
    generated = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M UTC")
    account = caller_arn.split(":")[4] if caller_arn and caller_arn.count(":") >= 4 else "unknown"

    banner = ""
    if result.completely_failed:
        banner = (
            '<p class="banner"><b>Nothing could be scanned.</b> Every one of the '
            f"{result.attempted} region/check pairs failed. This report is not an "
            "all-clear.</p>"
        )

    headline = (
        f'<p class="headline">{_money(total)}<span class="per"> / month</span></p>'
        f'<p class="annual">{_money(total * 12)} / year</p>'
        if total >= 0.01
        else '<p class="headline">Under $0.01<span class="per"> / month</span></p>'
        '<p class="annual">These cost almost nothing today. They are cleanup debt, '
        "not a bill.</p>"
    )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>zombiescan report — {_cell(account)}</title>
<style>{_CSS}</style></head>
<body><main>
<h1>AWS waste report</h1>
<p class="sub">Account {_cell(account)} · {len(result.regions)} region(s) · {generated}</p>
{banner}
{headline}
<div class="meta">
  <span><b>{len(result.findings)}</b> findings</span>
  <span><b>{result.attempted}</b> region/check pairs scanned</span>
  <span><b>{len(result.errors)}</b> errors</span>
  <span>prices generated <b>{_cell(pricing_generated or "unknown")}</b></span>
  <span>zombiescan <b>{_cell(__version__)}</b></span>
</div>

<h2>By check</h2>
<table><thead><tr><th>Check</th><th class="num">Found</th>
<th class="num">Monthly</th><th class="num">Annual</th></tr></thead>
<tbody>{_summary_rows(result)}</tbody></table>

<h2>Findings ({len(result.findings)})</h2>
<table><thead><tr><th>Region</th><th>Resource</th><th>Check</th>
<th class="num">Monthly</th><th>Why</th></tr></thead>
<tbody>{_finding_rows(result)}</tbody></table>

{_error_section(result)}

<footer>
<p>Costs are estimates from AWS on-demand list prices, not from your bill. They
exclude savings plans, reserved capacity, private pricing and credits. A
<span class="approx">~</span> marks an estimate or upper bound.</p>
<p>zombiescan is read-only and deleted nothing. The remediation commands above
were generated, not executed. Read them before running them.</p>
</footer>
</main></body></html>
"""


def write_html(result: ScanResult, path: str, **kwargs: Any) -> None:
    with open(path, "w") as handle:
        handle.write(to_html(result, **kwargs))
