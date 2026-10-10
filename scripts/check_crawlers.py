#!/usr/bin/env python3
"""Check that an answer engine can actually fetch and read a deployed site.

Three different questions get called "is the crawler working", and only two of
them can be answered from here:

  1. May it fetch?   robots.txt, parsed the way a crawler parses it.
  2. Can it read?    What each agent receives: status, bytes, and whether the
                     page still says anything with JavaScript switched off.
  3. Did it visit?   Not answerable. GitHub Pages keeps no access log, so
                     nothing here can tell you a crawler came. That needs a
                     proxy or CDN in front of the site.

    python3 scripts/check_crawlers.py [base-url ...]

Exits non-zero if any check fails, so it can gate a deploy.
"""
from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request
from typing import List, Tuple
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

DEFAULT_SITES = ["https://ai-theories.github.io/tax-skills/"]

#: The user agent each engine actually sends, not the token in robots.txt.
AGENTS = {
    "GPTBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; "
              "GPTBot/1.2; +https://openai.com/gptbot",
    "OAI-SearchBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); "
                     "compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot",
    "ChatGPT-User": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); "
                    "compatible; ChatGPT-User/1.0; +https://openai.com/bot",
    "ClaudeBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; "
                 "ClaudeBot/1.0; +claudebot@anthropic.com",
    "PerplexityBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); "
                     "compatible; PerplexityBot/1.0; "
                     "+https://perplexity.ai/perplexitybot",
    "Google-Extended": "Mozilla/5.0 (compatible; Googlebot/2.1; "
                       "+http://www.google.com/bot.html)",
    "CCBot": "CCBot/2.0 (https://commoncrawl.org/faq/)",
    "Applebot-Extended": "Mozilla/5.0 (compatible; Applebot/0.1; "
                         "+http://www.apple.com/go/applebot)",
}

TIMEOUT = 20
failures: List[str] = []


def fetch(url: str, agent: str = "crawler-check/1.0") -> Tuple[int, bytes, str]:
    request = urllib.request.Request(url, headers={"User-Agent": agent})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, response.read(), response.headers.get(
                "content-type", "")
    except urllib.error.HTTPError as error:
        return error.code, b"", ""
    except Exception as error:                      # noqa: BLE001 - reported, not raised
        return 0, str(error).encode(), ""


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    if not ok:
        failures.append(label)


def visible_text_without_js(page: str) -> str:
    """What a crawler that does not run JavaScript is left with.

    Most do not. A page whose content arrives through fetch() reads as empty to
    them however good its markup is.
    """
    stripped = re.sub(r"<script.*?</script>", " ", page, flags=re.S | re.I)
    stripped = re.sub(r"<style.*?</style>", " ", stripped, flags=re.S | re.I)
    return " ".join(re.sub(r"<[^>]+>", " ", stripped).split())


def check_site(base: str) -> None:
    print(f"\n{base}")

    # A crawler fetches /robots.txt at the HOST root and nowhere else. A file at
    # /project/robots.txt is never requested, so checking it proves nothing: this
    # read the project copy once and passed every agent against rules no crawler
    # would ever see.
    parts = urlparse(base)
    root = f"{parts.scheme}://{parts.netloc}/"
    print(f" robots.txt at the host root ({root}robots.txt)")
    status, body, _ = fetch(root + "robots.txt")
    check("root robots.txt served", status == 200, f"HTTP {status}")
    if status == 200:
        parser = RobotFileParser()
        parser.parse(body.decode("utf-8", "replace").splitlines())
        for name in AGENTS:
            check(f"{name} may fetch this project", parser.can_fetch(name, base))
        check("this project's sitemap is declared at the root",
              f"{base}sitemap.xml".encode() in body,
              "a sitemap listed only inside the project is not discovered")

    if root.rstrip("/") != base.rstrip("/"):
        code, _, _ = fetch(base + "robots.txt")
        if code == 200:
            print(f"  NOTE  {base}robots.txt exists and is never fetched by a "
                  "crawler; only the host root counts")

    print(" what each agent receives")
    for name, agent in AGENTS.items():
        status, body, _ = fetch(base, agent)
        page = body.decode("utf-8", "replace")
        text = visible_text_without_js(page)
        ok = status == 200 and len(text) > 1000
        check(f"{name}", ok, f"HTTP {status}, {len(text):,} chars without JS")

    print(" the files written for language models")
    for name in ("llms.txt", "llms-full.txt"):
        status, body, kind = fetch(base + name, AGENTS["ClaudeBot"])
        check(f"{name}", status == 200 and len(body) > 500,
              f"HTTP {status}, {len(body):,} bytes")

    print(" structured data a crawler can parse without rendering")
    status, body, _ = fetch(base, AGENTS["GPTBot"])
    page = body.decode("utf-8", "replace")
    blocks = re.findall(r"<script[^>]*ld\+json[^>]*>(.*?)</script>", page, re.S)
    check("JSON-LD present in the raw HTML", bool(blocks),
          f"{len(blocks)} block(s)")
    check("title present", "<title>" in page)
    check("canonical present", 'rel="canonical"' in page)

    print(" every sitemap URL resolves")
    status, body, _ = fetch(base + "sitemap.xml")
    locations = re.findall(r"<loc>([^<]+)</loc>", body.decode("utf-8", "replace"))
    check("sitemap lists URLs", bool(locations), f"{len(locations)} URLs")
    for url in locations:
        code, _, _ = fetch(url, AGENTS["PerplexityBot"])
        check(f"  {url.rsplit('/', 1)[-1] or '/'}", code == 200, f"HTTP {code}")


def main() -> None:
    for base in (sys.argv[1:] or DEFAULT_SITES):
        check_site(base if base.endswith("/") else base + "/")
    print()
    if failures:
        print(f"{len(failures)} check(s) failed")
        sys.exit(1)
    print("all checks passed")
    print("\nNot checked here: whether a crawler has actually visited. GitHub Pages\n"
          "keeps no access log, so that needs a CDN or proxy in front of the site.")


if __name__ == "__main__":
    main()
