"""The Claude Code plugin: manifest, MCP wiring, skill and commands.

The plugin is data, so nothing about it fails at import time -- a manifest
with a typo simply does not load, and a command referring to a tool the server
does not have fails only when someone runs it. These assertions are what
notices instead.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from zombiescan import mcp_server

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugin"


def _frontmatter(path: pathlib.Path) -> dict[str, str]:
    """The YAML frontmatter of a skill or command, read as simple key: value."""
    text = path.read_text()
    assert text.startswith("---\n"), f"{path} has no frontmatter"
    block = text.split("---\n", 2)[1]
    fields: dict[str, str] = {}
    for line in block.splitlines():
        if line and not line.startswith((" ", "\t")) and ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip().strip('"')
    return fields


def test_the_manifest_is_valid_and_names_the_plugin() -> None:
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "zombiescan"
    assert manifest["description"]
    assert manifest["license"] == "MIT"


def test_the_marketplace_entry_points_at_the_plugin() -> None:
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    entry = market["plugins"][0]
    assert entry["name"] == "zombiescan"
    assert (ROOT / entry["source"].lstrip("./") / ".claude-plugin" / "plugin.json").exists()


def test_the_mcp_config_runs_this_package() -> None:
    config = json.loads((PLUGIN / ".mcp.json").read_text())
    server = config["mcpServers"]["zombiescan"]
    # The console script the server is started as, which pyproject must define.
    assert server["args"][-1] == "zombiescan-mcp"
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert 'zombiescan-mcp = "zombiescan.mcp_server:main"' in pyproject


def test_the_skill_declares_itself() -> None:
    fields = _frontmatter(PLUGIN / "skills" / "zombiescan" / "SKILL.md")
    assert fields["name"] == "zombiescan"
    # The description is what decides whether the skill is loaded at all.
    assert len(fields["description"]) > 80


@pytest.mark.parametrize(
    "command", sorted((PLUGIN / "commands").glob("*.md")), ids=lambda p: p.name
)
def test_every_command_has_a_description(command: pathlib.Path) -> None:
    assert _frontmatter(command)["description"]


# Snake_case names the plugin's prose is allowed to use that are not tools:
# fields of the report and of a tool's result.
REPORT_FIELDS = {
    "report_path",
    "by_check",
    "filter_applied",
    "no_such_checks_in_report",
    "approximate_cost",
    "all_regions",
    "resource_types",
    "min_cost",
    "max_cost",
}


@pytest.mark.parametrize("document", sorted(PLUGIN.rglob("*.md")), ids=lambda p: p.name)
def test_the_plugin_only_names_tools_the_server_has(document: pathlib.Path) -> None:
    """A skill telling the agent to call a tool that does not exist wastes a turn."""
    text = document.read_text()
    backticked = set(re.findall(r"`([a-z][a-z0-9_]*)`", text))
    snake_case = {word for word in backticked if "_" in word}

    assert not snake_case - set(mcp_server.HANDLERS) - REPORT_FIELDS, (
        f"{document.name} names something that is neither a tool nor a report field"
    )
    assert snake_case & set(mcp_server.HANDLERS), f"{document.name} names none of the tools"
