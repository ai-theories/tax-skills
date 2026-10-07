#!/usr/bin/env python3
"""Minimal local dashboard for the tax-agent analysis backend.

Serves one HTML page and a small read-only JSON API on the loopback interface.
Unlike a browser-side dashboard, no calculation happens in the page: every
number comes from the same AnalysisService the MCP tools use, so what the
dashboard shows is what an agent would receive.

Only the dashboard file and the declared API routes are reachable. There is no
static file passthrough, and no endpoint mutates anything outside the in-memory
store.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from datetime import date
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from taxagent.application.bootstrap import build_service          # noqa: E402
from taxagent.connectors.uploaded_files import UploadedFileSource  # noqa: E402
from taxagent.domain.identities import (Account, Principal,        # noqa: E402
                                        Registration, Subject)
from taxagent.domain.sleeves import PlannedAcquisition             # noqa: E402
from taxagent.decisions.rule_registry import RulePackError            # noqa: E402
from taxagent.domain.money import MoneyError                          # noqa: E402
from taxagent.evidence.report_renderer import render_review_brief     # noqa: E402
from taxagent.tax import calculators                                  # noqa: E402
from taxagent.console import chat
from taxagent.console.inventory import inventory                                     # noqa: E402
from taxagent.console import use_cases as uc                          # noqa: E402
from taxagent.console.runner import run_case, run_suite               # noqa: E402
from taxagent.console.servers import build_clients, declared_servers  # noqa: E402
from taxagent.console.mcp_client import McpError                      # noqa: E402

DASHBOARD = os.path.join(ROOT, "dashboard.html")
SITE_INDEX = os.path.join(ROOT, "_site", "index.html")
PORT = int(os.environ.get("PORT", "4180"))
TENANT = "tenant_ria_1"
AS_OF = date(2026, 9, 15)
PRICES = {"VTI": "260.00", "AAPL": "205.00", "ARKK": "62.00", "TLT": "88.00",
          "AAA": "60.00", "BBB": "200.00"}

ACCOUNTS = [
    Account("acct_schwab_joint", TENANT, "tu_patel", Registration.JOINT_TAXABLE,
            "Schwab", "owner_patel"),
    Account("acct_fid_roth", TENANT, "tu_patel", Registration.ROTH_IRA,
            "Fidelity", "owner_patel"),
    Account("acct_spouse_ext", TENANT, "tu_patel", Registration.TAXABLE,
            "Vanguard", "owner_spouse"),
    Account("acct_uma", TENANT, "tu_patel", Registration.TAXABLE, "Schwab", "owner_patel"),
    Account("acct_a", TENANT, "tu_x", Registration.TAXABLE, "Schwab", "owner_x"),
    Account("acct_b", TENANT, "tu_x", Registration.TAXABLE, "Fidelity", "owner_x"),
]
SUBJECTS = [
    Subject("acct_schwab_joint", TENANT, "account", ("acct_schwab_joint",), ("tu_patel",)),
    Subject("household_patel", TENANT, "household",
            ("acct_schwab_joint", "acct_fid_roth"), ("tu_patel",)),
    Subject("acct_uma", TENANT, "account", ("acct_uma",), ("tu_patel",)),
    Subject("household_x", TENANT, "household", ("acct_a", "acct_b"), ("tu_x",)),
]
PRINCIPAL = Principal("adv_1", TENANT, frozenset({"analysis"}),
                      frozenset({"acct_schwab_joint", "acct_fid_roth", "acct_uma",
                                 "acct_a", "acct_b"}))


def _intent(cash: str, netting: Optional[str], period: Optional[str],
            budget: str = "15000.00", withdrawal: str = "acct_schwab_joint") -> Dict[str, Any]:
    return {
        "schema_version": "1.0", "request_id": "req_dash", "intent": "harvest_review",
        "mode": "analysis_only", "subject_ref": withdrawal,
        "objectives": [{"type": "raise_cash", "amount": cash, "currency": "USD",
                        "withdrawal_account": withdrawal}],
        "constraints": [{"type": "realized_gain_budget", "amount": budget,
                         "currency": "USD", "netting_basis": netting, "period": period}],
    }


SCENARIOS: List[Dict[str, Any]] = [
    {"id": "unsupported_scope", "title": "Household under harvest-review",
     "expects": "UNSUPPORTED_SCOPE",
     "why": "No validated joint optimizer exists, so the request is refused rather than "
            "approximated by running an account engine twice.",
     "template": "harvest-review.v1", "subject": "household_patel", "fixture": "patel_household",
     "intent": _intent("50000.00", "net_gains", "2026")},
    {"id": "needs_input", "title": "Gain budget with no netting basis or period",
     "expects": "needs_input",
     "why": "Gross and net readings of the same sentence give different answers, so the "
            "compiler asks instead of defaulting.",
     "template": "harvest-review.v1", "subject": "acct_schwab_joint", "fixture": "patel_household",
     "intent": _intent("50000.00", None, None)},
    {"id": "infeasible", "title": "$50,000 target inside a $15,000 gain budget",
     "expects": "INFEASIBLE_CONSTRAINTS",
     "why": "The shortfall is reported. Constraints are never quietly relaxed to manufacture "
            "a result.",
     "template": "harvest-review.v1", "subject": "acct_schwab_joint", "fixture": "patel_household",
     "intent": _intent("50000.00", "net_gains", "2026")},
    {"id": "completed", "title": "$10,000 target — completes with limitations",
     "expects": "completed_with_limitations",
     "why": "Harvests losses against a $10,000 cash need, screens for wash sales, "
            "and returns a reviewer-ready brief with the gaps it could not see.",
     "template": "harvest-review.v1", "subject": "acct_schwab_joint", "fixture": "patel_household",
     "intent": _intent("10000.00", "net_gains", "2026")},
    {"id": "coordinated", "title": "Same household, coordinated",
     "expects": "completed_with_limitations",
     "why": "Runs both household accounts against one shared budget per taxpayer, with "
            "reservations so no two runs spend the same allowance.",
     "template": "household-coordination.v1", "subject": "household_patel",
     "fixture": "patel_household", "intent": _intent("10000.00", "net_gains", "2026")},
    {"id": "uma", "title": "UMA: one sleeve would wash another's loss",
     "expects": "completed_with_limitations",
     "why": "Catches a cross-sleeve wash sale before it happens: one manager plans to buy "
            "what another is harvesting, in the same account and the same taxpayer.",
     "template": "household-coordination.v1", "subject": "acct_uma", "fixture": "patel_uma",
     "intent": _intent("10000.00", "net_gains", "2026", withdrawal="acct_uma"),
     "planned": [{"account_id": "acct_uma", "security_id": "ARKK", "quantity": "300",
                  "trade_date": "2026-09-25", "unit_id": "sleeve_beta"}]},
    {"id": "gap", "title": "Shortfall, with the ceiling it was measured against",
     "expects": "INFEASIBLE_CONSTRAINTS",
     "why": "Falls short of the cash target, then says by how much it could ever have been "
            "beaten: an LP bound on the cash any allocation of the same budget could raise.",
     "template": "household-coordination.v1", "subject": "household_x",
     "fixture": "order_dependence",
     "intent": _intent("30000.00", "gross_gains", "2026", budget="5000.00",
                       withdrawal="acct_a")},
    {"id": "wrong_year", "title": "Analysis dated 2019",
     "expects": "WRONG_RULE_YEAR",
     "why": "No reviewed rule pack covers 2019, so the run is blocked rather than computed "
            "from an assumed rule.",
     "template": "harvest-review.v1", "subject": "acct_schwab_joint", "fixture": "patel_household",
     "intent": _intent("10000.00", "net_gains", "2026"), "as_of": "2019-09-15"},
]


# The calculator surface is defined in the package so this page, the CLI and
# the federal MCP server cannot drift apart. See taxagent/tax/calculators.py.
CALCULATORS = calculators.CALCULATORS


def run_calculator(calc_id: str, values: Dict[str, Any]) -> Dict[str, Any]:
    return calculators.run(calc_id, values)



#: Working analyses first. The refusals matter, but a dashboard that opens with
#: them reads as a list of things that do not work.
SCENARIOS.sort(key=lambda s: ['completed', 'coordinated', 'uma', 'gap', 'infeasible', 'needs_input', 'unsupported_scope', 'wrong_year'].index(s["id"]))


def _source(fixture: str):
    path = os.path.join(ROOT, "tests", "fixtures", f"{fixture}.json")
    return UploadedFileSource().load_file(TENANT, path)


def _headline(package: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The answer itself: what the run proposes and what it realises.

    The envelope carries status, validation and limitations. None of those say
    what the analysis actually concluded, so a page built from the envelope
    alone shows only caveats. Pull the proposal out of the evidence package so
    the result can be stated first and the caveats read as qualifications of it.
    """
    results = package.get("results") or {}
    candidate = results.get("candidate") or {}
    diagnostics = candidate.get("diagnostics") or {}
    totals = (results.get("gain_loss") or {}).get("totals") or {}
    wash = results.get("wash_sale") or {}
    trades = candidate.get("trades") or []
    if not trades and not totals:
        return None
    return {
        "trades": trades,
        "cash_raised": diagnostics.get("cash_raised"),
        "net_gain": totals.get("net_gain"),
        "short_term_gain": totals.get("short_term_gain"),
        "long_term_gain": totals.get("long_term_gain"),
        "disallowed_loss": wash.get("total_disallowed_loss"),
        "held_back": [
            {"lot_id": f.get("lot_id"), "reason": f.get("reason")}
            for f in diagnostics.get("wash_sale_flagged", [])
        ] + [
            {"lot_id": s.get("lot_id"), "reason": s.get("reason")}
            for s in diagnostics.get("lots_skipped", [])
            if s.get("reason") not in ("CASH_TARGET_MET",)
        ],
    }


