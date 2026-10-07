"""The MCP boundary exposes only implemented, analysis-only tools."""
import io
import json

import pytest

from taxagent.gateway.calculator_dispatcher import BOUNDARIES, CalculatorDispatcher
from taxagent.gateway.domain_dispatchers import HouseholdDispatcher, PortfolioDispatcher
from taxagent.gateway.mcp_server import McpServer
from taxagent.gateway.server_registry import SERVERS, VERSION

WORKFLOW = {"portfolio": PortfolioDispatcher, "household": HouseholdDispatcher}


def _dispatcher(name, service, principal, source_data):
    if name in BOUNDARIES:
        return CalculatorDispatcher(name)
    return WORKFLOW[name](service, principal, document_loader=lambda ref: source_data)


def _server(service, principal, source_data, name="portfolio"):
    dispatcher = _dispatcher(name, service, principal, source_data)
    spec = SERVERS[name]
    return (McpServer(dispatcher, io.StringIO(), io.StringIO(),
                      server_info={"name": spec.name, "version": VERSION},
                      instructions=spec.instructions),
            dispatcher)


def _call(server, name, arguments=None, message_id=1):
    response = server.handle({"jsonrpc": "2.0", "id": message_id, "method": "tools/call",
                              "params": {"name": name, "arguments": arguments or {}}})
    return json.loads(response["result"]["content"][0]["text"])


@pytest.mark.parametrize("name", sorted(SERVERS))
def test_initialize_declares_analysis_only(name, service, principal, source_data):
    server, _ = _server(service, principal, source_data, name)
    result = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})["result"]
    assert result["serverInfo"]["name"] == SERVERS[name].name
    assert "Analysis only" in result["instructions"]


@pytest.mark.parametrize("name", sorted(SERVERS))
def test_no_server_offers_an_execution_tool(name, service, principal, source_data):
    server, _ = _server(service, principal, source_data, name)
    tools = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    names = {tool["name"] for tool in tools}
    assert names, f"{name} lists no tools at all"
    assert not names & {"execute_trade", "file_return", "place_order"}


@pytest.mark.parametrize("name", sorted(SERVERS))
def test_unregistered_tool_is_refused(name, service, principal, source_data):
    server, _ = _server(service, principal, source_data, name)
    envelope = _call(server, "execute_trade", {"lot_id": "lot_1"})
    assert envelope["status"] == "blocked"
    assert envelope["code"] == "UNSUPPORTED_SCOPE"


def test_notification_gets_no_response(service, principal, source_data):
    server, _ = _server(service, principal, source_data)
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_unknown_method_returns_jsonrpc_error(service, principal, source_data):
    server, _ = _server(service, principal, source_data)
    response = server.handle({"jsonrpc": "2.0", "id": 9, "method": "resources/list"})
    assert response["error"]["code"] == -32601


def test_analysis_runs_through_the_tool_surface(service, principal, source_data, prices):
    server, _ = _server(service, principal, source_data)
    envelope = _call(server, "review_harvest", {
        "subject_ref": "acct_schwab_joint", "document_ref": "fixture",
        "as_of": "2026-09-15", "prices": prices, "cash_target": "10000.00",
        "gain_budget": "15000.00", "netting_basis": "net_gains", "period": "2026",
        "idempotency_key": "idem_mcp_1",
    })
    assert envelope["execution_authorized"] is False
    assert envelope["status"] in {"completed", "completed_with_limitations", "blocked"}


@pytest.mark.parametrize("name", sorted(SERVERS))
def test_registry_and_tool_surface_agree_exactly(name, service, principal, source_data):
    """Per server, in both directions.

    A subset assertion passes while the registry over-claims, which is the
    failure this project exists to prevent. Checking the union of all three
    servers would hide a tool declared on the wrong one, so each is checked
    against its own live surface.
    """
    _, dispatcher = _server(service, principal, source_data, name)
    declared = set(service.capabilities.implemented_tools(name))
    registered = set(dispatcher.registered)
    assert registered == declared, (
        f"{name}: claimed but unreachable: {sorted(declared - registered)}; "
        f"reachable but unclaimed: {sorted(registered - declared)}")


