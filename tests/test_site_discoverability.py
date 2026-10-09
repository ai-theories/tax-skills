"""The site must be findable, quotable and shareable.

Every signal here was absent until 2026-10-08, and each absence is silent: a
page with no structured data still renders, a social card pointing at an SVG
still validates as HTML, and nothing fails when an answer engine cannot tell
what the page is about. These assert the signals exist and agree with what the
page actually says.
"""
import json
import os
import re
import struct
import sys

import pytest

from taxagent.application.bootstrap import PROJECT_ROOT

sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))
import build_site  # noqa: E402

PAGES = {"index.html": build_site.SITE_URL,
         "cases.html": f"{build_site.SITE_URL}cases.html"}


@pytest.fixture(scope="module")
def rendered():
    return {"index.html": build_site.anchor_headings(build_site.build_index()),
            "cases.html": build_site.build_cases()}


def _graph(page):
    blocks = re.findall(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', page, re.S)
    assert blocks, "no structured data on the page"
    return [item for block in blocks for item in json.loads(block)["@graph"]]


# --- SEO ------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(PAGES))
def test_every_page_declares_its_canonical_url(rendered, name):
    assert f'<link rel="canonical" href="{PAGES[name]}">' in rendered[name]


@pytest.mark.parametrize("name", sorted(PAGES))
def test_every_page_has_one_title_and_a_description(rendered, name):
    page = rendered[name]
    assert page.count("<title>") == 1
    description = re.search(r'<meta name="description" content="([^"]+)"', page)
    assert description and 70 <= len(description.group(1)) <= 320


@pytest.mark.parametrize("name", sorted(PAGES))
def test_social_card_is_a_png_with_declared_dimensions(rendered, name):
    """An SVG og:image renders nowhere that matters, so the share is blank."""
    page = rendered[name]
    assert f'content="{build_site.SITE_URL}og-image.png"' in page
    assert 'property="og:image:width" content="1200"' in page
    assert 'property="og:image:height" content="630"' in page
    assert 'name="twitter:card" content="summary_large_image"' in page
    assert "og-image.svg" not in page


def test_the_social_card_file_is_a_real_png_at_the_declared_size():
    path = os.path.join(PROJECT_ROOT, "assets", "og-image.png")
    blob = open(path, "rb").read()
    assert blob[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    width, height = struct.unpack(">II", blob[16:24])
    assert (width, height) == (1200, 630), (width, height)


# --- AEO ------------------------------------------------------------------

def test_the_faq_is_published_as_structured_data(rendered):
    """Eight questions in the page and none in the markup helps no one."""
    faq = next(i for i in _graph(rendered["index.html"]) if i["@type"] == "FAQPage")
    assert len(faq["mainEntity"]) == len(build_site.FAQ)
    for entry, (question, answer) in zip(faq["mainEntity"], build_site.FAQ):
        assert entry["name"] == question
        assert entry["acceptedAnswer"]["text"] == answer


def test_the_software_schema_matches_the_plugin_manifest(rendered):
    manifest = json.load(open(os.path.join(PROJECT_ROOT, ".claude-plugin", "plugin.json"),
                              encoding="utf-8"))
    software = next(i for i in _graph(rendered["index.html"])
                    if i["@type"] == "SoftwareApplication")
    assert software["softwareVersion"] == build_site.__version__
    assert software["codeRepository"] == build_site.REPO_URL
    assert software["description"] == " ".join(manifest["description"].split())


def test_the_case_inventory_is_described_as_a_dataset(rendered):
    graph = _graph(rendered["cases.html"])
    dataset = next(i for i in graph if i["@type"] == "Dataset")
    report = build_site.inventory()
    assert len(dataset["variableMeasured"]) == len(report["groups"])
    assert sum(v["value"] for v in dataset["variableMeasured"]) == report["total"]
    assert any(i["@type"] == "BreadcrumbList" for i in graph)


@pytest.mark.parametrize("name", sorted(PAGES))
def test_every_section_heading_can_be_linked_to(rendered, name):
    """A page that can only be cited whole gets cited badly."""
    headings = re.findall(r"<h2([^>]*)>", rendered[name])
    assert headings
    assert all("id=" in attributes for attributes in headings)


# --- GEO and AIO ----------------------------------------------------------

def test_robots_names_the_answer_engines_and_points_at_llms_txt():
    robots = build_site.build_robots()
    for agent in build_site.ANSWER_ENGINES:
        assert f"User-agent: {agent}\nAllow: /" in robots, agent
    assert f"Sitemap: {build_site.SITE_URL}sitemap.xml" in robots
    assert f"{build_site.SITE_URL}llms.txt" in robots


@pytest.mark.parametrize("name", sorted(PAGES))
def test_every_page_points_language_models_at_the_plain_text(rendered, name):
    page = rendered[name]
    assert f'href="{build_site.SITE_URL}llms.txt"' in page
    assert f'href="{build_site.SITE_URL}llms-full.txt"' in page


@pytest.mark.parametrize("name", sorted(PAGES))
def test_snippets_are_not_truncated_by_our_own_directive(rendered, name):
    assert "max-snippet:-1" in rendered[name]
    assert "max-image-preview:large" in rendered[name]


# --- SXO ------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(PAGES))
def test_keyboard_users_can_skip_the_header(rendered, name):
    page = rendered[name]
    assert '<a class="skip" href="#content">' in page
    assert 'id="content"' in page and "</main>" in page


def test_sitemap_lists_every_html_page_that_is_built(tmp_path):
    written = build_site.build_site(str(tmp_path))
    sitemap = open(os.path.join(tmp_path, "sitemap.xml"), encoding="utf-8").read()
    for name in written:
        if name.endswith(".html") and name != "404.html":
            expected = build_site.SITE_URL if name == "index.html" else \
                f"{build_site.SITE_URL}{name}"
            assert f"<loc>{expected}</loc>" in sitemap, name
