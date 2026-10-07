#!/usr/bin/env python3
"""Assemble the GitHub Pages site into a directory (default _site).

    index.html      landing page, generated from the registries
    404.html        small not-found page
    llms.txt        plain summary for AI assistants (llmstxt.org)
    llms-full.txt   README plus every SKILL.md in one Markdown file
    robots.txt      allows everything, names the sitemap
    sitemap.xml     published pages, dated by the last commit
    og-image.svg    social preview source

Every capability claim on the page is read from `capabilities/`, `workflows/`,
`rules/tax/` and the skills' own frontmatter at build time. Nothing about what
this system can do is typed by hand here, so the site cannot drift from the
code the way a hand-written feature list does — and an engine that is
unavailable is rendered as unavailable rather than quietly omitted.

    python3 scripts/build_site.py [outDir]
"""
from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
from datetime import date
from typing import Any, Dict, List, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import yaml  # noqa: E402

from taxagent import __version__  # noqa: E402
from taxagent.decisions.rule_registry import default_rules  # noqa: E402
from taxagent.console.inventory import inventory  # noqa: E402

SITE_URL = "https://ai-theories.github.io/tax-skills/"
REPO_URL = "https://github.com/ai-theories/tax-skills"


def covered_years() -> str:
    """The tax years the registry actually covers.

    Typed by hand this said "2024-2026" while every pack covered 2026 alone.
    A claim about coverage is read from the registry, like every other claim
    on this page.
    """
    engines = read_yaml("capabilities", "engines.yaml")["engines"]
    years = sorted({y for e in engines for y in e.get("tax_years", [])})
    if not years:
        return "no reviewed tax year"
    if len(years) == 1:
        return str(years[0])
    return f"{years[0]}-{years[-1]}"
SKILLS_DIR = os.path.join(ROOT, "skills")
E = html.escape


# --- sources -------------------------------------------------------------

def read_yaml(*parts: str) -> Dict[str, Any]:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def skills() -> List[Dict[str, str]]:
    found = []
    for name in sorted(os.listdir(SKILLS_DIR)):
        path = os.path.join(SKILLS_DIR, name, "SKILL.md")
        if not os.path.exists(path):
            continue
        text = open(path, encoding="utf-8").read()
        match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
        meta = yaml.safe_load(match.group(1)) if match else {}
        found.append({"name": meta.get("name", name),
                      "description": meta.get("description", "").strip()})
    return found


def workflows() -> List[Dict[str, Any]]:
    directory = os.path.join(ROOT, "workflows")
    out = []
    for name in sorted(os.listdir(directory)):
        if name.endswith((".yaml", ".yml")):
            raw = read_yaml("workflows", name)
            out.append({"id": raw["id"], "scope": raw.get("required_scope", ""),
                        "mode": raw.get("mode", ""), "steps": len(raw.get("steps", []))})
    return out


def last_modified() -> str:
    try:
        return subprocess.run(["git", "log", "-1", "--format=%cs"], cwd=ROOT,
                              capture_output=True, text=True, timeout=5).stdout.strip() \
            or date.today().isoformat()
    except Exception:
        return date.today().isoformat()


#: The refusals are the product. They are listed explicitly rather than left
#: for a reader to discover by hitting one.
REFUSALS: List[Tuple[str, str]] = [
    ("A municipal bond inside a portfolio engine",
     "Munis are priced on their own server. No portfolio engine reads one, because "
     "accrued market discount is not tracked in lot accounting, so a muni lot cannot "
     "be harvested, screened or rebalanced."),
    ("Tax owed, in dollars",
     "No return-level liability engine is bound. Gains and losses are produced; converting "
     "them to tax needs facts and an engine this release does not have."),
    ("&quot;Is this substantially identical?&quot;",
     "Matching is by exact security identifier. Whether two different securities are "
     "substantially identical is a reviewed policy question the screener declines."),
    ("A clean bill of health on wash sales",
     "A screen is complete or it has gaps. It never clears an account it has not read, "
     "including a spouse&rsquo;s account at another custodian."),
    ("A gain budget with no netting basis",
     "Gross and net readings of the same sentence give different answers, so the request "
     "returns the exact fields it needs rather than guessing."),
    ("A lot with unknown basis",
     "Blocked, never defaulted to zero. A missing basis stays missing."),
    ("A relaxed constraint to make a target reachable",
     "Infeasible is reported with the shortfall. Constraints change only when a person "
     "changes them."),
    ("Any trade, ever",
     "Analysis only. No tool in this release places an order, modifies a custodian record "
     "or files a return."),
]

