"""Run use cases against the live servers and judge the outcome.

The expectation beside each case is the whole point: a run that turns a refusal
into an answer, or an answer into a refusal, fails here rather than looking
like a feature.
"""
from __future__ import annotations

import itertools
from typing import Any, Dict, List, Optional

from .mcp_client import McpClient, McpError
from . import use_cases as library


def _outcome(result: Dict[str, Any]) -> str:
    """What actually happened, as one comparable token."""
    if result.get("protocol_error"):
        return "protocol_error"
    envelope = result.get("envelope")
    if not isinstance(envelope, dict):
        return "unparseable"
    return envelope.get("code") or envelope.get("status") or "unknown"


def _matches(expected: str, actual: str) -> bool:
    if expected == "protocol_or_needs_input":
        # A missing field may be caught by the schema at the protocol layer or
        # by the constraint compiler inside the run. Either is a correct
        # refusal to guess; which one depends on where the field is declared.
        return actual in {"protocol_error", "needs_input", "VALIDATION_FAILED",
                          "MISSING_NETTING_BASIS"}
    return actual == expected


#: Idempotency keys are stable within one invocation and unique between them.
#: A run is keyed so that repeating it returns the first answer rather than
#: analysing again — correct, and it makes the console's cases single-use. The
#: counter gives each invocation its own namespace, so clicking a case twice
#: runs it twice, while the case that deliberately replays an earlier key still
#: does, because both are rewritten with the same prefix.
_INVOCATION = itertools.count(1)


def _namespaced(arguments: Dict[str, Any], prefix: str) -> Dict[str, Any]:
    key = arguments.get("idempotency_key")
    if not key:
        return arguments
    return {**arguments, "idempotency_key": f"{prefix}_{key}"}


def run_case(case_id: str, clients: Dict[str, McpClient],
             prefix: Optional[str] = None) -> Dict[str, Any]:
    case = library.by_id(case_id)
    client = clients.get(case["server"])
    if client is None:
        return {"id": case_id, "ok": False, "actual": "no_such_server",
                "detail": f"{case['server']} is not declared in .mcp.json"}

    namespace = prefix or f"i{next(_INVOCATION)}"
    if case.get("depends_on") and prefix is None:
        # Run the predecessor in the same namespace first. This case asserts
        # that repeating a key returns the first run, which says nothing unless
        # that first run happened.
        run_case(case["depends_on"], clients, namespace)
    arguments = _namespaced(case["arguments"], namespace)
    before = len(client.transcript)
    try:
        result = client.call_tool(case["tool"], arguments)
    except McpError as exc:
        return {"id": case_id, "ok": False, "actual": "server_unreachable",
                "detail": str(exc), "frames": []}

    actual = _outcome(result)
    ok = _matches(case["expect"], actual)
    return {
        "id": case_id, "title": case["title"], "group": case["group"],
        "server": case["server"], "tool": case["tool"], "shows": case["shows"],
        "arguments": arguments, "expected": case["expect"],
        "actual": actual, "ok": ok, "detail": "",
        "envelope": result.get("envelope"),
        # The actual conversation, so the page can show the protocol rather
        # than only our reading of it.
        "frames": list(client.transcript[before:]),
    }


def run_all(clients: Dict[str, McpClient],
            group: Optional[str] = None) -> Dict[str, Any]:
    """Run the suite in order against the given servers.

    The suite is stateful on purpose: one case replays an earlier case's
    idempotency key to show that repeating a request returns the first run
    rather than analysing again. That only holds the first time through, so
    the suite must be run against servers that have not already served it.
    `run_suite` below starts fresh processes and is the safe entry point.
    """
    results: List[Dict[str, Any]] = []
    # One namespace for the whole suite, so the replay case still replays the
    # key that the harvest case established earlier in the same run.
    prefix = f"s{next(_INVOCATION)}"
    for case in library.USE_CASES:
        if group and case["group"] != group:
            continue
        results.append(run_case(case["id"], clients, prefix))
    passed = sum(1 for r in results if r["ok"])
    return {"passed": passed, "failed": len(results) - passed,
            "total": len(results), "results": results}


def run_suite(group: Optional[str] = None) -> Dict[str, Any]:
    """Start fresh servers, run the suite, stop them."""
    from .servers import build_clients

    clients = build_clients()
    try:
        return run_all(clients, group)
    finally:
        for client in clients.values():
            client.stop()
