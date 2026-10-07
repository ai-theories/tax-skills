"""The plugin must be installable, and must not advertise what it cannot do."""
import json
import os
import re

import pytest
import yaml

from taxagent.application.bootstrap import PROJECT_ROOT

PLUGIN = PROJECT_ROOT                       # the project root *is* the plugin root
MANIFEST = os.path.join(PLUGIN, ".claude-plugin", "plugin.json")
SKILLS = os.path.join(PLUGIN, "skills")
AGENTS = os.path.join(PLUGIN, "agents")


def _frontmatter(path):
    text = open(path, encoding="utf-8").read()
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, f"{path} has no frontmatter"
    return yaml.safe_load(match.group(1)), text


@pytest.fixture(scope="module")
def manifest():
    return json.load(open(MANIFEST, encoding="utf-8"))


# --- installability -------------------------------------------------------

def test_no_declared_path_escapes_the_plugin_root():
    # A path containing ".." fails plugin validation and, once installed,
    # points outside the copied plugin. This was why the backend was
    # unreachable: PYTHONPATH pointed two levels above the plugin root.
    mcp = json.load(open(os.path.join(PLUGIN, ".mcp.json"), encoding="utf-8"))
    blob = json.dumps(mcp) + open(MANIFEST, encoding="utf-8").read()
    assert ".." not in blob


def test_backend_is_inside_the_plugin_root():
    mcp = json.load(open(os.path.join(PLUGIN, ".mcp.json"), encoding="utf-8"))
    assert mcp["mcpServers"], "the plugin declares no servers"
    for name, server in mcp["mcpServers"].items():
        assert server["env"]["PYTHONPATH"] == "${CLAUDE_PLUGIN_ROOT}/src", name
        # The launcher must live inside the root too, or the host cannot run it.
        assert server["command"].startswith("${CLAUDE_PLUGIN_ROOT}/"), name
        relative = server["command"][len("${CLAUDE_PLUGIN_ROOT}/"):]
        launcher = os.path.join(PLUGIN, relative)
        assert os.path.exists(launcher), f"{name}: {relative} does not exist"
        assert os.access(launcher, os.X_OK), f"{name}: {relative} is not executable"
    # ... and that path really holds the package.
    assert os.path.exists(os.path.join(PLUGIN, "src", "taxagent", "__init__.py"))


def test_every_declared_server_is_one_the_code_can_start():
    """A name in .mcp.json that the entry point rejects is an install failure."""
    from taxagent.gateway.server_registry import SERVERS
    mcp = json.load(open(os.path.join(PLUGIN, ".mcp.json"), encoding="utf-8"))
    for name, server in mcp["mcpServers"].items():
        args = server.get("args") or []
        assert "--server" in args, f"{name} does not say which server to start"
        selected = args[args.index("--server") + 1]
        assert selected in SERVERS, f"{name} starts unknown server '{selected}'"
        # The host's key and the server's own name must agree, or a tool
        # refusal names a server the operator cannot find in their config.
        assert name == SERVERS[selected].name, name


def test_identity_comes_from_user_config_not_ambient_env(manifest):
    # ${TAXAGENT_*} is not a substitution Claude Code performs, so the server
    # used to receive literal strings and refuse to start. userConfig is the
    # mechanism that prompts the user and substitutes into MCP env.
    from taxagent.gateway.server_registry import SERVERS
    mcp = json.load(open(os.path.join(PLUGIN, ".mcp.json"), encoding="utf-8"))
    for name, server in mcp["mcpServers"].items():
        args = server.get("args") or []
        selected = args[args.index("--server") + 1]
        env = server["env"]
        if not SERVERS[selected].needs_identity:
            # It holds no client data. Demanding entitlements it never reads
            # would make the calculators uninstallable for no benefit.
            assert "TAXAGENT_ACCOUNTS" not in env, name
            continue
        assert env["TAXAGENT_TENANT_ID"] == "${user_config.tenant_id}"
        assert env["TAXAGENT_PRINCIPAL_ID"] == "${user_config.principal_id}"
        assert env["TAXAGENT_ACCOUNTS"] == "${user_config.accounts}"
    for key in ("tenant_id", "principal_id", "accounts"):
        assert manifest["userConfig"][key]["required"] is True


