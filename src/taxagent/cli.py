"""Command line interface.

    python3 -m taxagent federal tax --income 185000 --status MFJ --gain 50000

Every command prints a human summary by default and machine JSON with --json.
Calculations come from the same engines the MCP tools and the dashboard use, so
a number seen here is the number an agent would receive.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Optional

from . import __version__
from .decisions.rule_registry import RulePackError, income_rules
from .domain.money import MoneyError
from .tax import federal

STATUSES = ("SINGLE", "MFJ", "MFS", "HOH")


# --- output --------------------------------------------------------------

def emit(payload: Dict[str, Any], lines: List[str], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print("\n".join(lines))


def money_line(label: str, value: str, width: int = 34) -> str:
    return f"  {label:<{width}} {value:>14}"


# --- federal -------------------------------------------------------------

def cmd_federal_tax(args) -> int:
    result = federal.summarize_federal(
        ordinary_income=args.income, filing_status=args.status,
        long_term_gain=args.gain, deduction=args.deduction,
        net_investment_income=args.nii, tax_year=args.year)
    payload = result.to_json()
    lines = [
        f"Federal estimate — {result.filing_status}, tax year {result.tax_year}",
        "",
        money_line("Ordinary income", f"${result.gross_income.to_json()}"),
        money_line("Deduction", f"-${result.deduction.to_json()}"),
        money_line("Ordinary taxable income", f"${result.ordinary.taxable_income.to_json()}"),
        money_line("Tax on ordinary income", f"${result.ordinary.tax.to_json()}"),
    ]
    for band in result.ordinary.bands:
        lines.append(money_line(f"    {band.rate * 100:.0f}% on "
                                f"${band.income_in_band.to_json()}",
                                f"${band.tax.to_json()}"))
    if result.capital_gain.net_long_term_gain.amount > 0:
        lines += ["", money_line("Net long-term gain",
                                 f"${result.capital_gain.net_long_term_gain.to_json()}")]
        for band in result.capital_gain.bands:
            lines.append(money_line(f"    {band.rate * 100:.0f}% on "
                                    f"${band.income_in_band.to_json()}",
                                    f"${band.tax.to_json()}"))
        lines.append(money_line("Tax on gains "
                                f"(blended {result.capital_gain.blended_rate})",
                                f"${result.capital_gain.tax.to_json()}"))
    if result.niit.tax.amount > 0:
        lines += ["", money_line("Net investment income tax",
                                 f"${result.niit.tax.to_json()}")]
    lines += [
        "",
        money_line("TOTAL", f"${result.total_tax.to_json()}"),
        money_line("Marginal rate on ordinary income",
                   f"{result.ordinary.marginal_rate * 100:.0f}%"),
        money_line("Effective rate", result.effective_rate),
        "",
        f"  Rules: {result.rule_bundle_ref}",
        "  Estimate for professional review. Not tax advice. AMT, credits, QBI,",
        "  phase-outs and state tax are not modelled.",
    ]
    emit(payload, lines, args.json)
    return 0


def cmd_federal_capgains(args) -> int:
    result = federal.calculate_capital_gain_tax(
        args.gain, args.ordinary, args.status, args.year)
    lines = [f"Long-term capital gain — {args.status.upper()}, {args.year}", "",
             money_line("Net long-term gain", f"${result.net_long_term_gain.to_json()}"),
             money_line("Stacked on ordinary taxable income",
                        f"${result.ordinary_taxable_income.to_json()}"), ""]
    for band in result.bands:
        lines.append(money_line(f"  {band.rate * 100:.0f}% on ${band.income_in_band.to_json()}",
                                f"${band.tax.to_json()}"))
    lines += ["", money_line("Tax", f"${result.tax.to_json()}"),
              money_line("Blended rate", result.blended_rate), "",
              "  The gain is split across the bands it spans, not taxed wholly at",
              "  the rate its top dollar reaches."]
    emit(result.to_json(), lines, args.json)
    return 0


def cmd_federal_niit(args) -> int:
    result = federal.calculate_niit(args.nii, args.magi, args.status, args.year)
    lines = [f"Net investment income tax — {args.status.upper()}, {args.year}", "",
             money_line("Net investment income", f"${result.net_investment_income.to_json()}"),
             money_line("MAGI", f"${result.magi.to_json()}"),
             money_line("Threshold", f"${result.threshold.to_json()}"),
             money_line("Lesser of NII or excess", f"${result.amount_subject.to_json()}"),
             "", money_line("Tax at 3.8%", f"${result.tax.to_json()}"), "",
             "  The threshold is fixed in statute and not indexed for inflation."]
    emit(result.to_json(), lines, args.json)
    return 0


def cmd_federal_loss(args) -> int:
    result = federal.calculate_capital_loss_deduction(args.loss, args.status, args.year)
    lines = [f"Capital loss deduction — {args.status.upper()}, {args.year}", "",
             money_line("Net capital loss", f"${result.net_capital_loss.to_json()}"),
             money_line("Annual limit", f"${result.limit.to_json()}"),
             money_line("Deductible this year", f"${result.deductible_this_year.to_json()}"),
             money_line("Carried forward", f"${result.carryforward.to_json()}"), "",
             "  Carries forward indefinitely for individuals."]
    emit(result.to_json(), lines, args.json)
    return 0


def cmd_federal_amt(args) -> int:
    result = federal.calculate_amt(args.amti, args.regular_tax, args.status,
                                   net_capital_gain=args.gain, tax_year=args.year)
    lines = [f"Alternative minimum tax — {args.status.upper()}, {args.year}", "",
             money_line("AMTI", f"${result.amti.to_json()}"),
             money_line("Statutory exemption", f"${result.statutory_exemption.to_json()}")]
    if result.exemption_lost_to_phaseout.amount > 0:
        lines.append(money_line("  lost to phase-out at 50c on the dollar",
                                f"-${result.exemption_lost_to_phaseout.to_json()}"))
    lines += [money_line("Effective exemption", f"${result.effective_exemption.to_json()}"),
              money_line("AMT base", f"${result.amt_base.to_json()}"), ""]
    for band in result.bands:
        lines.append(money_line(f"  {band.rate * 100:.0f}% on ${band.income_in_band.to_json()}",
                                f"${band.tax.to_json()}"))
    lines += ["",
              money_line("Tentative minimum tax", f"${result.tentative_minimum_tax.to_json()}"),
              money_line("Regular tax", f"-${result.regular_tax.to_json()}"),
              money_line("AMT PAYABLE", f"${result.amt_payable.to_json()}"), ""]
    lines.append("  AMT applies." if result.applies
                 else "  Regular tax is higher, so no AMT is due.")
    lines.append("  Not modelled: AMT credit carryforward, ISO basis differences,")
    lines.append("  and section 57 preferences beyond the AMTI figure supplied.")
    emit(result.to_json(), lines, args.json)
    return 0


def cmd_federal_qbi(args) -> int:
    result = federal.calculate_qbi_deduction(
        args.qbi, args.taxable_income, args.status, w2_wages=args.wages,
        ubia=args.ubia, net_capital_gain=args.gain, is_sstb=args.sstb,
        tax_year=args.year)
    lines = [f"Section 199A deduction — {args.status.upper()}, {args.year}", "",
             money_line("Qualified business income", f"${result.qualified_business_income.to_json()}"),
             money_line("Taxable income before the deduction",
                        f"${result.taxable_income.to_json()}"),
             money_line("Threshold", f"${result.threshold.to_json()}"),
             money_line("Phase-in progress", result.phase_in_ratio),
             money_line("Specified service business", "yes" if result.is_sstb else "no"), "",
             money_line("20% of QBI", f"${result.tentative_deduction.to_json()}"),
             money_line("Wage and property limit",
                        f"${result.wage_and_property_limit.to_json()}"),
             money_line("20% of taxable income less gains",
                        f"${result.taxable_income_limit.to_json()}"), "",
             money_line("DEDUCTION", f"${result.deduction.to_json()}"),
             money_line("Binding limit", result.limit_applied), ""]
    for note in result.notes:
        lines.append(f"  {note}")
    emit(result.to_json(), lines, args.json)
    return 0


# --- discovery -----------------------------------------------------------

CAPABILITIES = [
    {"command": "federal tax",
     "summary": "Full federal estimate: ordinary tax, gains stacked across the "
                "preferential bands, and NIIT.",
     "typical_requests": ["what will they owe this year",
                          "estimate their federal tax",
                          "how much tax on this income and these gains"],
     "inputs": ["--income", "--status", "--gain", "--nii", "--deduction", "--year"]},
    {"command": "federal capgains",
     "summary": "Tax on net long-term gain, split across the 0/15/20 bands it spans.",
     "typical_requests": ["what is the tax on this gain",
                          "are they in the zero percent capital gains bracket",
                          "how much of the gain is taxed at 15%"],
     "inputs": ["--gain", "--ordinary", "--status", "--year"]},
    {"command": "federal niit",
     "summary": "3.8% net investment income tax on the lesser of NII or MAGI excess.",
     "typical_requests": ["do they owe the 3.8% surtax",
                          "are they over the NIIT threshold"],
     "inputs": ["--nii", "--magi", "--status", "--year"]},
    {"command": "federal amt",
     "summary": "Alternative minimum tax: tentative minimum tax against regular tax, with "
                "the 50% exemption phase-out and preferential rates preserved on gains.",
     "typical_requests": ["will they be caught by AMT",
                          "how much alternative minimum tax",
                          "did exercising those options trigger AMT"],
     "inputs": ["--amti", "--regular-tax", "--status", "--gain", "--year"]},
    {"command": "federal qbi",
     "summary": "Section 199A deduction, including the wage and property limit phasing in "
                "and the specified-service phase-out.",
     "typical_requests": ["how much qualified business income deduction",
                          "does the wage limit bite",
                          "what is the 199A deduction for this pass-through"],
     "inputs": ["--qbi", "--taxable-income", "--wages", "--ubia", "--sstb", "--status"]},
    {"command": "federal loss",
     "summary": "How much net capital loss is deductible now and what carries forward.",
     "typical_requests": ["how much of this loss can they use",
                          "what is the capital loss carryforward"],
     "inputs": ["--loss", "--status", "--year"]},
]


def cmd_capabilities(args) -> int:
    if args.json:
        print(json.dumps(CAPABILITIES, indent=2))
        return 0
    print(f"taxagent {__version__} — what this CLI can answer\n")
    for entry in CAPABILITIES:
        print(f"  {entry['command']}")
        print(f"      {entry['summary']}")
        print(f"      inputs: {' '.join(entry['inputs'])}")
        print(f"      ask it: {entry['typical_requests'][0]}")
        print()
    print("  Not covered here: state tax, retirement distributions,")
    print("  municipal bonds, equity compensation. The registry refuses those")
    print("  rather than approximating them.")
    return 0


def cmd_rules(args) -> int:
    rules = income_rules(args.year)
    payload = rules.to_json()
    lines = [f"Rule packs in force for {args.year}", "",
             f"  income parameters : {rules.bundle_id}",
             f"  hash              : {rules.bundle_hash[:30]}…",
             f"  knowledge time    : {rules.knowledge_time}", "", "  Authorities:"]
    for name, authority in sorted(rules.authorities.items()):
        lines.append(f"    {name:<26} {authority}")
    lines += ["", "  Figures are transcribed and await sign-off by a named reviewer."]
    emit(payload, lines, args.json)
    return 0


# --- wiring --------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="taxagent",
        description="US tax calculations backed by versioned rule packs. "
                    "Analysis only; nothing here places a trade or files a return.")
    parser.add_argument("--version", action="version", version=f"taxagent {__version__}")
    subparsers = parser.add_subparsers(dest="group", required=True)

    def add_common(sub):
        sub.add_argument("--status", default="MFJ", choices=STATUSES + tuple(
            s.lower() for s in STATUSES), help="filing status (default MFJ)")
        sub.add_argument("--year", type=int, default=2026, help="tax year (default 2026)")
        sub.add_argument("--json", action="store_true", help="machine-readable output")

    fed = subparsers.add_parser("federal", help="federal income tax").add_subparsers(
        dest="command", required=True)

    p = fed.add_parser("tax", help="full federal estimate")
    p.add_argument("--income", required=True, help="ordinary income, e.g. 185000")
    p.add_argument("--gain", default="0", help="net long-term capital gain")
    p.add_argument("--nii", default="0", help="net investment income")
    p.add_argument("--deduction", default=None, help="itemized deduction; omit for standard")
    add_common(p)
    p.set_defaults(func=cmd_federal_tax)

    p = fed.add_parser("capgains", help="tax on net long-term gain")
    p.add_argument("--gain", required=True)
    p.add_argument("--ordinary", default="0", help="ordinary taxable income the gain stacks on")
    add_common(p)
    p.set_defaults(func=cmd_federal_capgains)

    p = fed.add_parser("niit", help="net investment income tax")
    p.add_argument("--nii", required=True)
    p.add_argument("--magi", required=True)
    add_common(p)
    p.set_defaults(func=cmd_federal_niit)

    p = fed.add_parser("amt", help="alternative minimum tax")
    p.add_argument("--amti", required=True, help="alternative minimum taxable income")
    p.add_argument("--regular-tax", required=True, dest="regular_tax",
                   help="regular tax for comparison")
    p.add_argument("--gain", default="0", help="net capital gain inside AMTI")
    add_common(p)
    p.set_defaults(func=cmd_federal_amt)

    p = fed.add_parser("qbi", help="section 199A deduction")
    p.add_argument("--qbi", required=True, help="qualified business income")
    p.add_argument("--taxable-income", required=True, dest="taxable_income",
                   help="taxable income before this deduction")
    p.add_argument("--wages", default="0", help="W-2 wages paid by the business")
    p.add_argument("--ubia", default="0", help="unadjusted basis of qualified property")
    p.add_argument("--gain", default="0", help="net capital gain")
    p.add_argument("--sstb", action="store_true", help="specified service trade or business")
    add_common(p)
    p.set_defaults(func=cmd_federal_qbi)

    p = fed.add_parser("loss", help="capital loss deduction and carryforward")
    p.add_argument("--loss", required=True)
    add_common(p)
    p.set_defaults(func=cmd_federal_loss)

    p = subparsers.add_parser("capabilities", help="what this CLI can answer")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_capabilities)

    p = subparsers.add_parser("rules", help="which rule packs are in force")
    p.add_argument("--year", type=int, default=2026)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_rules)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if hasattr(args, "status"):
        args.status = args.status.upper()
    try:
        return args.func(args)
    except RulePackError as exc:
        print(f"error [{exc.code}]: {exc}", file=sys.stderr)
        return 2
    except (MoneyError, InvalidOperation) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
