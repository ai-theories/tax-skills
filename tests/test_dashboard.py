"""The localhost dashboard, exercised over real HTTP.

Starts the actual server on an ephemeral port. An in-process test of the
service objects would miss transport and routing drift, which is most of what
can break between a working backend and a working page.
"""
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer

import pytest

from taxagent.application.bootstrap import PROJECT_ROOT

sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))
import serve_dashboard as dash  # noqa: E402


@pytest.fixture(scope="module")
def httpd_server():
    httpd = HTTPServer(("127.0.0.1", 0), dash.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture(scope="module")
def server(httpd_server):
    return f"http://127.0.0.1:{httpd_server.server_address[1]}"


def get(base, path):
    with urllib.request.urlopen(base + path, timeout=60) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


# --- transport ------------------------------------------------------------

def test_dashboard_page_is_served(server):
    with urllib.request.urlopen(server + "/", timeout=30) as response:
        body = response.read().decode("utf-8")
    assert response.status == 200
    assert "tax-agent" in body


def test_no_static_passthrough(server):
    # The server exposes one page and the API. Nothing else, including source.
    for path in ("/src/taxagent/__init__.py", "/../pyproject.toml", "/rules/tax/manifests/index.yaml"):
        try:
            with urllib.request.urlopen(server + path, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
                assert payload.get("error") == "not found", path
        except urllib.error.HTTPError as exc:
            assert exc.code in (400, 404), path


def test_binds_loopback_only(httpd_server):
    # Checked at the socket, not by probing another address: on a host whose
    # own name resolves to 127.0.0.1 a probe proves nothing.
    assert httpd_server.server_address[0] == "127.0.0.1"
    source = open(os.path.join(PROJECT_ROOT, "scripts", "serve_dashboard.py"),
                  encoding="utf-8").read()
    assert 'HTTPServer(("127.0.0.1"' in source
    assert "0.0.0.0" not in source


def test_site_page_is_served_from_the_api_origin(server):
    import subprocess, sys as _sys
    subprocess.run([_sys.executable, os.path.join(PROJECT_ROOT, "scripts", "build_site.py"),
                    os.path.join(PROJECT_ROOT, "_site")], check=True, capture_output=True)
    with urllib.request.urlopen(server + "/site", timeout=30) as response:
        body = response.read().decode("utf-8")
    assert response.status == 200
    # Same origin as /api, so the live panel is reachable rather than decorative.
    assert "Live scenarios" in body and "/api/scenarios" in body


# --- contract -------------------------------------------------------------

def test_scenarios_declare_their_expected_outcome(server):
    status, scenarios = get(server, "/api/scenarios")
    assert status == 200 and len(scenarios) >= 8
    for scenario in scenarios:
        assert {"id", "title", "expects", "why", "template"} <= set(scenario)


def test_capabilities_expose_only_reachable_tools(server):
    _, caps = get(server, "/api/capabilities")
    names = {tool["name"] for tool in caps["tools"]}
    # Eight servers now, each with its own surface. The per-server agreement is
    # asserted against the live tools/list in test_mcp_surface; here it is
    # enough that every advertised tool names a declared server and that none
    # of them executes anything.
    declared = {s["id"] for s in caps["servers"]}
    # Generated from the server registry rather than listed again here: a
    # hand-written set is a second source of truth that goes stale on the next
    # domain, which is exactly what it did.
    from taxagent.gateway.server_registry import SERVERS
    assert declared == set(SERVERS)
    assert all(tool.get("server") in declared for tool in caps["tools"])
    assert {"review_harvest", "coordinate_household", "calculate_amt",
            "calculate_estate_tax", "calculate_salt_cap",
            "calculate_muni_yield"} <= names
    assert not names & {"execute_trade", "file_return", "place_order"}
    assert caps["execution_capabilities"] == []
    # Workflow steps are declared as internal, not offered as tools.
    assert {op["name"] for op in caps["internal_operations"]}


def test_unknown_scenario_is_a_clean_404(server):
    try:
        get(server, "/api/run/not_a_scenario")
        raise AssertionError("expected a 404")
    except urllib.error.HTTPError as exc:
        assert exc.code == 404


# --- the smoke run --------------------------------------------------------

def test_smoke_run_passes_every_scenario(server):
    _, smoke = get(server, "/api/smoke")
    failures = [r for r in smoke["results"] if not r["ok"]]
    assert not failures, "; ".join(
        f"{r['id']}: expected {r['expected']}, got {r['actual']} {r['detail']}"
        for r in failures)
    assert smoke["passed"] == smoke["total"] >= 8


def test_smoke_catches_a_broken_guarantee(server, monkeypatch):
    # Prove the harness has teeth: if a run started authorizing execution, the
    # status would still look right and the smoke check must still fail it.
    real = dash.run_scenario

    def leaky(scenario_id):
        outcome = real(scenario_id)
        outcome["envelope"]["execution_authorized"] = True
        return outcome

    monkeypatch.setattr(dash, "run_scenario", leaky)
    result = dash.smoke()
    assert result["failed"] == result["total"]
    assert all("execution_authorized" in r["detail"] for r in result["results"])


def test_every_completed_run_pins_its_rule_bundle(server):
    _, scenarios = get(server, "/api/scenarios")
    for scenario in scenarios:
        _, outcome = get(server, "/api/run/" + scenario["id"])
        manifest = outcome.get("manifest")
        if manifest:
            assert manifest["rule_bundle_ref"] == "us-federal-investment-rules.v1"
            assert manifest["rule_bundle_hash"].startswith("sha256:")


# --- calculators ----------------------------------------------------------

def post(base, path, payload):
    request = urllib.request.Request(
        base + path, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def test_calculator_specs_drive_the_form(server):
    """The page's forms are generated from the shared specs, not hand-written."""
    from taxagent.tax import calculators
    status, specs = get(server, "/api/calculators")
    assert status == 200
    assert {s["id"] for s in specs} == set(calculators.CALCULATOR_IDS)
    for spec in specs:
        assert spec["domain"] in calculators.DOMAINS
        assert spec["fields"], spec["id"]
        for field in spec["fields"]:
            assert {"name", "label", "type"} <= set(field), spec["id"]


def test_every_calculator_is_reachable_as_a_tool(server):
    """A calculator the page offers but no server exposes is a dead end.

    The page and the MCP surface are generated from one definition precisely so
    this holds; the assertion is here because that is easy to undo.
    """
    from taxagent.gateway.calculator_dispatcher import BOUNDARIES, CalculatorDispatcher
    _, specs = get(server, "/api/calculators")
    tools = set()
    for domain in BOUNDARIES:
        tools |= {t["name"] for t in CalculatorDispatcher(domain).list_tools()}
    for spec in specs:
        assert f"calculate_{spec['id']}" in tools, spec["id"]


def test_federal_calculator_returns_the_same_answer_as_the_cli(server):
    _, data = post(server, "/api/calc/federal",
                   {"income": "185000", "status": "MFJ", "gain": "50000",
                    "nii": "50000", "deduction": ""})
    assert data["total_tax"] == "30540.00"
    assert data["rule_bundle_ref"] == "us-federal-income-2026.v1"


def test_capgains_calculator_splits_the_bands(server):
    _, data = post(server, "/api/calc/capgains",
                   {"gain": "100000", "ordinary": "90000", "status": "MFJ"})
    assert data["tax"] == "13665.00"
    assert len(data["bands"]) == 2


def test_blank_money_fields_are_treated_as_zero_not_an_error(server):
    _, data = post(server, "/api/calc/federal",
                   {"income": "100000", "status": "SINGLE", "gain": "", "nii": "",
                    "deduction": ""})
    assert data["capital_gain"]["tax"] == "0.00"


def test_unknown_fields_are_ignored_not_trusted(server):
    _, data = post(server, "/api/calc/loss",
                   {"loss": "12000", "status": "MFJ", "rules": "made-up",
                    "total_tax": "0.00"})
    assert data["deductible_this_year"] == "3000.00"
    assert data["carryforward"] == "9000.00"


def test_amt_calculator(server):
    _, data = post(server, "/api/calc/amt",
                   {"amti": "600000", "regular_tax": "120000", "gain": "0",
                    "status": "MFJ"})
    assert data["tentative_minimum_tax"] == "123854.00"
    assert data["amt_payable"] == "3854.00"


def test_qbi_calculator_honours_the_service_business_flag(server):
    _, plain = post(server, "/api/calc/qbi",
                    {"qbi": "200000", "taxable_income": "600000", "wages": "50000",
                     "ubia": "0", "sstb": "no", "status": "MFJ"})
    _, sstb = post(server, "/api/calc/qbi",
                   {"qbi": "200000", "taxable_income": "600000", "wages": "50000",
                    "ubia": "0", "sstb": "yes", "status": "MFJ"})
    assert plain["deduction"] == "25000.00"
    assert sstb["deduction"] == "0.00"


def test_bad_input_is_a_400_not_a_crash(server):
    try:
        post(server, "/api/calc/federal", {"income": "not-a-number", "status": "MFJ"})
        raise AssertionError("expected a 400")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400
        assert "error" in json.loads(exc.read().decode("utf-8"))


def test_unknown_calculator_is_a_404(server):
    try:
        post(server, "/api/calc/nope", {})
        raise AssertionError("expected a 404")
    except urllib.error.HTTPError as exc:
        assert exc.code == 404


def test_figures_are_not_accepted_on_the_query_string(server):
    # A GET with numbers in the URL would put client figures in the access log.
    try:
        get(server, "/api/calc/federal?income=185000")
        raise AssertionError("expected a 404")
    except urllib.error.HTTPError as exc:
        assert exc.code == 404


def test_every_run_that_proposes_something_says_what(server):
    """A run that reaches a conclusion must state the conclusion.

    The envelope carries status, validation and limitations but not the
    proposal, so a page built from it alone shows only caveats. Every scenario
    that produced trades has to carry them, and the figures they realise, in a
    structured field the page can lead with.
    """
    _, scenarios = get(server, "/api/scenarios")
    proposing = []
    for scenario in scenarios:
        _, run = get(server, "/api/run/" + scenario["id"])
        result = run.get("result")
        if not result or not result["trades"]:
            continue
        proposing.append(scenario["id"])
        assert result["cash_raised"] is not None, scenario["id"]
        assert result["net_gain"] is not None, scenario["id"]
        for trade in result["trades"]:
            assert trade["side"] == "SELL"
            assert {"security_id", "quantity", "price_per_share",
                    "account_id"} <= set(trade)
    assert len(proposing) >= 4, f"only {proposing} proposed anything"


def test_a_lot_held_back_says_which_and_why(server):
    """Exclusions are part of the answer, not a footnote to it.

    The harvest run skips a lot for missing basis and holds another back on
    wash-sale risk. Both have to reach the caller attached to a lot id, or the
    proposal looks like the whole opportunity.
    """
    _, run = get(server, "/api/run/completed")
    reasons = {h["reason"]: h["lot_id"] for h in run["result"]["held_back"]}
    assert "MISSING_BASIS" in reasons
    assert "WASH_SALE_RISK" in reasons
    assert all(reasons.values()), "a held-back lot was reported without its id"


def test_a_refusal_carries_no_invented_result(server):
    """A run that never got to a proposal must not imply one."""
    _, run = get(server, "/api/run/unsupported_scope")
    assert run["envelope"]["status"] == "blocked"
    assert run["result"] is None


def test_console_lists_the_servers_the_plugin_declares(server):
    """The page must show what a host would start, not its own idea of it."""
    import json as _json
    import os as _os
    from taxagent.application.bootstrap import PROJECT_ROOT

    declared = _json.load(open(_os.path.join(PROJECT_ROOT, ".mcp.json"),
                               encoding="utf-8"))["mcpServers"]
    _, servers = get(server, "/api/console/servers")
    assert {s["key"] for s in servers} == set(declared)
    for entry in servers:
        assert entry["running"], f"{entry['key']} did not start: {entry.get('error')}"
        assert entry["tools"], entry["key"]
        # The command shown is the declared one, unsubstituted, so a reader can
        # compare it against the file rather than against our rendering of it.
        assert entry["command"].startswith("${CLAUDE_PLUGIN_ROOT}/")


def test_console_runs_a_case_through_a_real_server(server):
    _, result = get(server, "/api/console/case/portfolio-denied")
    assert result["ok"], result
    assert result["actual"] == "ACCESS_DENIED"
    # The frames are the evidence that a server was actually spoken to.
    directions = [f["direction"] for f in result["frames"]]
    assert "sent" in directions and "received" in directions


def test_console_rejects_an_unknown_case(server):
    try:
        get(server, "/api/console/case/no-such-case")
    except urllib.error.HTTPError as exc:
        assert exc.code == 404
    else:
        raise AssertionError("expected a 404")


def test_console_call_reaches_the_named_server(server):
    status, body = post(server, "/api/console/call", {
        "server": "taxagent-federal", "tool": "calculate_niit",
        "arguments": {"nii": "45000", "magi": "260000", "status": "MFJ"}})
    assert status == 200
    envelope = body["result"]["envelope"]
    assert envelope["status"] == "completed"
    # 3.8% of the $10,000 MAGI excess, not of the whole $45,000.
    assert envelope["data"]["amount_subject"] == "10000.00"
    assert envelope["data"]["tax"] == "380.00"
