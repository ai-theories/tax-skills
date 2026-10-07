"""The router, and the honesty of its labels.

The router itself is a keyword matcher and is not claimed to be good. What
must hold is that it never mislabels judgment as computation, never names a
tool that does not exist, and never silently picks between agents that matched
equally well.
"""
import os

import pytest

from taxagent.console import chat
from taxagent.console.chat import AGENTIC, ORACLE, REFUSED


def _top(message):
    routes = chat.route(message)
    assert routes, f"nothing matched: {message}"
    return routes[0]


def test_every_example_routes_somewhere():
    """A shipped example that routes nowhere makes the console look broken."""
    unrouted = [q for q in chat.examples() if not chat.route(q)]
    assert not unrouted, unrouted


def test_estate_does_not_route_to_state_and_local():
    """'state' is a substring of 'estate'.

    Plain substring matching sent every estate question to the SALT agent.
    Word boundaries fix it, and this pins the fix.
    """
    route = _top("A $20 million estate with $2 million of lifetime gifts")
    assert route.agent == "estate-planning"


@pytest.mark.parametrize("message,agent", [
    ("Can we harvest losses in the Schwab account to raise $10,000?", "portfolio-tax"),
    ("Should the business be an S corp or a C corp?", "business-entity"),
    ("The partnership return is four months late and there are eight partners",
     "filing-compliance"),
    ("My client moved to Portugal and earned $180,000 abroad", "international-tax"),
])
def test_clear_questions_reach_their_agent(message, agent):
    assert _top(message).agent == agent


def test_every_route_names_a_tool_that_exists():
    """A planned oracle step must be callable, or the plan is fiction."""
    from taxagent.gateway.calculator_dispatcher import BOUNDARIES, CalculatorDispatcher
    from taxagent.gateway.domain_dispatchers import (HouseholdDispatcher,
                                                     PortfolioDispatcher)
    surface = {}
    for key in BOUNDARIES:
        surface[f"taxagent-{key}"] = {t["name"]
                                      for t in CalculatorDispatcher(key).list_tools()}
    for key, cls in (("portfolio", PortfolioDispatcher),
                     ("household", HouseholdDispatcher)):
        surface[f"taxagent-{key}"] = set(cls.tools)

    for intent in chat.INTENTS:
        assert intent["server"] in surface, intent["agent"]
        assert intent["tool"] in surface[intent["server"]], (
            f"{intent['agent']} plans {intent['tool']}, which {intent['server']} "
            f"does not expose")


def test_every_route_has_exactly_one_deterministic_step():
    """One oracle call per plan, so the boundary is unambiguous."""
    for query in chat.examples():
        for route in chat.route(query):
            oracle = [s for s in route.steps if s.kind == ORACLE]
            assert len(oracle) == 1, (route.agent, query)
            assert oracle[0].server and oracle[0].tool


def test_every_route_states_what_it_refuses():
    for query in chat.examples():
        for route in chat.route(query):
            assert any(s.kind == REFUSED for s in route.steps), route.agent


def test_judgment_steps_are_never_labelled_deterministic():
    """The whole point of the labels.

    Routing, reading figures out of a sentence, and stating a result are
    judgment. If any of them were labelled oracle, the console would be
    claiming reproducibility it does not have.
    """
    for query in chat.examples():
        for route in chat.route(query):
            for step in route.steps:
                if step.kind != ORACLE:
                    continue
                assert step.summary.startswith("Call "), step.summary
                assert step.tool, step.summary
            routing = route.steps[0]
            assert routing.kind == AGENTIC
            assert "keyword matcher" in routing.why


def test_a_contested_question_is_flagged_rather_than_guessed():
    """Two agents matching equally well is a question to ask, not a tie to break."""
    route = _top("What does California charge on a $400,000 capital gain?")
    assert route.confidence == "ambiguous"
    asks = [s for s in route.steps if s.kind == AGENTIC and "Ask which" in s.summary]
    assert asks, "an ambiguous route did not plan a clarifying question"


def test_a_weak_route_also_plans_to_ask():
    route = _top("They paid $60,000 of state tax on $550,000 of income")
    assert route.confidence in {"weak", "ambiguous"}
    assert any(s.kind == AGENTIC and ("Ask which" in s.summary
                                      or "Confirm this really is" in s.summary)
               for s in route.steps)


@pytest.mark.parametrize("text,expected", [
    ("$185,000 of income", ["185000"]),
    ("$20 million estate and $2 million of gifts", ["20000000", "2000000"]),
    ("about $10k of losses", ["10000"]),
    ("no figures here", []),
])
def test_money_is_read_out_of_the_sentence(text, expected):
    assert chat.extract_money(text) == expected


def test_the_router_says_it_is_not_an_agent():
    """The console must not be mistaken for the thing it is demonstrating."""
    note = chat.explain()["note"]
    assert "keyword matcher" in note
    assert "not an agent" in note
    assert "not performed" in note


def test_every_agent_named_by_the_router_has_a_definition():
    import os
    from taxagent.application.bootstrap import PROJECT_ROOT
    agents = {f[:-3] for f in os.listdir(os.path.join(PROJECT_ROOT, "agents"))
              if f.endswith(".md")}
    for intent in chat.INTENTS:
        assert intent["agent"] in agents, intent["agent"]


def test_every_skill_named_by_the_router_exists():
    import os
    from taxagent.application.bootstrap import PROJECT_ROOT
    skills = set(os.listdir(os.path.join(PROJECT_ROOT, "skills")))
    for intent in chat.INTENTS:
        if intent["skill"]:
            assert intent["skill"] in skills, intent["skill"]


def test_every_mcp_server_is_reachable_from_the_router():
    """A server no question can route to is a domain the console cannot reach.

    The municipal server shipped with an agent, a skill and ten use cases, and
    no route: every muni question fell through to "the router found nothing".
    """
    import json
    from taxagent.application.bootstrap import PROJECT_ROOT
    declared = json.load(open(os.path.join(PROJECT_ROOT, ".mcp.json"),
                              encoding="utf-8"))["mcpServers"]
    routed = {intent["server"] for intent in chat.INTENTS}
    assert set(declared) - routed == set(), sorted(set(declared) - routed)


def test_every_example_reaches_the_agent_it_names():
    """An example is a claim about who owns the question."""
    for example in chat.EXAMPLES:
        agents = [r.agent for r in chat.route(example["question"])]
        assert example["agent"] in agents, (example["question"], agents)