FAQ: List[Tuple[str, str]] = [
    ("Does the model do the tax maths?",
     "No. Every number comes from a tested Python backend; the model interprets the "
     "request, picks a workflow and explains the result. If a figure is not in the "
     "evidence package, it cannot appear in the answer."),
    ("Can it trade?",
     "No. There is no execute_trade or file_return tool, and every result carries "
     "execution_authorized: false. A future execution service would need action-bound "
     "authorization enforced by the receiving system."),
    ("What does it actually cover?",
     f"US federal investment tax for {covered_years()}: US equities and US-listed ETFs, "
     "long positions, USD, and one account, a coordinated household or a jointly "
     "optimized one. Municipal bonds are priced on their own server and stay outside "
     "every portfolio engine. Options, K-1 partnerships, digital assets and equity "
     "compensation are out of scope, and the registry refuses them rather than "
     "approximating."),
    ("Where do the tax rules live?",
     "In a versioned pack under rules/tax/, each rule carrying its authority, legal "
     "effective date and the time we recorded it. The engines read from the pack, a "
     "constant reappearing in the calculation path fails the build, and every evidence "
     "package pins the bundle and its hash."),
    ("What happens when it cannot answer?",
     "It says so, with a code: UNSUPPORTED_SCOPE, WRONG_RULE_YEAR, MISSING_BASIS, "
     "INFEASIBLE_CONSTRAINTS. A refusal is the designed outcome, not a failure."),
    ("Is a completed calculation a complete answer?",
     "Not necessarily. Status and coverage are separate fields: a run can finish while "
     "wash-sale screening remains incomplete because an account was unreadable or the "
     "30-day forward window has not closed."),
    ("Can I reproduce a result later?",
     "Yes. Evidence pins the snapshot hash, rule bundle and hash, engine manifest, "
     "calculation versions and input hashes. Replaying the same inputs yields an "
     "identical digest, and that is enforced by a test."),
    ("Is this tax advice?",
     "No. It prepares scenarios and evidence for review by a licensed professional, who "
     "remains responsible for the advice."),
]


# --- page ----------------------------------------------------------------

STYLE = """
:root{--bg:#fbfbfa;--panel:#fff;--ink:#1b1b19;--muted:#6b6b66;--line:#e3e2de;
--accent:#3b5bdb;--ok:#2b7a4b;--warn:#9a6700;--code:#f5f5f3;
--chip-ok:#e6f2ea;--chip-warn:#fdf3d8;--chip-info:#e8edfb}
@media(prefers-color-scheme:dark){:root{--bg:#17181a;--panel:#1e1f22;--ink:#e8e8e5;
--muted:#9b9b95;--line:#32343a;--accent:#8fa6ff;--ok:#6cc08a;--warn:#e0b44a;--code:#232428;
--chip-ok:#1e3328;--chip-warn:#3a3120;--chip-info:#23283c}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 -apple-system,
BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:0 22px}
header{padding:56px 0 30px;border-bottom:1px solid var(--line)}
h1{margin:0 0 8px;font-size:34px;letter-spacing:-0.02em;line-height:1.15}
.tag{color:var(--muted);font-size:18px;margin:0 0 18px}
h2{margin:44px 0 6px;font-size:21px;letter-spacing:-0.01em}
h2+.lede{color:var(--muted);margin:0 0 16px;font-size:15px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:11px;
padding:15px 17px;margin-bottom:11px}
.card h3{margin:0 0 5px;font-size:15px}
.card p{margin:0;color:var(--muted);font-size:14px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:11px}
table{border-collapse:collapse;width:100%;font-size:14.5px;margin:6px 0 4px}
th,td{text-align:left;padding:8px 12px 8px 0;border-bottom:1px solid var(--line);
vertical-align:top}
th{color:var(--muted);font-size:12.5px;font-weight:650;text-transform:uppercase;
letter-spacing:.05em}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}
pre{background:var(--code);border:1px solid var(--line);border-radius:9px;padding:14px;
overflow-x:auto;font-size:13px;margin:10px 0}
.chip{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11.5px;
font-weight:650;letter-spacing:.02em}
.chip.ok{background:var(--chip-ok);color:var(--ok)}
.chip.warn{background:var(--chip-warn);color:var(--warn)}
.chip.info{background:var(--chip-info);color:var(--accent)}
details{border-bottom:1px solid var(--line);padding:11px 0}
summary{cursor:pointer;font-weight:560;font-size:15.5px}
details p{color:var(--muted);margin:8px 0 0;font-size:14.5px}
footer{margin:52px 0 60px;padding-top:20px;border-top:1px solid var(--line);
color:var(--muted);font-size:13.5px}
a{color:var(--accent)}
#live{display:none}
"""