def test_every_user_config_reference_is_declared(manifest):
    mcp = open(os.path.join(PLUGIN, ".mcp.json"), encoding="utf-8").read()
    referenced = set(re.findall(r"\$\{user_config\.(\w+)\}", mcp))
    assert referenced <= set(manifest["userConfig"])


def test_no_top_level_bin_directory():
    # claude.ai and Cowork refuse to install a plugin that has one.
    assert not os.path.isdir(os.path.join(PLUGIN, "bin"))
    assert os.path.isdir(os.path.join(PLUGIN, "scripts"))


def test_license_text_exists_for_the_declared_license(manifest):
    assert manifest["license"] == "Apache-2.0"
    text = open(os.path.join(PLUGIN, "LICENSE"), encoding="utf-8").read()
    assert "Apache License" in text and "Version 2.0" in text


def test_marketplace_entry_matches_the_manifest(manifest):
    market = json.load(open(os.path.join(PLUGIN, ".claude-plugin", "marketplace.json"),
                            encoding="utf-8"))
    entry = next(p for p in market["plugins"] if p["name"] == manifest["name"])
    assert entry["source"] == "./"
    for field in ("version", "license", "homepage", "repository", "displayName"):
        assert entry[field] == manifest[field], field


def test_manifest_carries_discovery_metadata(manifest):
    for field in ("displayName", "version", "description", "author", "homepage",
                  "repository", "license", "keywords"):
        assert manifest.get(field), f"{field} is missing"
    assert re.fullmatch(r"[a-z0-9-]+", manifest["name"])


def test_no_claude_md_at_plugin_root():
    # Not loaded as context, and plugin validate warns about it.
    assert not os.path.exists(os.path.join(PLUGIN, "CLAUDE.md"))


# --- components -----------------------------------------------------------

@pytest.mark.parametrize("skill", sorted(os.listdir(SKILLS)))
def test_every_skill_has_name_and_description(skill):
    meta, _ = _frontmatter(os.path.join(SKILLS, skill, "SKILL.md"))
    assert meta["name"] == skill
    assert len(meta["description"]) > 40


@pytest.mark.parametrize("skill", sorted(os.listdir(SKILLS)))
def test_skill_references_resolve(skill):
    """A cited file must exist, in the skill's own folder or at the plugin root.

    Both are legitimate: a reference specific to one skill sits beside it, and
    guidance every skill needs — capability boundaries, when to ask rather than
    default, the glossary — is shared at the root rather than copied into each
    skill, because copies drift.
    """
    _, text = _frontmatter(os.path.join(SKILLS, skill, "SKILL.md"))
    for ref in re.findall(r"`(references/[\w-]+\.md|templates/[\w-]+\.md)`", text):
        local = os.path.join(SKILLS, skill, ref)
        shared = os.path.join(PROJECT_ROOT, ref)
        assert os.path.exists(local) or os.path.exists(shared), (
            f"{skill} cites {ref}, which resolves neither beside the skill nor "
            "at the plugin root")


@pytest.mark.parametrize("agent", sorted(os.listdir(AGENTS)))
def test_every_agent_declares_bounded_tools(agent):
    meta, _ = _frontmatter(os.path.join(AGENTS, agent))
    assert meta["name"] == agent[:-3]
    declared = {t.strip() for t in str(meta["tools"]).split(",")}
    assert declared and not declared & {"Write", "Edit", "Bash", "Task", "Agent"}


def test_planned_skills_are_not_enabled():
    enabled = set(os.listdir(SKILLS))
    assert not enabled & {"tax-transition", "transition-frontier", "muni-taxable-compare"}
    assert os.path.exists(os.path.join(PROJECT_ROOT, "planned-skills", "README.md"))
