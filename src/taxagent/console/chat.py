"""Routing a question to an agent, and separating what is computed from what
is judged.

This module is NOT an agent and does not pretend to be one. In a real
deployment the host's model reads the agent definitions and the skills, asks
the clarifying questions and decides which tool to call. Here a keyword matcher
picks the likely agent and builds the plan that agent would follow, so the
split can be shown concretely:

  oracle    a deterministic MCP tool call. Same inputs, same output, every
            time, from a reviewed rule pack with a cited authority. The console
            actually makes these calls.
  agentic   judgment: which figures the question needs, what to ask when it is
            ambiguous, which of several tools applies, and how to state the
            result honestly. The console describes these and does not perform
            them.
  refused   outside what any server here computes.

Labelling matters more than the routing does. A console that ran a calculation
and presented the whole answer as "the system's" would hide exactly the line a
reviewer needs to see.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from ..application.bootstrap import PROJECT_ROOT

AGENTS_DIR = os.path.join(PROJECT_ROOT, "agents")
SKILLS_DIR = os.path.join(PROJECT_ROOT, "skills")

ORACLE = "oracle"
AGENTIC = "agentic"
REFUSED = "refused"

KIND_MEANING = {
    ORACLE: ("Deterministic. A real MCP tool call into a rule pack with a cited "
             "authority: same inputs, same answer, every time, and replayable from "
             "the evidence."),
    AGENTIC: ("Judgment. Which figures the question needs, what to ask when it is "
              "ambiguous, which tool applies, and how to state the result without "
              "overclaiming. A model does this; the console only shows where it "
              "happens."),
    REFUSED: ("Outside what any server in this deployment computes. Named rather "
              "than approximated."),
}


@dataclass(frozen=True)
class Step:
    kind: str
    summary: str
    server: Optional[str] = None
    tool: Optional[str] = None
    arguments: Dict[str, Any] = field(default_factory=dict)
    why: str = ""

    def to_json(self) -> Dict[str, Any]:
        return {"kind": self.kind, "summary": self.summary, "server": self.server,
                "tool": self.tool, "arguments": dict(self.arguments), "why": self.why}


@dataclass(frozen=True)
class Route:
    agent: str
    skill: Optional[str]
    confidence: str
    matched: List[str]
    steps: List[Step]
    alternatives: List[str]

    def to_json(self) -> Dict[str, Any]:
        return {"agent": self.agent, "skill": self.skill, "confidence": self.confidence,
                "matched": list(self.matched), "alternatives": list(self.alternatives),
                "steps": [s.to_json() for s in self.steps]}


# --- what each agent answers, and what it must establish first -------------
# `asks` are the agentic steps: the things a model has to settle before any
# deterministic call is worth making. They come from the skill files.

INTENTS: List[Dict[str, Any]] = [
    {"agent": "federal-estimator", "skill": "", "server": "taxagent-federal",
     "keywords": ["income tax", "tax bracket", "marginal rate", "what will i owe",
                  "capital gain", "long-term gain", "niit", "surtax", "amt",
                  "alternative minimum", "199a", "qbi", "qualified business income",
                  "capital loss"],
     "tool": "calculate_federal",
     "asks": ["Filing status and tax year.",
              "Whether the income figure is gross or already after the deduction: "
              "`calculate_federal` takes gross, `calculate_capgains` takes taxable.",
              "Whether AMT or the section 199A deduction is also in scope, since they "
              "are computed separately and never folded into one liability."],
     "refuses": ["Credits, self-employment tax and state tax, so this is never a "
                 "complete return."]},

    {"agent": "portfolio-tax", "skill": "harvest-review", "server": "taxagent-portfolio",
     "keywords": ["harvest", "tax loss", "tax-loss", "wash sale", "lot", "cost basis",
                  "sell shares", "raise cash", "offset gains", "holdings"],
     "tool": "review_harvest",
     "asks": ["Which account, and whether the caller is entitled to read it.",
              "Whether a gain budget is net of losses or on gross gains: the two "
              "readings give different answers and the server will not choose.",
              "Any purchases already planned but not yet placed, so a wash sale is "
              "caught before it is created."],
     "refuses": ["Placing the trade. Nothing here authorizes execution."]},

    {"agent": "household-coordination", "skill": "household-coordination",
     "server": "taxagent-household",
     "keywords": ["household", "spouse", "both accounts", "uma", "sleeve", "managers",
                  "across accounts", "family"],
     "tool": "coordinate_household",
     "asks": ["Whether the household is one tax unit or several: budgets are "
              "allocated per tax unit and are never pooled across them.",
              "Which accounts and sleeves are in scope, and what each manager plans "
              "to buy."],
     "refuses": ["A joint optimum. Allocation is sequential and order-dependent; the "
                 "gap to a relaxed bound is reported rather than hidden."]},

    {"agent": "estate-planning", "skill": "estate-review", "server": "taxagent-estate",
     "keywords": ["estate", "inheritance", "death", "exclusion", "unified credit",
                  "portability", "dsue", "trust income", "fiduciary", "beneficiary"],
     "tool": "calculate_estate_tax",
     "asks": ["The gross estate, deductions, and lifetime taxable gifts. If gifts are "
              "unknown, say the answer is provisional rather than passing zero.",
              "Whether a deceased spouse's unused exclusion is available, and that it "
              "is taken as given because portability needs a timely first return."],
     "refuses": ["Valuing anything, deciding what is in the gross estate, and every "
                 "state estate or inheritance tax."]},

    {"agent": "estate-planning", "skill": "gift-planning", "server": "taxagent-estate",
     "keywords": ["gift", "gifts", "gifting", "annual exclusion", "give", "giving",
                  "gift splitting", "grandchildren", "to their children",
                  "to my children"],
     "tool": "calculate_gift_exclusion",
     "asks": ["How many recipients, and whether the spouses intend to split — which "
              "requires consent on a return and is not implied by being married.",
              "Whether the recipient is a non-citizen spouse, which uses a different "
              "figure entirely."],
     "refuses": ["Whether a transfer into a trust is a present interest, which decides "
                 "if the annual exclusion applies at all."]},

    {"agent": "international-tax", "skill": "expat-review",
     "server": "taxagent-international",
     "keywords": ["abroad", "expat", "expatriate", "foreign earned", "overseas", "feie",
                  "foreign gift", "form 3520", "housing exclusion", "moved to",
                  "relocated", "working overseas", "foreign income", "non-resident"],
     "tool": "calculate_feie",
     "asks": ["Which qualifying test applies and how many days, because the answer is "
              "prorated and a day count that is off by one changes it.",
              "Whether the foreign tax credit is the better route — which this "
              "deployment cannot compute, and which cannot be combined with the "
              "exclusion on the same income."],
     "refuses": ["Establishing that the taxpayer qualifies at all; the foreign tax "
                 "credit; GILTI, subpart F, treaties and FBAR."]},

    {"agent": "business-entity", "skill": "entity-review", "server": "taxagent-business",
     "keywords": ["s corp", "s corporation", "c corp", "c corporation", "llc",
                  "partnership", "entity", "section 179", "179", "equipment",
                  "depreciation", "incorporate", "business income"],
     "tool": "calculate_section_179",
     "asks": ["The owner's actual marginal rate rather than the top one, and what "
              "fraction of profit is really distributed — defaulting to all of it "
              "quietly favours the pass-through.",
              "Whether the question is the dollar limit or the business income limit, "
              "because only one of them has a timing answer."],
     "refuses": ["Choosing an entity. Self-employment tax, reasonable compensation, "
                 "state tax and qualified small business stock are all excluded and "
                 "any of them can reverse the comparison."]},

    {"agent": "filing-compliance", "skill": "penalty-review",
     "server": "taxagent-compliance",
     "keywords": ["late", "penalty", "penalties", "missed the deadline", "extension",
                  "estimated tax", "safe harbor", "safe harbour", "underpayment",
                  "quarterly", "didn't file", "did not file", "months late",
                  "return is late", "overdue"],
     "tool": "calculate_filing_penalty",
     "asks": ["Whether the return was filed but unpaid, or not filed at all — the "
              "difference is a factor of ten.",
              "Whether reasonable cause or first-time abatement might apply, since "
              "either can remove the penalty and neither is computed."],
     "refuses": ["Interest. The rate is set quarterly and compounds daily, so the "
                 "figure returned is always lower than what is owed."]},

    {"agent": "portfolio-tax", "skill": "rebalance-review",
     "server": "taxagent-portfolio",
     "keywords": ["rebalance", "rebalancing", "drift", "drifted", "target weights",
                  "model weights", "back to target", "overweight", "underweight",
                  "60/40", "allocation"],
     "tool": "review_rebalance",
     "asks": ["The target weights, which must be supplied: a target inferred from the "
              "current holdings would make the portfolio its own benchmark.",
              "The tolerance band, because inside it the right answer is to leave the "
              "portfolio alone."],
     "refuses": ["Transaction costs, market impact and tracking error. The engine "
                 "never sells a security below its target, so with chunky lots it "
                 "under-trades and reports the residual drift."]},

    {"agent": "portfolio-tax", "skill": "gain-loss-review",
     "server": "taxagent-portfolio",
     "keywords": ["where do we stand", "unrealized", "unrealised", "embedded gain",
                  "gain and loss", "position report", "what are we holding"],
     "tool": "review_lots",
     "asks": ["Whether this is a review or a proposal. `review_lots` proposes "
              "nothing, which is the point of asking it first.",
              "The prices to value the position at, and the date they are as of."],
     "refuses": ["Recommending anything. A lot review reports what is held and what "
                 "it carries, and names the lots a screen could not clear."]},

    {"agent": "portfolio-tax", "skill": "tax-intake", "server": "taxagent-portfolio",
     "keywords": ["new export", "custodian export", "unknown basis", "missing basis",
                  "no cost basis", "data quality", "intake", "uploaded"],
     "tool": "check_holdings",
     "asks": ["Whether anyone has basis evidence for the blocked lots, because that "
              "is the only thing that unblocks them.",
              "Whether the export covers every account in the tax unit, since a "
              "wash-sale screen cannot clear an account it has not read."],
     "refuses": ["Treating an unknown basis as zero. A lot that cannot be priced for "
                 "tax is named and excluded, never guessed at."]},

    {"agent": "household-coordination", "skill": "household-coordination",
     "server": "taxagent-household",
     "keywords": ["jointly", "joint optimum", "optimize across", "optimise across",
                  "best across", "all at once", "whole family at once"],
     "tool": "optimize_household",
     "asks": ["How many tax units the household spans, because each one gets its own "
              "budget and they are never pooled.",
              "Whether a proof matters here: the joint optimizer proves optimality "
              "where its search exhausts, the sequential coordinator never does."],
     "refuses": ["Deciding which account should hold which exposure. That is an "
                 "allocation decision this engine does not make."]},

    {"agent": "municipal-bonds", "skill": "muni-review", "server": "taxagent-muni",
     "keywords": ["muni", "munis", "municipal", "municipal bond", "tax-exempt",
                  "tax exempt", "taxable equivalent", "equivalent yield", "de minimis",
                  "market discount", "bond premium", "private activity"],
     "tool": "calculate_muni_yield",
     "asks": ["The holder's state, their state rate, and whether the bond is exempt "
              "in that state: omitting it answers the out-of-state question instead.",
              "Whether the bond is a private activity bond and whether the holder is "
              "in AMT, because together they reverse the answer rather than shade it.",
              "Whether the question is the yield comparison, the de minimis discount "
              "test or the premium \u2014 three different calculations."],
     "refuses": ["Deciding whether a bond is a private activity bond, what any state "
                 "charges, and credit, call, duration or liquidity risk. Munis are "
                 "also outside every portfolio engine, so a muni lot cannot be "
                 "harvested, screened or rebalanced."]},

    {"agent": "state-and-local", "skill": "salt-review", "server": "taxagent-salt",
     "keywords": ["salt", "state and local", "state tax", "california", "new york",
                  "new jersey", "property tax", "itemize", "itemise",
                  "state income tax", "what does my state"],
     "tool": "calculate_salt_cap",
     "asks": ["Whether the return itemizes at all — for many taxpayers the standard "
              "deduction wins and the cap never bites.",
              "Whether the user means the federal deduction for state tax, or what "
              "their state actually charges. Only the first is computed here."],
     "refuses": ["Any state's own income tax: no state pack is bound. Residency, "
                 "part-year allocation and reciprocity are not modelled."]},
]

MONEY = re.compile(r"\$\s?([\d,]+(?:\.\d{1,2})?)\s*(k|m|million|thousand)?", re.I)


def extract_money(message: str) -> List[str]:
    """Dollar figures in the message, normalised to decimal strings.

    Shown to make the point that a figure a model lifts out of a sentence is
    the model's reading of it, not a fact. The console displays what it found
    so a reviewer can see whether the reading was right.
    """
    found: List[str] = []
    for raw, suffix in MONEY.findall(message):
        value = raw.replace(",", "")
        if suffix:
            lowered = suffix.lower()
            if lowered in {"k", "thousand"}:
                value = str(int(float(value) * 1_000))
            elif lowered in {"m", "million"}:
                value = str(int(float(value) * 1_000_000))
        found.append(value)
    return found


def _score(message: str, keywords: Sequence[str]) -> List[str]:
    """Keywords present as whole words or phrases, not as substrings.

    Plain substring matching sent "a $20 million estate" to the state-and-local
    agent, because "state" is inside "estate". Word boundaries fix that class
    of error; they do not make this a good router, and it is not claimed to be.
    """
    lowered = message.lower()
    return [k for k in keywords
            if re.search(r"(?<!\w)" + re.escape(k) + r"(?!\w)", lowered)]


def route(message: str) -> List[Route]:
    """Rank the agents that could own this question.

    A keyword matcher, stated as one. The ranking is not the interesting part;
    the steps each route would involve are.
    """
    scored = []
    for intent in INTENTS:
        matched = _score(message, intent["keywords"])
        if matched:
            scored.append((len(matched), max(len(m) for m in matched), intent, matched))
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)

    routes: List[Route] = []
    names = [i["agent"] for _, _, i, _ in scored]
    # Two agents matching equally well is not a tie to break silently. It is
    # the case where a model should ask, and the console should show that.
    contested = (len(scored) > 1 and scored[0][0] == scored[1][0]
                 and scored[0][2]["agent"] != scored[1][2]["agent"])

    for index, (count, _, intent, matched) in enumerate(scored[:3]):
        if contested and index == 0:
            confidence = "ambiguous"
        elif count >= 2:
            confidence = "clear"
        else:
            confidence = "weak"
        others = [n for n in names if n != intent["agent"]][:2]
        routes.append(Route(
            agent=intent["agent"], skill=intent["skill"] or None,
            confidence=confidence, matched=matched,
            steps=_steps(message, intent, confidence, others),
            alternatives=others))
    return routes


def _steps(message: str, intent: Dict[str, Any], confidence: str = "clear",
           alternatives: Sequence[str] = ()) -> List[Step]:
    steps: List[Step] = [
        Step(AGENTIC, f"Route to the {intent['agent']} agent.",
             why="A keyword matcher did this here. In a deployment the host's model "
                 "reads each agent's description and decides, and may ask before "
                 "choosing."),
    ]
    if confidence != "clear":
        if alternatives:
            summary = (f"Ask which is meant before computing: {intent['agent']} or "
                       f"{' or '.join(alternatives)}?")
        else:
            # Nothing else matched, so there is no choice to put to the user —
            # but one weak signal is still a thin basis for picking an engine.
            summary = (f"Confirm this really is a {intent['agent']} question before "
                       "computing; only one weak signal picked it.")
        steps.append(Step(
            AGENTIC, summary,
            why="The routing here is "
                + ("contested — more than one agent matched equally well."
                   if confidence == "ambiguous"
                   else "thin: one weak signal picked it.")
                + " Running the wrong engine produces a correct number to the wrong "
                  "question, which is harder to catch than an error."))
    for ask in intent["asks"]:
        steps.append(Step(AGENTIC, ask,
                          why="Settled before any calculation. Getting it wrong makes "
                              "the arithmetic that follows exactly right and entirely "
                              "useless."))
    figures = extract_money(message)
    if figures:
        steps.append(Step(
            AGENTIC, f"Read these figures out of the question: {', '.join(figures)}.",
            why="A figure lifted from a sentence is a reading, not a fact. It is shown "
                "so a reviewer can check the reading before trusting the result."))
    steps.append(Step(
        ORACLE, f"Call {intent['tool']}.", server=intent["server"], tool=intent["tool"],
        why="From here the answer is deterministic: a reviewed rule pack, a cited "
            "authority, and the same output for the same inputs every time."))
    steps.append(Step(
        AGENTIC, "State the result, its limits, and what was not computed.",
        why="The engine returns figures and limitations. Which of them matter to this "
            "person, and how to say them without overclaiming, is judgment."))
    for refusal in intent["refuses"]:
        steps.append(Step(REFUSED, refusal,
                          why="Named rather than approximated. A plausible number here "
                              "would be indistinguishable from a verified one."))
    return steps


def explain() -> Dict[str, Any]:
    """What the three labels mean, for the console to show alongside a trace."""
    return {
        "kinds": [{"kind": k, "meaning": v} for k, v in KIND_MEANING.items()],
        "agents": sorted({i["agent"] for i in INTENTS}),
        "note": ("This router is a keyword matcher, not an agent. It shows which agent "
                 "would own a question and which of its steps are deterministic. The "
                 "deterministic steps are really executed against the MCP servers; the "
                 "judgment steps are described and not performed."),
    }


# Grouped the way the use-case inventory is grouped: by what the client is
# trying to achieve. `agent` is the route the question must reach, which
# test_chat.py enforces — an example that stops routing is how the console
# starts looking broken.
EXAMPLES: List[Dict[str, str]] = [
    {"group": "Harvest losses", "agent": "portfolio-tax",
     "question": "Can we harvest losses in the Schwab account to raise $10,000 "
                 "without a wash sale?"},
    {"group": "Harvest losses", "agent": "portfolio-tax",
     "question": "The manager plans to buy VTI back next week. Does that break the "
                 "harvest?"},
    {"group": "Raise cash", "agent": "portfolio-tax",
     "question": "We need to raise cash of $250,000 by month end. What should we "
                 "sell?"},
    {"group": "Raise cash", "agent": "portfolio-tax",
     "question": "Raise cash of $400,000 but keep gains under $20,000."},
    {"group": "Cap realized gains", "agent": "portfolio-tax",
     "question": "Can we still harvest after $40,000 of realized gains elsewhere?"},
    {"group": "Rebalance", "agent": "portfolio-tax",
     "question": "Rebalance the account to 60/40 without realizing more than $5,000 "
                 "of gains."},
    {"group": "Rebalance", "agent": "portfolio-tax",
     "question": "The equity sleeve is 8% overweight. Fix it without a tax bill."},
    {"group": "Where do we stand", "agent": "portfolio-tax",
     "question": "Where do we stand on unrealized gains in the joint account?"},
    {"group": "Data quality", "agent": "portfolio-tax",
     "question": "Here is a new export from the custodian. What in it cannot be "
                 "used?"},
    {"group": "Data quality", "agent": "portfolio-tax",
     "question": "Three lots came over with unknown basis. Can we still act?"},
    {"group": "Coordinate a household", "agent": "household-coordination",
     "question": "The husband and wife both have taxable accounts. Coordinate the "
                 "harvest across the household."},
    {"group": "Coordinate a household", "agent": "household-coordination",
     "question": "Optimize jointly rather than one account at a time."},
    {"group": "Municipal bonds", "agent": "municipal-bonds",
     "question": "A 3.5% California muni for a top-bracket resident. What would a "
                 "taxable bond have to pay?"},
    {"group": "Municipal bonds", "agent": "municipal-bonds",
     "question": "They bought a muni at 98 that matures in two years. Is the "
                 "discount still tax-exempt?"},
    {"group": "Municipal bonds", "agent": "municipal-bonds",
     "question": "Is a private activity bond worth holding for someone in AMT?"},
    {"group": "Know what a gain costs", "agent": "federal-estimator",
     "question": "My client has $185,000 of income and a $50,000 long-term gain. "
                 "What do they owe?"},
    {"group": "Know what a gain costs", "agent": "federal-estimator",
     "question": "Does the alternative minimum tax reprice their long-term gain?"},
    {"group": "Transfer wealth", "agent": "estate-planning",
     "question": "A $20 million estate, $2 million of lifetime gifts. What is the "
                 "estate tax?"},
    {"group": "Transfer wealth", "agent": "estate-planning",
     "question": "Two parents want to give $100,000 to their three children this "
                 "year."},
    {"group": "Business owner", "agent": "business-entity",
     "question": "Should the business be an S corp or a C corp at $1 million of "
                 "profit?"},
    {"group": "Business owner", "agent": "business-entity",
     "question": "They want to buy $4.5 million of equipment this year."},
    {"group": "Expat and cross-border", "agent": "international-tax",
     "question": "My client moved to Portugal in July and earned $180,000. What can "
                 "they exclude?"},
    {"group": "Expat and cross-border", "agent": "international-tax",
     "question": "They received $150,000 from a relative abroad. Is it reportable?"},
    {"group": "Fix a filing problem", "agent": "filing-compliance",
     "question": "The partnership return is four months late and there are eight "
                 "partners."},
    {"group": "Fix a filing problem", "agent": "filing-compliance",
     "question": "Income jumped this year. What estimated tax must they pay in?"},
    {"group": "What is deductible", "agent": "state-and-local",
     "question": "They paid $60,000 of state tax on $550,000 of income. How much is "
                 "deductible?"},
    {"group": "What is deductible", "agent": "state-and-local",
     "question": "What does California charge on a $400,000 capital gain?"},
]


def example_groups() -> List[Dict[str, Any]]:
    """The examples the console offers, in objective order.

    One flat row of chips hid how much of the surface is reachable. Grouping
    them by objective is the same grouping the use-case inventory uses, so the
    console and the inventory can be read against each other.
    """
    order: List[str] = []
    for example in EXAMPLES:
        if example["group"] not in order:
            order.append(example["group"])
    return [{"group": group,
             "questions": [e["question"] for e in EXAMPLES if e["group"] == group]}
            for group in order]


def examples() -> List[str]:
    return [example["question"] for example in EXAMPLES]