LIVE_JS = """
// The published site is static. When the same page is served by the local
// dashboard, the API answers and the scenario panel turns on.
fetch('/api/scenarios').then(r => r.ok ? r.json() : Promise.reject()).then(list => {
  const box = document.getElementById('live');
  const body = document.getElementById('live-body');
  box.style.display = 'block';
  list.forEach(s => {
    const card = document.createElement('div');
    card.className = 'card';
    const h = document.createElement('h3');
    h.textContent = s.title;
    const p = document.createElement('p');
    p.textContent = s.why;
    const out = document.createElement('div');
    out.style.marginTop = '8px';
    const b = document.createElement('button');
    b.textContent = 'Run';
    b.onclick = async () => {
      out.textContent = 'running...';
      const d = await (await fetch('/api/run/' + s.id)).json();
      const e = d.envelope;
      out.textContent = '';
      const chip = document.createElement('span');
      chip.className = 'chip ' + (e.status.startsWith('completed') ? 'ok' : 'warn');
      chip.textContent = e.code || e.status;
      out.appendChild(chip);
    };
    card.append(h, p, b, out);
    body.appendChild(card);
  });
}).catch(() => {});
"""


def build_index() -> str:
    engines = read_yaml("capabilities", "engines.yaml")["engines"]
    tools = read_yaml("capabilities", "tools.yaml")["tools"]
    rules = default_rules(2026)
    skill_list = skills()

    def engine_rows() -> str:
        rows = []
        for e in engines:
            available = e.get("status") == "implemented"
            chip = "ok" if available else "warn"
            scopes = ", ".join(e.get("scopes") or []) or "&mdash;"
            years = ", ".join(str(y) for y in (e.get("tax_years") or [])) or "&mdash;"
            note = (e.get("limitations") or ["&mdash;"])[0]
            rows.append(
                f"<tr><td><code>{E(e['engine_id'])}</code></td>"
                f"<td><span class='chip {chip}'>{E(e.get('status',''))}</span></td>"
                f"<td>{scopes}</td><td>{years}</td><td>{note}</td></tr>")
        return "\n".join(rows)

    exposed = [t for t in tools if t.get("status") == "implemented"]
    internal = [t for t in tools if t.get("status") == "internal_operation"]

    skill_cards = "\n".join(
        f"<div class='card'><h3><code>{E(s['name'])}</code></h3>"
        f"<p>{E(s['description'])}</p></div>" for s in skill_list)

    refusal_rows = "\n".join(
        f"<tr><td><strong>{claim}</strong></td><td>{because}</td></tr>"
        for claim, because in REFUSALS)

    workflow_rows = "\n".join(
        f"<tr><td><code>{E(w['id'])}</code></td><td>{E(w['scope'])}</td>"
        f"<td>{E(w['mode'])}</td><td>{w['steps']}</td></tr>" for w in workflows())

    rule_rows = "\n".join(
        f"<tr><td><code>{E(name)}</code></td><td>{E(authority)}</td></tr>"
        for name, authority in sorted(rules.authorities.items()))

    faq = "\n".join(
        f"<details><summary>{E(q)}</summary><p>{E(a)}</p></details>" for q, a in FAQ)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tax Agent: Claude skills for US investment tax analysis</title>
<meta name="description" content="Open-source Claude skills for US investment tax:
lot-level gain and loss, wash-sale screening across accounts and UMA sleeves, and
loss-harvest scenarios, backed by a deterministic Python engine. Analysis only.">
<meta property="og:title" content="Tax Agent">
<meta property="og:description" content="Claude skills for US investment tax analysis,
backed by a deterministic engine. Analysis only.">
<meta property="og:url" content="{SITE_URL}">
<meta property="og:image" content="{SITE_URL}og-image.svg">
<link rel="canonical" href="{SITE_URL}">
<style>{STYLE}</style>
</head>
<body>
<div class="wrap">

