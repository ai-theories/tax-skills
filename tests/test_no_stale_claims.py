"""Prose must not contradict the registry.

Every stale claim found in this project was a sentence in a doc, a skill or a
server's own instructions that had been true once. None of them broke a test,
because nothing compared words against the capability registry. An agent reads
those words and acts on them, so a stale one is a live defect: the household
server told every agent "no validated joint optimizer exists" for as long as
one did exist.
"""
import os
import re

import pytest
import yaml

from taxagent.application.bootstrap import PROJECT_ROOT
from taxagent.gateway.capability_registry import CapabilityRegistry

REGISTRY = CapabilityRegistry.load(os.path.join(PROJECT_ROOT, "capabilities"))

#: Files an agent or a reader takes as current. Design documents are excluded
#: only when they carry a dated status banner saying what has changed since.
PROSE_ROOTS = ("skills", "agents", "references", "docs")

#: Phrases that assert an engine is absent, paired with the engine they are
#: about. A file saying one of these while the registry says `implemented` is
#: the exact failure this guards.
ABSENCE_CLAIMS = [
    (re.compile(r"no validated joint (household )?optimizer exists", re.I),
     "household-optimizer"),
    (re.compile(r"no validated rebalancing engine is bound", re.I),
     "rebalance-engine"),
    (re.compile(r"no rebalancing engine (exists|is bound)", re.I), "rebalance-engine"),
    (re.compile(r"oracle-account-adapter.{0,40}not[_ ]configured", re.I),
     "oracle-account-adapter"),
]


def _prose_files():
    found = []
    for root in PROSE_ROOTS:
        base = os.path.join(PROJECT_ROOT, root)
        if not os.path.isdir(base):
            continue
        for directory, _, files in os.walk(base):
            for name in files:
                if name.endswith(".md"):
                    found.append(os.path.join(directory, name))
    return sorted(found)


def _has_status_banner(text):
    """A dated banner at the top saying what changed is an accepted answer."""
    return bool(re.search(r"^>\s*\*\*Status, \d{4}-\d{2}-\d{2}", text, re.M))


@pytest.mark.parametrize("path", _prose_files(),
                         ids=lambda p: os.path.relpath(p, PROJECT_ROOT))
def test_no_file_claims_an_engine_is_missing_that_ships(path):
    text = open(path, encoding="utf-8").read()
    if _has_status_banner(text):
        return
    for pattern, engine_id in ABSENCE_CLAIMS:
        match = pattern.search(text)
        if not match:
            continue
        engine = REGISTRY.engine(engine_id)
        assert engine is None or not engine.is_available, (
            f"says {match.group(0)!r} but {engine_id} is {engine.status} "
            "in capabilities/engines.yaml")


def test_server_instructions_do_not_claim_a_missing_engine():
    """What an agent reads first, before any tool call."""
    from taxagent.gateway.server_registry import SERVERS

    for key, spec in SERVERS.items():
        for pattern, engine_id in ABSENCE_CLAIMS:
            match = pattern.search(spec.instructions)
            if not match:
                continue
            engine = REGISTRY.engine(engine_id)
            assert engine is None or not engine.is_available, (
                f"{key} instructions say {match.group(0)!r} but {engine_id} ships")


def test_tool_descriptions_do_not_claim_a_missing_engine():
    from taxagent.gateway.calculator_dispatcher import BOUNDARIES, CalculatorDispatcher
    from taxagent.gateway.domain_dispatchers import (HouseholdDispatcher,
                                                     PortfolioDispatcher)

    tools = []
    for key in BOUNDARIES:
        tools += CalculatorDispatcher(key).list_tools()
    for cls in (PortfolioDispatcher, HouseholdDispatcher):
        tools += [{"name": n, "description": t["description"]}
                  for n, t in cls.tools.items()]
        tools.append({"name": cls.capability_tool,
                      "description": cls.capability_description})

    for tool in tools:
        for pattern, engine_id in ABSENCE_CLAIMS:
            match = pattern.search(tool["description"])
            if not match:
                continue
            engine = REGISTRY.engine(engine_id)
            assert engine is None or not engine.is_available, (
                f"{tool['name']} says {match.group(0)!r} but {engine_id} ships")


def test_console_use_cases_do_not_claim_a_missing_engine():
    from taxagent.console import use_cases

    for case in use_cases.USE_CASES:
        blob = f"{case['title']} {case['shows']}"
        for pattern, engine_id in ABSENCE_CLAIMS:
            match = pattern.search(blob)
            if not match:
                continue
            engine = REGISTRY.engine(engine_id)
            assert engine is None or not engine.is_available, (
                f"{case['id']} says {match.group(0)!r} but {engine_id} ships")


