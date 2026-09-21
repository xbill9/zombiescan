---
title: "Put the Arithmetic in the Tool: an MCP Server for an AWS Waste Scanner"
published: false
description: "A cost report gets read twice: once by a person in a terminal, once by an agent through an MCP server. Making the server compute every total instead of handing back rows keeps the two answers the same, and measures the report's own arithmetic against the AWS bill."
tags: aws, mcp, python, devops
cover_image: https://raw.githubusercontent.com/xbill9/zombiescan/main/articles/zombiescan-mcp/devto-cover.cde3f396.jpg
---

This article provides a step by step guide to adding an MCP server to an AWS cost scanner so an agent drives the same engine a terminal does. Every tool returns figures the engine computed, and the two front doors are held to the same number by an end-to-end test.

https://github.com/xbill9/zombiescan

---

#### A Report Gets Read Twice

A scan produces one JSON document. A person reads it through a terminal table; an agent reads it through whatever tool it is given.

Both quote dollar figures to whoever asked. When the two disagree, one of them is wrong in a way that survives review, because each looks sourced.

A model handed a list of rows and asked "how much is this costing" does the addition in the answer. Eleven small integers are enough to break that: one small model asked how many of eleven ids were 10 or more answered wrong in twenty calls out of twenty. Push the count into the engine and the same question is right every time.

So the tools here return totals, counts, breakdowns, minima and maxima, and the filter that produced them.

---

#### At This Point You Should Have…

- An AWS account with credentials your shell can use, from `aws login`, a profile, SSO or environment variables
- Python 3.12 and `uv`
- Read-only access wide enough to describe resources in every region you scan
- `ce:GetCostAndUsage` for the calibration step
- Claude Code, for the plugin at the end

---

#### Step 1 — Scan From the Terminal

The scan is read-only: Describe, List and Get calls, across every region the account has enabled.

```
uv run zombiescan scan --all-regions --limit 8
```

```
Scanning as arn:aws:iam::<account>:root
17 region(s), 30 check(s) — read-only

zombiescan — 120 findings across 17 regions

Check                   Found  Monthly
ecr-stale-images           33    $6.40
unused-route53-zone         1    $0.50
empty-vpc                   1    $0.00
log-group-no-retention     67    $0.00
unused-security-group      18    $0.00

Estimated waste: $6.90/month ($82.80/year)
```

One hundred and twenty findings, thirty-one of them priced. That JSON document is what everything below reads.

---

#### Step 2 — Serve the Same Engine Over stdio

MCP over stdio is line-delimited JSON-RPC 2.0. A server answers `initialize`, `tools/list` and `tools/call`, and that is enough for a client to drive it.

```
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}' \
  | uv run zombiescan-mcp
```

```json
{
  "protocolVersion": "2025-06-18",
  "capabilities": {"tools": {"listChanged": false}},
  "serverInfo": {"name": "zombiescan", "version": "0.1.0"},
  "instructions": "zombiescan finds AWS resources nobody is using and prices them. Every tool is read-only; nothing here deletes anything. Run scan_account once, then ask estimate_savings for any total, count or breakdown of the report it writes -- do not add up findings yourself."
}
```

The `instructions` field is served to the client on connect and carries the operating rule for the whole server.

```
tools/list -> ['scan_account', 'estimate_savings', 'explain_finding', 'list_checks', 'plan_cleanup']
```

Five tools, about a hundred lines of framing, and no MCP SDK in the dependency list. A cost scanner's install has one job, and a second package to keep current is a second thing to break.

🔎 Tip: answer `initialize` with the client's own `protocolVersion` when you recognise it. A client that asks for a version it gets back proceeds; one that gets a different string negotiates again.

---

#### Step 3 — Return Figures, Not Rows

`estimate_savings` takes a report path and a filter, and answers with the arithmetic already done.

```json
{
  "matched": {
    "count": 120,
    "monthly_cost": 6.9,
    "annual_cost": 82.8,
    "free_count": 89,
    "approximate_count": 100,
    "costliest": {
      "check": "ecr-stale-images",
      "resource_id": "ai-course-creator-app",
      "region": "us-east-1",
      "monthly_cost": 0.52
    }
  },
  "by_check": [
    {"check": "ecr-stale-images", "count": 33, "monthly_cost": 6.4},
    {"check": "unused-route53-zone", "count": 1, "monthly_cost": 0.5}
  ]
}
```

Every how-much, how-many and which-is-biggest question is answered from that one call. The costliest row is picked in code, the per-check and per-region sections are grouped in code, and the model quotes what came back.