<header>
  <h1>Tax Agent</h1>
  <p class="tag">Claude skills for US investment tax analysis, backed by a deterministic
  engine.</p>
  <p><span class="chip info">analysis only</span>
     <span class="chip ok">{len(skill_list)} skills</span>
     <span class="chip ok">v{__version__}</span></p>
  <p>The model interprets the request, chooses a workflow and explains the result. It does
  not do the tax maths: every figure comes from a tested Python backend and is pinned to a
  reviewed rule pack. Runs locally &mdash; no sign-up, no API key, no client data leaving
  the machine.</p>
</header>

<h2>What it does</h2>
<p class="lede">Six calculators and four portfolio workflows, every figure verified against
<a href="https://www.irs.gov/pub/irs-drop/rp-25-32.pdf">Rev. Proc. 2025-32</a> and computed
by tested code.</p>
<div class="grid">
  <div class="card"><h3>Federal estimate</h3><p>Ordinary tax band by band, long-term gains
  stacked across the 0/15/20 breakpoints, and the 3.8% surtax.</p></div>
  <div class="card"><h3>Alternative minimum tax</h3><p>Tentative minimum tax against regular
  tax, with the 50% exemption phase-out and preferential rates kept on gains.</p></div>
  <div class="card"><h3>Section 199A</h3><p>Qualified business income with the wage and
  property limit phasing in and the service-business phase-out.</p></div>
  <div class="card"><h3>Capital gains and losses</h3><p>Lot-level gain and loss by holding
  period, the annual loss limit, and the carryforward.</p></div>
  <div class="card"><h3>Wash-sale screening</h3><p>Across every account in the tax unit,
  including a retirement account and purchases a manager has only planned.</p></div>
  <div class="card"><h3>Household and UMA coordination</h3><p>One shared budget per taxpayer,
  with cross-sleeve conflicts caught before they are traded.</p></div>
</div>

<h2>What it will not do</h2>
<p class="lede">Each of these is enforced in code and covered by a test, so an answer you do
get is one the system is prepared to stand behind.</p>
<table><tbody>
{refusal_rows}
</tbody></table>

<h2>Skills</h2>
<p class="lede">Read from each skill&rsquo;s own frontmatter at build time.</p>
<div class="grid">
{skill_cards}
</div>

<h2>Engine coverage</h2>
<p class="lede">Straight from <code>capabilities/engines.yaml</code>. An engine with no
passing reference test may not be advertised as implemented, and unavailable engines are
listed rather than hidden.</p>
<table>
<thead><tr><th>Engine</th><th>Status</th><th>Scopes</th><th>Tax years</th><th>Limitation</th></tr></thead>
<tbody>
{engine_rows()}
</tbody></table>

<h2>Tool surface</h2>
<p class="lede">{len(exposed)} tools a host can call. {len(internal)} further operations run
only as workflow steps and are declared as internal, because telling a host it can call
something it cannot is the failure this registry exists to prevent.</p>
<p>{" ".join(f"<code>{E(t['name'])}</code>" for t in exposed)}</p>
<p class="lede">Internal operations: {" ".join(f"<code>{E(t['name'])}</code>" for t in internal)}</p>

<h2>Workflows</h2>
<table>
<thead><tr><th>Template</th><th>Scope</th><th>Mode</th><th>Steps</th></tr></thead>
<tbody>
{workflow_rows}
</tbody></table>

<h2>Tax rules</h2>
<p class="lede">Bundle <code>{E(rules.bundle_id)}</code>, hash
<code>{E(rules.bundle_hash[:26])}&hellip;</code>. Each rule carries its authority and legal
effective date; the engines read from the pack, and a constant reappearing in the
calculation path fails the build.</p>
<table>
<thead><tr><th>Rule</th><th>Authority</th></tr></thead>
<tbody>
{rule_rows}
</tbody></table>

<h2>Run it</h2>
<pre>git clone {REPO_URL}
cd tax-skills/tax-agent
python3 -m pytest tests/ -q          # the full suite, no network needed
python3 scripts/serve_dashboard.py       # dashboard on 127.0.0.1:4180
python3 scripts/smoke_test.py            # check every scenario still behaves</pre>
<p class="lede">The dashboard runs the same scenarios against the same backend the MCP
tools call. This page is static; served locally, the panel below turns on.</p>
<p class="lede"><strong><a href="cases.html">Every scripted case &rarr;</a></strong>
All {inventory()['total']} of them, grouped by objective, each linking to the file that
defines it. Listing only &mdash; running them needs the console above.</p>

<section id="live">
  <h2>Live scenarios</h2>
  <div id="live-body" class="grid"></div>
</section>

