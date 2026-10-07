"""A real MCP client: spawns a server process and speaks JSON-RPC to it.

The dashboard calls the service objects in process. That is useful for showing
what the engines compute, and useless for showing whether the plugin works:
the server entry point, the tool schemas, the framing and the entitlement gate
as a tool actually exercises it are all untested by it. A server that exits on
startup, a tool whose schema does not match what a skill sends, and a discovery
call that denies every request have all shipped past an in-process harness.

So this client does what the host does. It launches the declared command, runs
the initialize handshake, lists tools and calls them over stdio, and keeps every
frame in both directions so the console can show the actual conversation rather
than a rendering of its outcome.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional

PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "taxagent-console", "version": "1.0.0"}


class McpError(RuntimeError):
    """The server could not be started, or did not answer."""


class Frame(dict):
    """One JSON-RPC message, with the direction it travelled and when."""

    @classmethod
    def of(cls, direction: str, payload: Dict[str, Any], elapsed_ms: float = 0.0) -> "Frame":
        return cls(direction=direction, payload=payload,
                   elapsed_ms=round(elapsed_ms, 2))


class McpClient:
    """One long-lived server process, driven over stdio.

    A process per call would be simpler and would also hide every bug that only
    appears on the second request, so the process is kept and reused.
    """

    def __init__(self, server_key: str, command: List[str],
                 env: Optional[Dict[str, str]] = None, cwd: Optional[str] = None):
        self.server_key = server_key
        self.command = command
        self.env = env
        self.cwd = cwd
        self._process: Optional[subprocess.Popen] = None
        self._next_id = 0
        self._lock = threading.Lock()
        self.server_info: Dict[str, Any] = {}
        self.instructions = ""
        self.transcript: List[Frame] = []

    # --- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        try:
            self._process = subprocess.Popen(
                self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=1,
                env=self.env or dict(os.environ), cwd=self.cwd)
        except OSError as exc:
            raise McpError(f"{self.server_key}: could not start: {exc}") from exc

        reply = self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": CLIENT_INFO})
        result = reply.get("result") or {}
        self.server_info = result.get("serverInfo") or {}
        self.instructions = result.get("instructions", "")
        self._notify("notifications/initialized")

    def stop(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()
            process.wait(timeout=5)
        except Exception:
            process.kill()

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def stderr_tail(self, limit: int = 2000) -> str:
        """Whatever the server complained about, for a start that failed."""
        if self._process is None or self._process.stderr is None:
            return ""
        try:
            os.set_blocking(self._process.stderr.fileno(), False)
            return (self._process.stderr.read() or "")[:limit]
        except Exception:
            return ""

    # --- calls -----------------------------------------------------------
    def list_tools(self) -> List[Dict[str, Any]]:
        self.start()
        return (self._request("tools/list", {}).get("result") or {}).get("tools", [])

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Call a tool and return {envelope, raw, is_error}.

        A tool result is text content carrying our envelope. It is parsed here
        so the console can render it, and kept raw as well so what the server
        actually sent is visible rather than only our reading of it.
        """
        self.start()
        reply = self._request("tools/call", {"name": name, "arguments": arguments})
        if "error" in reply:
            return {"envelope": None, "raw": reply["error"], "is_error": True,
                    "protocol_error": True}
        result = reply.get("result") or {}
        content = result.get("content") or [{}]
        text = content[0].get("text", "")
        try:
            envelope = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            envelope = None
        return {"envelope": envelope, "raw": text, "is_error": bool(result.get("isError"))}

    # --- transport -------------------------------------------------------
    def _request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self._next_id += 1
            message = {"jsonrpc": "2.0", "id": self._next_id,
                       "method": method, "params": params}
            started = time.monotonic()
            self._write(message)
            self.transcript.append(Frame.of("sent", message))
            reply = self._read_until(self._next_id)
            elapsed = (time.monotonic() - started) * 1000
            self.transcript.append(Frame.of("received", reply, elapsed))
            return reply

    def _notify(self, method: str) -> None:
        with self._lock:
            message = {"jsonrpc": "2.0", "method": method}
            self._write(message)
            self.transcript.append(Frame.of("sent", message))

    def _write(self, message: Dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise McpError(f"{self.server_key}: the server is not running. "
                           f"{self.stderr_tail(400)}".strip())
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    def _read_until(self, message_id: int) -> Dict[str, Any]:
        """Read frames until the one answering this id arrives.

        Notifications and unrelated frames are kept in the transcript rather
        than discarded: what the server volunteered is part of the evidence
        that it behaved.
        """
        process = self._process
        assert process is not None and process.stdout is not None
        while True:
            line = process.stdout.readline()
            if not line:
                raise McpError(
                    f"{self.server_key}: the server exited without answering. "
                    f"{self.stderr_tail(600)}".strip())
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                self.transcript.append(Frame.of("unparseable", {"line": line[:400]}))
                continue
            if message.get("id") == message_id:
                return message
            self.transcript.append(Frame.of("received", message))