def run_scenario(scenario_id: str) -> Dict[str, Any]:
    scenario = next((s for s in SCENARIOS if s["id"] == scenario_id), None)
    if scenario is None:
        raise KeyError(scenario_id)

    service = build_service(SUBJECTS, ACCOUNTS)
    planned = [
        PlannedAcquisition(p["account_id"], p["security_id"], Decimal(p["quantity"]),
                           date.fromisoformat(p["trade_date"]), p["unit_id"])
        for p in scenario.get("planned", [])
    ]
    as_of = date.fromisoformat(scenario["as_of"]) if scenario.get("as_of") else AS_OF

    envelope = service.start_analysis(
        PRINCIPAL, "req_dash", scenario["template"], scenario["subject"],
        _source(scenario["fixture"]), as_of, PRICES, scenario["intent"],
        f"dash_{scenario_id}", planned_acquisitions=planned)

    payload: Dict[str, Any] = {"scenario": {k: scenario[k] for k in
                                            ("id", "title", "expects", "why", "template")},
                               "envelope": envelope, "brief": None, "diagnostics": None,
                               "manifest": None, "result": None}
    evidence_ref = envelope.get("evidence_ref")
    if evidence_ref:
        package = service.store.get_artifact(TENANT, evidence_ref)
        if package:
            payload["brief"] = render_review_brief(package)
            payload["manifest"] = package["engine_manifest"]
            candidate = package["results"].get("candidate") or {}
            payload["diagnostics"] = candidate.get("diagnostics")
            payload["result"] = _headline(package)
    return payload