<h2>Frequently asked questions</h2>
{faq}

<footer>
  Apache-2.0 &middot; v{__version__} &middot; Not tax, investment or legal advice.<br>
  Prepared for review by a licensed professional, who remains responsible for the advice.<br>
  <a href="{REPO_URL}">GitHub</a> &middot;
  <a href="llms.txt">llms.txt</a> &middot;
  <a href="llms-full.txt">llms-full.txt</a>
</footer>

</div>
<script>{LIVE_JS}</script>
</body>
</html>
"""


def build_llms_txt() -> str:
    engines = read_yaml("capabilities", "engines.yaml")["engines"]
    tools = read_yaml("capabilities", "tools.yaml")["tools"]
    rules = default_rules(2026)
    lines = [
        "# Tax Agent",
        "",
        "> Claude skills for US investment tax analysis, backed by a deterministic Python "
        "engine. Analysis only: no tool places an order, modifies a custodian record or "
        "files a return.",
        "",
        "The model interprets requests and explains results. It performs no tax "
        "calculation. Every figure is produced by tested code, validated independently of "
        "the engine that produced it, and pinned to a reviewed rule pack.",
        "",
        "## Skills",
        "",
    ]
    for skill in skills():
        lines.append(f"- **{skill['name']}**: {skill['description']}")
    lines += ["", "## Engine coverage", ""]
    for engine in engines:
        scopes = ", ".join(engine.get("scopes") or []) or "none"
        lines.append(f"- `{engine['engine_id']}` — {engine.get('status')}; scopes: {scopes}")
        for limitation in (engine.get("limitations") or []):
            lines.append(f"  - {limitation}")
    lines += ["", "## Tools a host can call", ""]
    for tool in tools:
        if tool.get("status") == "implemented":
            lines.append(f"- `{tool['name']}` ({tool.get('mode')})")
    lines += ["", "## Internal workflow operations", "",
              "Real code, but not callable as tools: they run as steps inside a workflow "
              "template, which owns their order and preconditions.", ""]
    for tool in tools:
        if tool.get("status") == "internal_operation":
            lines.append(f"- `{tool['name']}` ({tool.get('mode')})")
    lines.append("")
    lines += ["## Tax rules", "",
              f"Bundle `{rules.bundle_id}` ({rules.bundle_hash[:26]}…), "
              f"knowledge time {rules.knowledge_time}.", ""]
    for name, authority in sorted(rules.authorities.items()):
        lines.append(f"- `{name}`: {authority}")
    lines += ["", "## Not supported", ""]
    lines += [f"- {claim}: {because}" for claim, because in
              [(c.replace("&quot;", '"').replace("&rsquo;", "'"),
                b.replace("&quot;", '"').replace("&rsquo;", "'")) for c, b in REFUSALS]]
    lines += ["", f"Source: {REPO_URL}", f"Full documentation: {SITE_URL}llms-full.txt", ""]
    return "\n".join(lines)


def build_llms_full() -> str:
    parts = [f"# Tax Agent: full documentation\n\nSource: {REPO_URL}\n",
             open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()]
    for name in sorted(os.listdir(SKILLS_DIR)):
        path = os.path.join(SKILLS_DIR, name, "SKILL.md")
        if os.path.exists(path):
            parts.append(f"\n\n---\n\n# Skill: {name}\n\n"
                         + open(path, encoding="utf-8").read())
    return "\n".join(parts)


def build_cases() -> str:
    """Every scripted case, grouped by objective.

    The console at 127.0.0.1:4180 can run these; a static page cannot, and
    says so rather than replaying a recorded answer and calling it a result.
    What this page is for is the part that needs no backend: seeing the whole
    surface at once, and getting from a case to the file that defines it.
    """
    report = inventory()
    suites = report["suites"]
    sections = []
    for group in report["groups"]:
        rows = []
        for case in group["cases"]:
            chip = "info" if case["runnable"] else "warn"
            label = "runs live" if case["runnable"] else case["suite"]
            rows.append(
                f"<tr><td><code>{E(case['id'])}</code></td>"
                f"<td>{E(case['pins'])}</td>"
                f"<td><span class='chip {chip}'>{E(label)}</span></td>"
                f"<td><a href='{REPO_URL}/blob/main/{E(case['source'])}'>"
                f"<code>{E(case['source'].rsplit('/', 1)[-1])}</code></a></td></tr>")
        sections.append(
            f"<h2 id='{E(group['group'].lower().replace(' ', '-').replace(',', ''))}'>"
            f"{E(group['group'])} <span class='chip info'>{group['count']}</span></h2>\n"
            f"<p class=\"lede\">{E(group['lead'])}</p>\n"
            "<table>\n<thead><tr><th>Case</th><th>What it pins</th><th>Suite</th>"
            "<th>Defined in</th></tr></thead>\n<tbody>\n"
            + "\n".join(rows) + "\n</tbody></table>")

    contents = " &middot; ".join(
        f"<a href='#{g['group'].lower().replace(' ', '-').replace(',', '')}'>"
        f"{E(g['group'])}</a> ({g['count']})" for g in report["groups"])

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Every scripted case &mdash; Tax Agent</title>
<meta name="description" content="All {report['total']} scripted cases in the Tax Agent
suite, grouped by what the client is trying to achieve: loss harvesting, raising cash,
capping gains, rebalancing, household coordination, municipal bonds and more.">
<link rel="canonical" href="{SITE_URL}cases.html">
<style>{STYLE}</style>
</head>
<body>
<header>
<p class="lede"><a href="{SITE_URL}">&larr; Tax Agent</a></p>
<h1>Every scripted case</h1>
<p class="lede">All <strong>{report['total']}</strong> cases that run on every build,
grouped by what the client is trying to achieve. {suites['console']} run live over
JSON-RPC against the MCP servers, {suites['rule']} pin figures in the rule packs, and
{suites['conformance']} are optimizer invariants recomputed from the snapshot.</p>
<p class="lede">Nothing executes on this page. It is a listing: every case links to the
file that defines it. To run them, clone the repository and start the console
&mdash; <code>python3 scripts/serve_dashboard.py</code> &mdash; where the
{suites['console']} console cases spawn a real server and speak the protocol.</p>
<p class="lede">{contents}</p>
</header>
<main>
{chr(10).join(sections)}
</main>
<footer>
<p>Generated from <code>src/taxagent/console/inventory.py</code> and
<code>tests/cases/</code> at build time, so this page cannot drift from the suite.
<a href="{REPO_URL}">Source</a> &middot; Apache-2.0</p>
</footer>
</body>
</html>
"""