def test_planned_skills_are_not_quietly_shipped():
    """The planned list must stay a list of things that are not here."""
    path = os.path.join(PROJECT_ROOT, "planned-skills", "README.md")
    if not os.path.exists(path):
        pytest.skip("no planned-skills README")
    planned = set(re.findall(r"`([a-z][a-z0-9-]+)`\s*\|", open(path, encoding="utf-8").read()))
    shipped = {name for name in os.listdir(os.path.join(PROJECT_ROOT, "skills"))
               if os.path.isdir(os.path.join(PROJECT_ROOT, "skills", name))}
    overlap = planned & shipped
    assert not overlap, f"listed as planned but shipped: {sorted(overlap)}"


def test_no_empty_package_ships():
    """An empty package implies a capability that has no code behind it."""
    base = os.path.join(PROJECT_ROOT, "src", "taxagent")
    empty = []
    for directory, _, files in os.walk(base):
        if "__pycache__" in directory:
            continue
        modules = [f for f in files if f.endswith(".py")]
        if not modules:
            continue
        body = ""
        for name in modules:
            with open(os.path.join(directory, name), encoding="utf-8") as handle:
                body += "".join(line for line in handle
                                if line.strip() and not line.strip().startswith("#"))
        if not body.strip():
            empty.append(os.path.relpath(directory, PROJECT_ROOT))
    assert not empty, f"packages with no code: {empty}"


def test_every_reference_file_is_reachable():
    """A reference nothing points to is a document nobody will read.

    These three shipped beside the code with no skill or agent naming them, so
    an agent following its instructions would never open them. They are
    guidance an agent needs before it promises a capability or defaults a
    financial choice, which makes being unreachable a real cost.
    """
    base = os.path.join(PROJECT_ROOT, "references")
    if not os.path.isdir(base):
        pytest.skip("no references directory")

    body = ""
    for root in ("skills", "agents", "docs", "capabilities"):
        directory = os.path.join(PROJECT_ROOT, root)
        for folder, _, files in os.walk(directory):
            for name in files:
                if name.endswith((".md", ".yaml")):
                    with open(os.path.join(folder, name), encoding="utf-8") as handle:
                        body += handle.read()

    orphans = [name for name in sorted(os.listdir(base))
               if name.endswith(".md") and name not in body]
    assert not orphans, f"reference files nothing points to: {orphans}"


def test_every_reference_a_skill_names_exists():
    """The other direction: a named reference that is not there.

    A citation resolves either beside the file that makes it — a reference
    specific to one skill — or at the plugin root, where guidance shared by
    every skill lives. Both are legitimate, and requiring the root alone
    reported a correct per-skill reference as missing.
    """
    pattern = re.compile(r"`references/([a-z0-9-]+\.md)`")
    shared = os.path.join(PROJECT_ROOT, "references")
    at_root = set(os.listdir(shared)) if os.path.isdir(shared) else set()
    for root in ("skills", "agents"):
        directory = os.path.join(PROJECT_ROOT, root)
        for folder, _, files in os.walk(directory):
            for name in files:
                if not name.endswith(".md"):
                    continue
                path = os.path.join(folder, name)
                for named in pattern.findall(open(path, encoding="utf-8").read()):
                    beside = os.path.join(folder, "references", named)
                    assert named in at_root or os.path.exists(beside), (
                        f"{os.path.relpath(path, PROJECT_ROOT)} names "
                        f"references/{named}, which resolves neither beside it "
                        "nor at the plugin root")


# --- the generated site ---------------------------------------------------
# The guard above reads .md files. The public page is not a .md file: it is
# assembled by scripts/build_site.py, whose refusal list and FAQ are typed by
# hand. That page told the open internet "no validated joint optimizer exists"
# for as long as one shipped, and claimed 2024-2026 coverage when every pack
# covers 2026 alone. Scanning the rendered output covers any future prose the
# generator grows, which scanning its source would not.

def _rendered_site() -> str:
    import sys
    scripts = os.path.join(PROJECT_ROOT, "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import build_site
    return build_site.build_index() + build_site.build_llms_txt()


@pytest.mark.parametrize("pattern,engine", ABSENCE_CLAIMS,
                         ids=[e for _, e in ABSENCE_CLAIMS])
def test_generated_site_never_says_a_shipped_engine_is_missing(pattern, engine):
    entry = REGISTRY.engine(engine)
    if entry is None or entry.status != "implemented":
        pytest.skip(f"{engine} does not ship, so the claim is true")
    found = pattern.search(_rendered_site())
    assert not found, (
        f"the published page says {engine} is missing: {found.group(0)!r}")


def test_generated_site_claims_only_the_tax_years_the_registry_covers():
    years = sorted({y for e in REGISTRY.engines for y in (e.tax_years or [])})
    page = _rendered_site()
    for year in range(2015, 2031):
        if year in years:
            continue
        assert f"tax for {year}" not in page, year
        assert f"-{year}:" not in page, year
