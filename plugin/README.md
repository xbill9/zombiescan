# zombiescan — Claude Code plugin

Find the AWS resources nobody is using, from inside Claude Code.

## Install

```
/plugin marketplace add xbill9/zombiescan
/plugin install zombiescan@zombiescan
```

## What you get

- **`/zombiescan [region ...]`** — scan the account these credentials belong to
  and report what the waste costs.
- **`/zombie-cleanup <report path>`** — show exactly what cleaning that report
  up would do, without changing anything.
- **A skill** that loads itself whenever the conversation turns to AWS spend,
  unused resources, or a finding you want explained.
- **An MCP server** with five read-only tools: `list_checks`, `scan_account`,
  `estimate_savings`, `explain_finding` and `plan_cleanup`.

## Requirements

AWS credentials the machine can already use — an `aws login` session, a
profile, SSO or environment variables — and [uv](https://docs.astral.sh/uv/),
which the MCP server is started with.

The server runs out of this repository: `.mcp.json` starts it with
`uv run --project ${CLAUDE_PLUGIN_ROOT}/.. zombiescan-mcp`, which resolves to
the checkout the plugin was installed from. With zombiescan installed globally
(`uv tool install git+https://github.com/xbill9/zombiescan`), `zombiescan-mcp`
on its own works just as well; edit `.mcp.json` to use it.

## Read-only

Every tool here makes Describe, List and Get calls only. Nothing in this plugin
deletes an AWS resource, and nothing in it runs a remediation command.
`plan_cleanup` plans a cleanup and stops. Applying one is `zombiescan clean
--apply`, run by a person at a terminal, where it prompts before each resource
and warns about steps that cannot be undone.

Costs are estimates from a bundled list-price table, not from your bill. They
exclude savings plans, reserved capacity, private pricing and credits.
