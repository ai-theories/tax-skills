"""How the console launches the servers the plugin declares.

The commands are read from .mcp.json rather than written again here. A console
that launched the servers its own way could pass while the declared command was
broken — which is exactly the failure that shipped before.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from typing import Any, Dict, List, Optional

from ..application.bootstrap import PROJECT_ROOT
from .mcp_client import McpClient

MCP_CONFIG = os.path.join(PROJECT_ROOT, ".mcp.json")

# Identity for the console's own session. Real deployments bind a principal
# from an identity provider; this stands in for one, and the entitlement list
# is deliberately narrower than the fixtures so a denial is reachable.
CONSOLE_IDENTITY = {
    "TAXAGENT_TENANT_ID": "tenant_ria_1",
    "TAXAGENT_PRINCIPAL_ID": "adv_console",
    "TAXAGENT_ACCOUNTS": "acct_schwab_joint,acct_fid_roth,acct_uma,acct_a,acct_b",
}


def _substitute(value: str) -> str:
    """Resolve the substitutions a host would perform before launching."""
    value = value.replace("${CLAUDE_PLUGIN_ROOT}", PROJECT_ROOT)
    for key, replacement in (("tenant_id", CONSOLE_IDENTITY["TAXAGENT_TENANT_ID"]),
                             ("principal_id", CONSOLE_IDENTITY["TAXAGENT_PRINCIPAL_ID"]),
                             ("accounts", CONSOLE_IDENTITY["TAXAGENT_ACCOUNTS"])):
        value = value.replace("${user_config." + key + "}", replacement)
    return value


def declared_servers() -> Dict[str, Dict[str, Any]]:
    with open(MCP_CONFIG, encoding="utf-8") as handle:
        return json.load(handle).get("mcpServers", {})


def build_clients(python: Optional[str] = None) -> Dict[str, McpClient]:
    """One client per declared server, launched the way a host would.

    The declared command is a shell launcher that hunts for a suitable
    interpreter. When the console is already running under one, it is passed
    through so both sides are the same build.
    """
    clients: Dict[str, McpClient] = {}
    for key, server in declared_servers().items():
        command = [_substitute(server["command"]), *[_substitute(a) for a in
                                                     server.get("args", [])]]
        env = dict(os.environ)
        env.update({k: _substitute(v) for k, v in (server.get("env") or {}).items()})
        env["TAXAGENT_PYTHON"] = python or sys.executable
        if not (os.path.exists(command[0]) or shutil.which(command[0])):
            raise FileNotFoundError(f"{key}: declared command {command[0]} does not exist")
        clients[key] = McpClient(key, command, env=env, cwd=PROJECT_ROOT)
    return clients


def server_keys() -> List[str]:
    return list(declared_servers())