def test_every_declared_server_is_registered_in_code(service):
    """The registry file and the code must ship the same servers."""
    declared = {s["id"] for s in service.capabilities.servers()}
    assert declared == set(SERVERS), (
        f"declared only: {sorted(declared - set(SERVERS))}; "
        f"in code only: {sorted(set(SERVERS) - declared)}")
    for server in service.capabilities.servers():
        assert server["name"] == SERVERS[server["id"]].name
        assert server["needs_identity"] is SERVERS[server["id"]].needs_identity


def test_internal_operations_are_not_offered_as_tools(service, principal, source_data):
    internal = set(service.capabilities.internal_operations())
    assert internal, "workflow steps should be declared, not omitted"
    for name in sorted(SERVERS):
        _, dispatcher = _server(service, principal, source_data, name)
        assert not internal & set(dispatcher.registered), name
        # ... and a host that tries one anyway is refused rather than half-served.
        for operation in sorted(internal):
            envelope = dispatcher.call(operation, {}, "req_x")
            assert envelope["status"] == "blocked"


# --- the server as a process ---------------------------------------------
# These launch the exact command the plugin declares. The in-process tests
# above all passed while `python3 -m taxagent.gateway.mcp_server` started and
# exited doing nothing, because no test ever ran it as a process.

import json as _json
import os as _os
import subprocess
import sys as _sys

from taxagent.application.bootstrap import PROJECT_ROOT

COMMAND = [_sys.executable, "-m", "taxagent.gateway.mcp_server"]


def _command(server=None):
    return COMMAND + (["--server", server] if server else [])