`scan_account` follows the same rule from the other direction: it returns the totals, the breakdowns and the costliest few findings inline, and leaves all hundred and twenty rows in the report file. A busy account's rows would bury the total in the context window.

---

#### Step 4 — Echo the Filter That Produced the Number

A filter naming a check the report does not contain returns a precise zero with a citation, which reads as good news.

```
estimate_savings(report_path=..., checks=["unattached-ebs-volumes"])
```

```json
{
  "filter_applied": {
    "checks": ["unattached-ebs-volumes"],
    "findings_in_report": 120,
    "findings_matched": 0,
    "no_such_checks_in_report": ["unattached-ebs-volumes"]
  },
  "matched": {"count": 0, "monthly_cost": 0},
  "not_matched": {"count": 120, "monthly_cost": 6.9}
}
```

The check is called `unattached-ebs`, so that filter matched nothing. `no_such_checks_in_report` says so in the same response, and `not_matched` shows what the filter excluded.

🔎 Tip: check the predicate a model sent, not only the number it quoted. A wrong filter returns an exact figure with a source attached, which is harder to catch than a miscount.

---

#### Step 5 — Keep Every Tool Read-Only

Scanning and cleaning are separate commands in this tool, and the MCP server exposes only the first.

`plan_cleanup` builds the same plan the `clean` command shows on a dry run: the API calls in order, the backup step before the destruction where AWS allows one, and an `irreversible` flag on every step with no recovery window. It stops there. Applying a plan stays `zombiescan clean --apply` at a terminal, where the per-resource prompt lives.

Convention is weak, so the suite holds the boundary:

```python
def test_the_server_cannot_apply_a_cleanup():
    source = pathlib.Path(mcp_server.__file__).read_text()
    assert "apply_outcome" not in source
    assert set(re.findall(r"\bclean\.(\w+)", source)) == {"plan_for", "UNSUPPORTED"}
```

`clean.plan_for` yields `Step` objects. `clean.apply_outcome` is the one function that sends a step to AWS, and a tool that reaches it fails the suite instead of shipping.

---

#### Step 6 — Make the Total the Sum of the Rows

An end-to-end test runs both front doors over one account and compares them: `scan_account` writes the report, `estimate_savings` reads it back, and the totals must match.

Two summation orders over the same one hundred and twenty findings:

| Order | Monthly |
|---|---|
| Each finding rounded to the cent, then added | **$6.90** |
| Exact costs added, then rounded once | **$6.92** |

The report published every finding rounded to the cent and computed its headline from the unrounded values, so the document disagreed with its own rows by two cents. An agent reading the published rows can only reach the first figure.

The rule that follows: a report's total is the sum of the rows it prints. `ScanResult.total_monthly_cost` rounds each finding before adding, with the same `round` that publishes it, and the per-check sections in the JSON, the terminal table and the HTML report do the same.

```python
result.findings = [_finding("half-cent", f"r-{index}", 0.125) for index in range(4)]

assert result.total_monthly_cost == 0.48
assert round(sum(f.monthly_cost for f in result.findings), 2) == 0.50
```

Arithmetic on four findings priced at one half-cent: at $0.125 each they publish as $0.12 and total $0.48, where adding first and rounding once gives $0.50. The truer figure loses, because a total nobody can reproduce from the rows in front of them is worth less than one that is two cents off.

---

#### Step 7 — Calibrate the Estimate Against the Bill

Thirty-three stale container repositories carry the whole dollar total. Cost Explorer knows what they charged.

```
aws ce get-cost-and-usage --granularity MONTHLY --metrics UnblendedCost UsageQuantity \
  --group-by Type=DIMENSION,Key=USAGE_TYPE \
  --filter '{"And":[{"Dimensions":{"Key":"RECORD_TYPE","Values":["Usage"]}},
                    {"Dimensions":{"Key":"SERVICE","Values":["Amazon EC2 Container Registry (ECR)"]}}]}'
```

| Figure | Value |
|---|---|
| Billed ECR storage, 1–21 September, 20 days | 19.96 GB-Month |
| Projected over 30 days | 29.94 GB-Month |
| At $0.10 per GB-month | $2.99/month |
| `ecr-stale-images` estimate, 33 repositories | $6.40/month |
| Implied storage at the same rate | 64.0 GB |
| Ratio | **2.1x** |

ECR bills unique layers once. The check sums each image's full size, so thirty-three repositories built from the same base layer are counted thirty-three times over. Every one of those findings carries `approximate_cost: true` and an `approximate_reason` of `shared-layers`, and `explain_finding` returns the reason with the finding.