def smoke() -> Dict[str, Any]:
    """Run every scenario and check it still produces the outcome it documents.

    The expectation lives next to the scenario, so a change that silently turns
    a refusal into an answer fails here rather than looking like a feature.
    """
    results: List[Dict[str, Any]] = []
    for scenario in SCENARIOS:
        expected = scenario["expects"]
        try:
            outcome = run_scenario(scenario["id"])
            envelope = outcome["envelope"]
            actual = envelope.get("code") or envelope["status"]
            ok = expected in (envelope.get("code"), envelope["status"])
            detail = ""
            if ok:
                # A passing status is not enough: the guarantees must hold too.
                if envelope.get("execution_authorized") is not False:
                    ok, detail = False, "execution_authorized is not false"
                elif outcome.get("manifest") and not outcome["manifest"].get("rule_bundle_ref"):
                    ok, detail = False, "rule bundle is not pinned"
        except Exception as exc:                          # a crash is a failure
            ok, actual, detail = False, "exception", str(exc)
        results.append({"id": scenario["id"], "title": scenario["title"],
                        "expected": expected, "actual": actual, "ok": ok,
                        "detail": detail})
    passed = sum(1 for r in results if r["ok"])
    return {"passed": passed, "failed": len(results) - passed,
            "total": len(results), "results": results}


