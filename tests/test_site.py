"""The published site is generated from the registries and cannot over-claim."""
import os
import re
import sys

import pytest
import yaml

from taxagent import __version__
from taxagent.application.bootstrap import PROJECT_ROOT

sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))
import build_site  # noqa: E402


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("site")
    build_site.build_site(str(out))
    return {name: (out / name).read_text(encoding="utf-8")
            for name in os.listdir(out)}


@pytest.fixture(scope="module")
def engines():
    return yaml.safe_load(
        open(os.path.join(PROJECT_ROOT, "capabilities", "engines.yaml"),
             encoding="utf-8"))["engines"]


@pytest.fixture(scope="module")
def tools():
    return yaml.safe_load(
        open(os.path.join(PROJECT_ROOT, "capabilities", "tools.yaml"),
             encoding="utf-8"))["tools"]


def test_expected_files_are_written(site):
    assert {"index.html", "404.html", "llms.txt", "llms-full.txt",
            "robots.txt", "sitemap.xml", "og-image.svg"} <= set(site)


def test_every_skill_appears(site):
    for skill in build_site.skills():
        assert skill["name"] in site["index.html"]
        assert skill["name"] in site["llms.txt"]


def test_no_unavailable_engine_is_shown_as_implemented(site, engines):
    page = site["index.html"]
    for engine in engines:
        # Each engine is rendered with its real status, never omitted and never
        # upgraded: a marketing page that outruns the registry is the drift this
        # whole project is built to prevent.
        row = re.search(
            rf"<code>{re.escape(engine['engine_id'])}</code></td>\s*<td>(.*?)</td>",
            page, re.DOTALL)
        assert row, f"{engine['engine_id']} is missing from the page"
        assert engine["status"] in row.group(1)


def test_unavailable_engines_are_listed_not_hidden(site, engines):
    unavailable = [e["engine_id"] for e in engines if e["status"] != "implemented"]
    assert unavailable, "fixture expects at least one unavailable engine"
    for engine_id in unavailable:
        assert engine_id in site["index.html"]
        assert engine_id in site["llms.txt"]


def test_llms_txt_lists_exactly_the_callable_tools(site, tools):
    callable_tools = {t["name"] for t in tools if t["status"] == "implemented"}
    internal = {t["name"] for t in tools if t["status"] == "internal_operation"}
    body = site["llms.txt"]
    section = body.split("## Tools a host can call")[1].split("##")[0]
    assert set(re.findall(r"`(\w+)`", section)) == callable_tools
    # Internal operations get their own section, explicitly marked not callable.
    internal_section = body.split("## Internal workflow operations")[1].split("##")[0]
    assert "not callable as tools" in internal_section
    assert set(re.findall(r"`(\w+)`", internal_section)) == internal


def test_no_execution_tool_is_advertised(site, tools):
    forbidden = {"execute_trade", "file_return", "place_order"}
    # Named only where the page says they do not exist, never in a tool list.
    listed = {t["name"] for t in tools}
    assert not listed & forbidden

    section = site["llms.txt"].split("## Tools a host can call")[1].split("##")[0]
    assert not forbidden & set(re.findall(r"`(\w+)`", section))

    tool_paragraph = site["index.html"].split("<h2>Tool surface</h2>")[1].split("<h2>")[0]
    assert not any(name in tool_paragraph for name in forbidden)


def test_capabilities_lead_and_limits_still_appear(site):
    page = site["index.html"]
    # Capabilities first: a page that opens with refusals reads as a list of
    # things that do not work, whatever the engineering behind it.
    assert page.index("What it does") < page.index("What it will not do")
    assert "Not tax, investment or legal advice" in page
    assert "analysis only" in page.lower()


def test_the_verified_source_is_cited(site):
    assert "rp-25-32" in site["index.html"]


def test_rule_bundle_is_named_with_its_authorities(site):
    from taxagent.decisions.rule_registry import default_rules
    rules = default_rules(2026)
    page = site["index.html"]
    assert rules.bundle_id in page
    assert rules.bundle_hash[:20] in page
    for authority in rules.authorities.values():
        assert authority.split(";")[0].strip() in page


def test_version_is_not_hardcoded(site):
    assert f"v{__version__}" in site["index.html"]
    assert "0.0.0" not in site["index.html"]


def test_full_docs_include_every_skill_body(site):
    body = site["llms-full.txt"]
    for skill in build_site.skills():
        assert f"# Skill: {skill['name']}" in body
