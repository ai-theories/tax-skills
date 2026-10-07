"""MCP stdio server.

Implements the JSON-RPC subset the host needs: initialize, tools/list and
tools/call. The principal comes from the server's own configuration, never
from the caller: a tenant or account named in a tool argument is a request,
not a grant.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import date
from typing import Any, Dict, Optional, TextIO

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "taxagent", "version": "0.1.0"}
DEFAULT_INSTRUCTIONS = ("Analysis only. No tool in this server places orders, "
                        "modifies custodian records or files returns.")


class McpServer:
    def __init__(self, dispatcher, stdin: Optional[TextIO] = None,
                 stdout: Optional[TextIO] = None,
                 server_info: Optional[Dict[str, str]] = None,
                 instructions: str = DEFAULT_INSTRUCTIONS):
        self.dispatcher = dispatcher
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        # Each server identifies itself. A host that lists three servers all
        # calling themselves "taxagent" cannot tell the operator which one
        # refused a call.
        self.server_info = server_info or SERVER_INFO
        self.instructions = instructions

    # --- protocol --------------------------------------------------------
    def handle(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        method = message.get("method")
        message_id = message.get("id")

        if method == "initialize":
            return self._result(message_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": self.server_info,
                "instructions": self.instructions,
            })

        if method in {"notifications/initialized", "initialized"}:
            return None

        if method == "tools/list":
            return self._result(message_id, {"tools": self.dispatcher.list_tools()})

        if method == "tools/call":
            params = message.get("params") or {}
            name = params.get("name", "")
            arguments = params.get("arguments") or {}
            request_id = f"req_{uuid.uuid4().hex[:10]}"
            try:
                envelope = self.dispatcher.call(name, arguments, request_id)
            except Exception as exc:  # never leak a stack trace across the boundary
                return self._result(message_id, {
                    "isError": True,
                    "content": [{"type": "text", "text": json.dumps({
                        "schema_version": "1.0", "request_id": request_id, "status": "failed",
                        "code": "VALIDATION_FAILED", "message": str(exc),
                        "execution_authorized": False,
                    })}],
                })
            return self._result(message_id, {
                "isError": envelope.get("status") in {"failed"},
                "content": [{"type": "text", "text": json.dumps(envelope, indent=2)}],
            })

        return self._error(message_id, -32601, f"method not found: {method}")

    @staticmethod
    def _result(message_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": message_id, "result": result}

    @staticmethod
    def _error(message_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}

    # --- transport -------------------------------------------------------
    def serve_forever(self) -> None:
        for line in self.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                self._write(self._error(None, -32700, "parse error"))
                continue
            response = self.handle(message)
            if response is not None:
                self._write(response)

    def _write(self, payload: Dict[str, Any]) -> None:
        self.stdout.write(json.dumps(payload) + "\n")
        self.stdout.flush()


def main(argv: Optional[list] = None) -> None:  # pragma: no cover - process entry point
    """Entry point for every server this plugin ships.

    Which server to run is named on the command line, so one module serves all
    three and a host's declaration says plainly which surface it is starting.
    """
    import argparse

    from .server_registry import SERVERS, VERSION, spec

    parser = argparse.ArgumentParser(
        prog="taxagent-mcp",
        description="Start one of the tax-agent MCP servers on stdio.")
    parser.add_argument("--server", default=os.environ.get("TAXAGENT_SERVER", "portfolio"),
                        choices=sorted(SERVERS),
                        help="Which surface to serve. Default: portfolio.")
    parser.add_argument("--list", action="store_true",
                        help="Print the available servers and exit.")
    args = parser.parse_args(argv)

    if args.list:
        for key in sorted(SERVERS):
            server = SERVERS[key]
            identity = "needs identity" if server.needs_identity else "no identity needed"
            sys.stdout.write(f"{key:<11} {server.name:<22} ({identity})\n")
            sys.stdout.write(f"{'':<11} {server.summary}\n")
        return

    server = spec(args.server)
    McpServer(server.build(),
              server_info={"name": server.name, "version": VERSION},
              instructions=server.instructions).serve_forever()


if __name__ == "__main__":
    # Without this, `python3 -m taxagent.gateway.mcp_server` — the command the
    # plugin declares — starts a process that defines main() and exits.
    main()