def _env(**overrides):
    env = dict(_os.environ)
    env["PYTHONPATH"] = _os.path.join(PROJECT_ROOT, "src")
    env.update({"TAXAGENT_TENANT_ID": "tenant_ria_1",
                "TAXAGENT_PRINCIPAL_ID": "adv_1",
                "TAXAGENT_ACCOUNTS": "acct_schwab_joint,acct_fid_roth"})
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def _talk(messages, env=None, timeout=90, server=None):
    payload = "".join(_json.dumps(m) + "\n" for m in messages)
    result = subprocess.run(_command(server), input=payload, capture_output=True, text=True,
                            env=env or _env(), timeout=timeout)
    return result, [_json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def test_each_server_process_speaks_the_protocol():
    """Every declared server must start, name itself, and list its own tools.

    Eight processes now ship. A host that starts one and gets silence, or gets
    a server calling itself by another server's name, cannot tell an operator
    which surface refused a call.
    """
    import yaml
    registry = yaml.safe_load(
        open(_os.path.join(PROJECT_ROOT, "capabilities", "tools.yaml"), encoding="utf-8"))
    declared = {}
    for tool in registry["tools"]:
        if tool.get("status") == "implemented":
            declared.setdefault(tool["server"], set()).add(tool["name"])

    for server, expected in sorted(declared.items()):
        _, replies = _talk([{"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}],
                           server=server)
        assert replies, f"{server} produced no output at all"
        assert replies[0]["result"]["serverInfo"]["name"] == f"taxagent-{server}"
        assert {t["name"] for t in replies[1]["result"]["tools"]} == expected, server


def test_the_federal_server_needs_no_identity():
    """It holds no client data, so it must install without entitlements.

    If it demanded a tenant and an account list like the others, an operator
    could not run the calculators without first handing over entitlements that
    nothing in this server reads.
    """
    result, replies = _talk(
        [{"jsonrpc": "2.0", "id": 1, "method": "initialize"},
         {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
          "params": {"name": "calculate_capgains",
                     "arguments": {"gain": "100000", "ordinary": "90000",
                                   "status": "MFJ"}}}],
        env=_env(TAXAGENT_TENANT_ID=None, TAXAGENT_PRINCIPAL_ID=None,
                 TAXAGENT_ACCOUNTS=None),
        server="federal")
    assert result.returncode == 0, result.stderr
    body = _json.loads(replies[1]["result"]["content"][0]["text"])
    assert body["status"] == "completed", body
    assert body["data"]["tax"] == "13665.00"


def test_server_refuses_to_start_without_configured_identity():
    result, _ = _talk([{"jsonrpc": "2.0", "id": 1, "method": "initialize"}],
                      env=_env(TAXAGENT_TENANT_ID=None))
    assert result.returncode == 2
    assert "will not infer a principal" in result.stderr


def test_an_analysis_runs_end_to_end_through_the_protocol():
    """The domain tool takes flat arguments and chooses the template itself."""
    fixture = _os.path.join(PROJECT_ROOT, "tests", "fixtures", "patel_household.json")
    _, replies = _talk([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "review_harvest", "arguments": {
                "subject_ref": "acct_schwab_joint", "document_ref": fixture,
                "as_of": "2026-09-15",
                "prices": {"VTI": "260.00", "AAPL": "205.00", "ARKK": "62.00",
                           "TLT": "88.00"},
                "cash_target": "10000.00", "gain_budget": "15000.00",
                "netting_basis": "net_gains", "period": "2026",
                "idempotency_key": "mcp_e2e_1"}}}], server="portfolio")
    envelope = _json.loads(replies[1]["result"]["content"][0]["text"])
    # Accounts come from the uploaded document; entitlement still gates them.
    assert envelope["status"] == "completed_with_limitations", envelope
    assert envelope["execution_authorized"] is False
    assert envelope["evidence_ref"]


def test_a_caller_never_names_a_template():
    """The template id is chosen server-side, where it is checked.

    It used to be a string the caller supplied, so a skill could route a
    household request at the account engine by typing the wrong one. No tool
    schema accepts it any more.
    """
    for server in ("portfolio", "household"):
        _, replies = _talk([{"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}],
                           server=server)
        for tool in replies[1]["result"]["tools"]:
            properties = tool["inputSchema"].get("properties", {})
            assert "template_id" not in properties, f"{server}.{tool['name']}"


def test_a_document_cannot_widen_entitlement_through_the_protocol():
    fixture = _os.path.join(PROJECT_ROOT, "tests", "fixtures", "patel_uma.json")
    _, replies = _talk([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "resolve_subject", "arguments": {"subject_ref": "acct_uma"}}}])
    envelope = _json.loads(replies[1]["result"]["content"][0]["text"])
    # acct_uma is not in TAXAGENT_ACCOUNTS, so naming it resolves to nothing.
    assert envelope["status"] == "blocked"
    assert envelope["code"] == "ACCESS_DENIED"


def _body(reply):
    return _json.loads(reply["result"]["content"][0]["text"])


def test_subject_discovery_works_on_a_first_call():
    """A fresh session must be able to find what it may analyse.

    Subjects exist only once a document has supplied account metadata, so
    before this `resolve_subject` could only ever be denied — and denied with
    ACCESS_DENIED, which an agent reads as "the user is not entitled to their
    own account" rather than "nothing has been loaded yet".
    """
    document = _os.path.join(PROJECT_ROOT, "tests", "fixtures", "patel_household.json")
    _, replies = _talk([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "resolve_subject",
                    "arguments": {"subject_ref": "acct_schwab_joint",
                                  "document_ref": document}}},
    ])
    body = _body(next(r for r in replies if r.get("id") == 2))
    assert body["status"] == "completed", body
    assert body["data"]["subject_id"] == "acct_schwab_joint"
    assert body["data"]["tax_unit_ids"], "a resolved subject reports no tax unit"


def test_loading_a_document_still_does_not_widen_entitlement():
    """The discovery fix must not become a way in.

    `resolve_subject` now reads a document before resolving. The document
    describes accounts this principal is not entitled to, and those must stay
    unreachable, with the same message as a subject that does not exist.
    """
    document = _os.path.join(PROJECT_ROOT, "tests", "fixtures", "patel_household.json")
    _, replies = _talk([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "resolve_subject",
                    "arguments": {"subject_ref": "acct_spouse_ext",
                                  "document_ref": document}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "resolve_subject",
                    "arguments": {"subject_ref": "acct_does_not_exist",
                                  "document_ref": document}}},
    ])
    denied = _body(next(r for r in replies if r.get("id") == 2))
    unknown = _body(next(r for r in replies if r.get("id") == 3))
    assert denied["code"] == "ACCESS_DENIED"
    # Identical wording: a caller cannot tell a real account from a made-up one.
    assert denied["message"] == unknown["message"]