def capabilities() -> Dict[str, Any]:
    service = build_service(SUBJECTS, ACCOUNTS)
    return service.get_capabilities(PRINCIPAL, "req_dash")["data"]


class Handler(BaseHTTPRequestHandler):
    server_version = "taxagent-dashboard"

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: Any) -> None:
        self._send(status, json.dumps(payload, indent=2).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/":
            with open(DASHBOARD, "rb") as handle:
                self._send(200, handle.read(), "text/html; charset=utf-8")
        elif path == "/site":
            # The built landing page, served from the same origin as the API so
            # its progressive "live scenarios" panel can actually turn on.
            if not os.path.exists(SITE_INDEX):
                self._json(404, {"error": "run scripts/build_site.py first"})
                return
            with open(SITE_INDEX, "rb") as handle:
                self._send(200, handle.read(), "text/html; charset=utf-8")
        elif path == "/api/scenarios":
            self._json(200, [{k: s[k] for k in ("id", "title", "expects", "why", "template")}
                             for s in SCENARIOS])
        elif path == "/api/calculators":
            self._json(200, CALCULATORS)
        elif path == "/api/smoke":
            self._json(200, smoke())
        elif path == "/api/capabilities":
            self._json(200, capabilities())
        elif path == "/api/console/servers":
            try:
                self._json(200, console_servers())
            except Exception as exc:
                self._json(500, {"error": str(exc)})
        elif path == "/api/chat/explain":
            self._json(200, {"explain": chat.explain(), "examples": chat.examples(),
                             "groups": chat.example_groups()})
        elif path == "/api/console/inventory":
            self._json(200, inventory())
        elif path == "/api/console/cases":
            self._json(200, {"groups": uc.GROUPS, "cases": uc.summary()})
        elif path == "/api/console/suite":
            # Fresh processes: the suite replays an idempotency key on purpose.
            try:
                self._json(200, run_suite())
            except Exception as exc:
                self._json(500, {"error": str(exc)})
        elif path.startswith("/api/console/case/"):
            try:
                self._json(200, run_case(path[len("/api/console/case/"):],
                                         console_clients()))
            except KeyError:
                self._json(404, {"error": "unknown use case"})
            except Exception as exc:
                self._json(500, {"error": str(exc)})
        elif path.startswith("/api/run/"):
            try:
                self._json(200, run_scenario(path[len("/api/run/"):]))
            except KeyError:
                self._json(404, {"error": "unknown scenario"})
            except Exception as exc:                     # never leak a stack trace
                self._json(500, {"error": str(exc)})
        else:
            # No static passthrough: only the dashboard and the API exist.
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        """Figures arrive in a body, never a query string.

        A URL would put a client's income into the access log and the browser
        history; the request line this server logs carries only the path.
        """
        path = self.path.split("?")[0]
        if path == "/api/console/call":
            self._console_call()
            return
        if path == "/api/chat/route":
            self._chat_route()
            return
        if not path.startswith("/api/calc/"):
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 64_000:
                self._json(413, {"error": "payload too large"})
                return
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            self._json(200, run_calculator(path[len("/api/calc/"):], body))
        except KeyError:
            self._json(404, {"error": "unknown calculator"})
        except (RulePackError, MoneyError, ValueError, ArithmeticError) as exc:
            # A bad figure is the caller's, not a server fault.
            self._json(400, {"error": str(exc)})

    def _console_call(self) -> None:
        """Call any tool on any declared server, as the host would."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 256_000:
                self._json(413, {"error": "payload too large"})
                return
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            self._json(200, console_call(body.get("server", ""), body.get("tool", ""),
                                         body.get("arguments") or {}))
        except KeyError as exc:
            self._json(404, {"error": f"no such server: {exc}"})
        except McpError as exc:
            # The server died or never started. That is the answer, not a 500.
            self._json(200, {"result": {"envelope": None, "is_error": True,
                                        "raw": str(exc), "transport_error": True},
                             "frames": []})
        except Exception as exc:
            self._json(500, {"error": str(exc)})

    def _chat_route(self) -> None:
        """Plan a conversation: which agent, and which steps are deterministic.

        The planned tool call is returned with an argument template rather than
        executed. Filling it from figures guessed out of a sentence and then
        presenting the result would be the exact overclaim this console exists
        to expose, so the caller sees the arguments and runs it deliberately.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 16_000:
                self._json(413, {"error": "payload too large"})
                return
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            message = str(body.get("message", ""))[:2000]
            routes = [r.to_json() for r in chat.route(message)]
            for route in routes:
                for step in route["steps"]:
                    if step["kind"] == "oracle" and step["tool"]:
                        step["argument_template"] = _argument_template(step["tool"])
            self._json(200, {
                "message": message,
                "figures_read": chat.extract_money(message),
                "routes": routes,
                "routed": bool(routes),
            })
        except Exception as exc:
            self._json(500, {"error": str(exc)})

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))



