#!/usr/bin/env python3
"""Smoke-test a running dashboard over HTTP.

Exercises the real transport, not just the service objects: if the server is up
but the API contract has drifted, this fails where an in-process test would not.

    python3 scripts/smoke_test.py [--url http://127.0.0.1:4180]

Exits non-zero on any failure, so it can gate a deploy.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

REQUIRED_TOOLS = {"get_capabilities", "resolve_subject", "start_analysis",
                  "get_run_status", "cancel_run", "get_scenario"}
FORBIDDEN_TOOLS = {"execute_trade", "file_return", "place_order"}


def get(url: str, path: str):
    with urllib.request.urlopen(url + path, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:4180")
    args = parser.parse_args()
    url = args.url.rstrip("/")

    try:
        capabilities = get(url, "/api/capabilities")
    except urllib.error.URLError as exc:
        print(f"FAIL  cannot reach {url}: {exc}")
        print("      start it with: python3 scripts/serve_dashboard.py")
        return 2

    failures = 0

    names = {tool["name"] for tool in capabilities["tools"]}
    if names != REQUIRED_TOOLS:
        print(f"FAIL  tool surface drifted: {sorted(names ^ REQUIRED_TOOLS)}")
        failures += 1
    else:
        print(f"pass  tool surface: {len(names)} tools")

    if names & FORBIDDEN_TOOLS:
        print(f"FAIL  an execution tool is exposed: {sorted(names & FORBIDDEN_TOOLS)}")
        failures += 1
    else:
        print("pass  no execution tool is exposed")

    if capabilities.get("execution_capabilities") != []:
        print("FAIL  execution_capabilities is not empty")
        failures += 1
    else:
        print("pass  execution_capabilities is empty")

    smoke = get(url, "/api/smoke")
    for result in smoke["results"]:
        if result["ok"]:
            print(f"pass  {result['id']}: {result['actual']}")
        else:
            failures += 1
            detail = f" — {result['detail']}" if result["detail"] else ""
            print(f"FAIL  {result['id']}: expected {result['expected']}, "
                  f"got {result['actual']}{detail}")

    print()
    print(f"{smoke['passed']}/{smoke['total']} scenarios, {failures} check(s) failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
