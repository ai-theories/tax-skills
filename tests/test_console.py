"""The console's use cases, run against the servers as real processes.

These are slower than the in-process tests and they are the only ones that
exercise what a host actually gets: the declared command, the initialize
handshake, the published tool schemas, and the entitlement gate as a tool call
reaches it. Every bug this file was written to catch had already passed the
in-process suite.
"""
import pytest

from taxagent.console import use_cases as library
from taxagent.console.runner import run_case, run_suite
from taxagent.console.servers import build_clients, declared_servers


@pytest.fixture(scope="module")
def clients():
    started = build_clients()
    yield started
    for client in started.values():
        client.stop()


def test_every_declared_server_starts_and_identifies_itself(clients):
    for key, client in clients.items():
        tools = client.list_tools()
        assert tools, f"{key} started but offers no tools"
        assert client.server_info.get("name") == key, (
            f"{key} calls itself {client.server_info.get('name')}")
        assert "Analysis only" in client.instructions, key


@pytest.mark.parametrize("case_id", [c["id"] for c in library.USE_CASES])
def test_use_case_produces_its_documented_outcome(case_id, clients):
    result = run_case(case_id, clients)
    assert result["ok"], (
        f"{case_id}: expected {result.get('expected')}, got {result['actual']}. "
        f"{result.get('detail', '')}")


def test_the_suite_covers_every_server(clients):
    """A server with no use case is a surface nobody has exercised."""
    covered = {c["server"] for c in library.USE_CASES}
    assert covered == set(declared_servers()), (
        f"servers with no use case: {sorted(set(declared_servers()) - covered)}")


def test_the_suite_covers_refusals_as_well_as_answers(clients):
    """A suite of only happy paths proves the engine, not the boundary."""
    refusals = {c["expect"] for c in library.USE_CASES
                if c["expect"] not in {"completed", "completed_with_limitations",
                                       "accepted"}}
    assert len(refusals) >= 4, f"only these refusals are exercised: {sorted(refusals)}"


def test_the_whole_suite_passes_against_fresh_servers():
    """Run end to end, in order, on servers that have served nothing else.

    The suite replays an idempotency key on purpose, so it only holds against
    processes that have not already run it. Reusing the module's clients here
    would fail for that reason alone and say nothing about the code.
    """
    report = run_suite()
    assert report["total"] == len(library.USE_CASES)
    assert report["failed"] == 0, [r["id"] for r in report["results"] if not r["ok"]]


def test_a_broken_expectation_is_caught(clients):
    """The suite must be able to fail.

    A harness that reports success whatever happens is worse than none, so one
    case is run with a deliberately wrong expectation and must not pass.
    """
    case = library.by_id("portfolio-denied")
    original = case["expect"]
    case["expect"] = "completed"
    try:
        assert not run_case("portfolio-denied", clients)["ok"]
    finally:
        case["expect"] = original