# --- the MCP console ------------------------------------------------------
# These endpoints drive the servers the plugin declares, as processes, over
# JSON-RPC. Everything above this line calls the service objects directly and
# so cannot see a broken server; everything below can see nothing else.

_CONSOLE: Dict[str, Any] = {"clients": None}
_CONSOLE_LOCK = threading.Lock()


def console_clients(restart: bool = False) -> Dict[str, Any]:
    """The live server processes, started on first use.

    Held open between calls so the page can show a conversation rather than a
    series of unrelated first requests — and so a bug that only appears on the
    second call is reachable.
    """
    with _CONSOLE_LOCK:
        if restart and _CONSOLE["clients"]:
            for client in _CONSOLE["clients"].values():
                client.stop()
            _CONSOLE["clients"] = None
        if _CONSOLE["clients"] is None:
            _CONSOLE["clients"] = build_clients(python=sys.executable)
        return _CONSOLE["clients"]


def _argument_template(tool: str) -> Dict[str, Any]:
    """Defaults from the tool's own schema, marked as defaults.

    These are the spec's example figures, not anything read from the question.
    The console labels them so nobody mistakes a default for their own number.
    """
    if tool.startswith("calculate_"):
        calc_id = tool[len("calculate_"):]
        try:
            return {f["name"]: f.get("default", "")
                    for f in calculators.spec(calc_id)["fields"]}
        except KeyError:
            return {}
    case = next((c for c in uc.USE_CASES if c["tool"] == tool), None)
    return dict(case["arguments"]) if case else {}


def console_servers() -> List[Dict[str, Any]]:
    """Each declared server, started, with the tools it actually publishes."""
    declared = declared_servers()
    clients = console_clients()
    out = []
    for key, config in declared.items():
        entry: Dict[str, Any] = {
            "key": key,
            "command": " ".join([config["command"], *config.get("args", [])]),
            "needs_identity": "TAXAGENT_ACCOUNTS" in (config.get("env") or {}),
        }
        try:
            tools = clients[key].list_tools()
            entry.update({"running": True, "tools": tools,
                          "server_info": clients[key].server_info,
                          "instructions": clients[key].instructions})
        except (McpError, KeyError) as exc:
            # A server that will not start is the most important thing this
            # page can show, so it is reported rather than omitted.
            entry.update({"running": False, "tools": [], "error": str(exc)})
        out.append(entry)
    return out


def console_call(server_key: str, tool: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    clients = console_clients()
    if server_key not in clients:
        raise KeyError(server_key)
    client = clients[server_key]
    before = len(client.transcript)
    result = client.call_tool(tool, arguments)
    return {"result": result, "frames": list(client.transcript[before:])}



def main() -> None:
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"tax-agent dashboard: http://127.0.0.1:{PORT}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