An upper bound stated as an upper bound is useful. The calibration puts a number on it for this account.

---

#### Step 8 — Wire It Up as a Claude Code Plugin

The plugin is the server plus two slash commands and a skill, installed from the same repository.

```
/plugin marketplace add xbill9/zombiescan
/plugin install zombiescan@zombiescan
```

`.mcp.json` at the plugin root starts the server:

```json
{
  "mcpServers": {
    "zombiescan": {
      "command": "uv",
      "args": ["run", "--quiet", "--project", "${CLAUDE_PLUGIN_ROOT}/..", "zombiescan-mcp"]
    }
  }
}
```

`${CLAUDE_PLUGIN_ROOT}` resolves to the installed plugin directory, so the same file works for a local checkout and for an install from GitHub.

The skill carries the rules a client cannot infer from the schemas: scan once and keep the report path, read `filter_applied` before quoting a zero, treat `complete: false` as an incomplete scan instead of an all-clear, and hand the operator the `clean --apply` command instead of running it.

---

#### Compare and Contrast

| | Terminal | MCP server |
|---|---|---|
| Entry point | `zombiescan scan` | `scan_account` |
| Output | rich table, JSON, HTML, cleanup script | JSON per tool call |
| Findings shown | 25 by default, `--limit 0` for all | costliest 10, full set in the report file |
| Totals | printed | returned computed, with breakdowns |
| Filtering | `--check`, `--region`, `--min-cost` | one filter argument, echoed back |
| Cleanup | dry run, then `--apply` | plan only |
| Dependencies | click, rich, boto3 | the same, plus nothing |

---

#### So, Which One?

🥇 **The terminal** for the first look at an account. The table ranks and groups in one screen, and the HTML report goes in a ticket.

🥈 **The MCP server** for the follow-up questions. "Which region holds most of it", "what would removing the ECR repositories save", "why is that zone waste" are three tool calls against one report, with no second scan and no second set of numbers.

Both read the same engine, so a disagreement between them is a defect in one of the two.

---

#### Step 9 — Run the Tests

The offline suite drives every check against recorded AWS API responses, so it needs no credentials.

```
uv run pytest -q
```

```
343 passed, 3 deselected in 0.39s
```

The three deselected are the live tests, which need a real account:

```
ZOMBIESCAN_LIVE=1 uv run pytest -m live
```

```
3 passed, 343 deselected in 25.58s
```

The third of those is the one that compares the two front doors.

---

#### Summary

The goal of this article was to give a local AWS cost scanner an agent front door without giving up the numbers. The key to the solution was making every tool return figures the engine computed, and holding both front doors to one total in a live test. The results were:

- 🟢 Five read-only MCP tools over stdio, JSON-RPC 2.0, standard library only
- 🟢 One scan of 17 regions: 120 findings, $6.90 a month, $82.80 a year
- 🟢 `estimate_savings` answers totals, counts, extremes and three breakdowns from one call
- 🟢 A filter that matches nothing reports which of its terms the report never held
- 🟢 A report's headline, its per-check sections and its rows all add to $6.90
- ⚠️ Rounding each finding before adding costs two cents against the exact sum on this account
- ⚠️ The ECR estimate is 2.1x the billed figure: $6.40 against $2.99 projected from 19.96 GB-Month, because shared layers are counted once per image
- ❌ The server plans cleanups and performs none; `clean --apply` stays a terminal command

One AWS account, 17 enabled regions, scanned on 2026-09-21 with a price table generated the same day. Prices are on-demand USD list prices from the AWS Price List API; they ignore private pricing, savings plans and credits, so they will not match an invoice to the cent. The ECR comparison projects 20 days of billed usage to 30 and compares it against a single scan taken inside that window. The model-arithmetic figures quoted in the opening were measured on 2026-09-15 with eleven ids and one API call per run, twenty runs per cell.

The strategy for using MCP for AWS cost reporting was validated with an incremental step by step approach.

---

#### References

- zombiescan — https://github.com/xbill9/zombiescan
- Model Context Protocol specification — https://modelcontextprotocol.io/specification/2025-06-18
- Claude Code plugins — https://docs.claude.com/en/docs/claude-code/plugins
- Cost Explorer GetCostAndUsage — https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_GetCostAndUsage.html
- Amazon ECR pricing — https://aws.amazon.com/ecr/pricing/
- AWS Price List API, GetProducts — https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_pricing_GetProducts.html
