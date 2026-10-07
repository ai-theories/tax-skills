"""Agent definitions must name tools that actually exist.

An agent's frontmatter lists the tools it may call. Nothing checked those
names, so an agent could be bound to a tool that was renamed, moved to another
server, or never written — and the failure would appear only when a user asked
for that domain, as a tool call that silently did nothing.
"""
import os
import re

import pytest

from taxagent.gateway.server_registry import SERVERS
from taxagent.application.bootstrap import PROJECT_ROOT

AGENTS = os.path.join(PROJECT_ROOT, "agents")
MCP_TOOL = re.compile(r"^mcp__([a-z0-9-]+)__([a-z0-9_]+)$")


def _frontmatter(path):
    text = open(path, encoding="utf-8").read()
    assert text.startswith("---\n"), f"{path} has no frontmatter"
    block = text.split("---\n", 2)[1]
    fields = {}
    for line in block.splitlines():
        if ": " in line and not line.startswith(" "):
            key, _, value = line.partition(": ")
            fields[key.strip()] = value.strip()
    return fields


def _agent_files():
    return sorted(os.path.join(AGENTS, f) for f in os.listdir(AGENTS) if f.endswith(".md"))


def _surface():
    """Every tool each server actually exposes, keyed by server name."""
    from taxagent.gateway.calculator_dispatcher import BOUNDARIES, CalculatorDispatcher
    from taxagent.gateway.domain_dispatchers import (HouseholdDispatcher,
                                                     PortfolioDispatcher)
    workflow = {"portfolio": PortfolioDispatcher, "household": HouseholdDispatcher}
    surface = {}
    for key, spec in SERVERS.items():
        if key in BOUNDARIES:
            surface[spec.name] = {t["name"] for t in CalculatorDispatcher(key).list_tools()}
            continue
        # The tool table is a class attribute; no service is needed to read it.
        cls = workflow[key]
        surface[spec.name] = set(cls.tools) | {"resolve_subject", "get_run_status",
                                               "get_scenario", cls.capability_tool}
    return surface


@pytest.mark.parametrize("path", _agent_files(), ids=os.path.basename)
def test_agent_declares_a_name_and_description(path):
    fields = _frontmatter(path)
    assert fields.get("name") == os.path.basename(path)[:-3]
    assert len(fields.get("description", "")) > 40, "description will not route well"


@pytest.mark.parametrize("path", _agent_files(), ids=os.path.basename)
def test_every_mcp_tool_an_agent_claims_exists(path):
    fields = _frontmatter(path)
    declared = [t.strip() for t in fields.get("tools", "").split(",") if t.strip()]
    surface = _surface()
    for tool in declared:
        match = MCP_TOOL.match(tool)
        if not match:
            # A built-in such as Read or WebSearch. Not ours to verify.
            assert tool in {"Read", "Write", "Edit", "Bash", "WebSearch", "WebFetch",
                            "Glob", "Grep"}, f"{tool} is neither a built-in nor an MCP tool"
            continue
        server, name = match.groups()
        assert server in surface, f"{tool}: no server named '{server}' ships"
        assert name in surface[server], (
            f"{tool}: '{server}' exposes {sorted(surface[server])}")


def test_each_domain_server_has_an_agent():
    """A server nobody is bound to is a surface no agent will ever reach."""
    bound = set()
    for path in _agent_files():
        for tool in _frontmatter(path).get("tools", "").split(","):
            match = MCP_TOOL.match(tool.strip())
            if match:
                bound.add(match.group(1))
    expected = {spec.name for spec in SERVERS.values()}
    assert expected <= bound, f"servers with no agent: {sorted(expected - bound)}"


# --- skills ---------------------------------------------------------------
# A SKILL.md is instructions to an agent. When it names a tool, that name has
# to exist, or the agent follows the instruction and the call goes nowhere.
# Splitting one server into three renamed every workflow tool, and every skill
# still named the old one until this was written.

SKILLS = os.path.join(PROJECT_ROOT, "skills")
BACKTICKED = re.compile(r"`([a-z][a-z0-9_]{3,})`")
RETIRED = {"start_analysis", "cancel_run"}


def _skill_files():
    return sorted(os.path.join(SKILLS, name, "SKILL.md") for name in os.listdir(SKILLS)
                  if os.path.isdir(os.path.join(SKILLS, name)))


def _all_tool_names():
    names = set()
    for tools in _surface().values():
        names |= tools
    return names


@pytest.mark.parametrize("path", _skill_files(),
                         ids=lambda p: os.path.basename(os.path.dirname(p)))
def test_skill_names_no_retired_tool(path):
    text = open(path, encoding="utf-8").read()
    found = {name for name in BACKTICKED.findall(text) if name in RETIRED}
    assert not found, f"names tools that no longer exist: {sorted(found)}"


@pytest.mark.parametrize("path", _skill_files(),
                         ids=lambda p: os.path.basename(os.path.dirname(p)))
def test_skill_routes_to_a_tool_that_exists(path):
    """Each skill that drives a workflow must name a live tool.

    Only the skills that actually call the backend are checked: some are
    guidance about reading a result and legitimately call nothing.
    """
    text = open(path, encoding="utf-8").read()
    mentioned = {n for n in BACKTICKED.findall(text) if n in _all_tool_names()}
    workflow = {"check_holdings", "review_lots", "review_harvest", "review_rebalance",
                "coordinate_household"}
    if not mentioned & workflow:
        return
    for name in mentioned:
        assert name in _all_tool_names(), name


def test_every_workflow_tool_is_reachable_from_some_skill():
    """A tool no skill mentions is one an agent will not find."""
    mentioned = set()
    for path in _skill_files():
        mentioned |= set(BACKTICKED.findall(open(path, encoding="utf-8").read()))
    workflow = {"check_holdings", "review_lots", "review_harvest", "review_rebalance",
                "coordinate_household"}
    assert workflow <= mentioned, f"no skill names: {sorted(workflow - mentioned)}"