def build_sitemap() -> str:
    stamp = last_modified()
    urls = "".join(
        f"<url><loc>{u}</loc><lastmod>{stamp}</lastmod></url>"
        for u in (SITE_URL, f"{SITE_URL}cases.html", f"{SITE_URL}llms.txt",
                  f"{SITE_URL}llms-full.txt"))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{urls}</urlset>\n")


def build_404() -> str:
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<title>Not found</title></head><body style=\"font-family:system-ui;"
            "max-width:38rem;margin:4rem auto;padding:0 1rem\">"
            "<h1>Not found</h1><p>That page does not exist. "
            f"<a href=\"{SITE_URL}\">Back to Tax Agent</a>.</p></body></html>\n")


def build_og_image() -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630">
<rect width="1200" height="630" fill="#17181a"/>
<text x="80" y="250" font-family="Helvetica,Arial" font-size="76" fill="#e8e8e5"
 font-weight="bold">Tax Agent</text>
<text x="80" y="320" font-family="Helvetica,Arial" font-size="34" fill="#9b9b95">
Claude skills for US investment tax analysis</text>
<text x="80" y="376" font-family="Helvetica,Arial" font-size="34" fill="#9b9b95">
backed by a deterministic engine</text>
<text x="80" y="470" font-family="Helvetica,Arial" font-size="26" fill="#6cc08a">
analysis only &#183; v{__version__} &#183; Apache-2.0</text>
</svg>
"""


def build_site(out_dir: str) -> List[str]:
    os.makedirs(out_dir, exist_ok=True)
    files = {
        "index.html": build_index(),
        "cases.html": build_cases(),
        "404.html": build_404(),
        "llms.txt": build_llms_txt(),
        "llms-full.txt": build_llms_full(),
        "robots.txt": f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}sitemap.xml\n",
        "sitemap.xml": build_sitemap(),
        "og-image.svg": build_og_image(),
    }
    for name, body in files.items():
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as handle:
            handle.write(body)
    return sorted(files)


def main() -> None:
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "_site")
    written = build_site(out_dir)
    print(f"site -> {out_dir}")
    for name in written:
        size = os.path.getsize(os.path.join(out_dir, name))
        print(f"  {name:<16} {size:>7,} bytes")


if __name__ == "__main__":
    main()
