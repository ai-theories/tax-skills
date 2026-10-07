"""Scripted use cases, each exercised through a real MCP tool call.

Every case names the server, the tool and the exact arguments, plus what it is
meant to demonstrate and the outcome it must produce. The console runs them
against the shipped servers over JSON-RPC, so a case that passes is evidence
the tool works as declared — not that an engine behind it computes correctly,
which the test suite covers separately.

`expect` is the status or code the run must produce. A case whose outcome
changes is a regression or a feature, never a surprise: the expectation lives
beside the case and the console fails it loudly.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

from ..application.bootstrap import PROJECT_ROOT

FIXTURES = os.path.join(PROJECT_ROOT, "tests", "fixtures")
HOUSEHOLD = os.path.join(FIXTURES, "patel_household.json")
UMA = os.path.join(FIXTURES, "patel_uma.json")
TWO_ACCOUNTS = os.path.join(FIXTURES, "order_dependence.json")

AS_OF = "2026-09-15"
PRICES = {"VTI": "260.00", "AAPL": "205.00", "ARKK": "62.00", "TLT": "88.00",
          "AAA": "60.00", "BBB": "200.00"}

FEDERAL = "taxagent-federal"
PORTFOLIO = "taxagent-portfolio"
HOUSEHOLD_SERVER = "taxagent-household"
ESTATE = "taxagent-estate"
INTERNATIONAL = "taxagent-international"
BUSINESS = "taxagent-business"
COMPLIANCE = "taxagent-compliance"
SALT = "taxagent-salt"
MUNI = "taxagent-muni"


def _run(**overrides) -> Dict[str, Any]:
    base = {"subject_ref": "acct_schwab_joint", "document_ref": HOUSEHOLD,
            "as_of": AS_OF, "prices": PRICES}
    base.update(overrides)
    return base


USE_CASES: List[Dict[str, Any]] = [
    # --- federal: figures the caller supplies -----------------------------
    {"id": "federal-estimate", "group": "Federal", "server": FEDERAL,
     "tool": "calculate_federal",
     "title": "What does $185,000 and a $50,000 gain cost?",
     "shows": "Ordinary tax band by band, then the gain stacked on top of taxable "
              "income so it lands in the right preferential band.",
     "arguments": {"income": "185000", "status": "MFJ", "gain": "50000",
                   "nii": "50000"},
     "expect": "completed"},

    {"id": "federal-gain-splits", "group": "Federal", "server": FEDERAL,
     "tool": "calculate_capgains",
     "title": "A gain that straddles the 0% and 15% bands",
     "shows": "The gain is split across bands rather than taxed wholly at the rate "
              "its top dollar reaches. This is the error the legacy code made.",
     "arguments": {"gain": "100000", "ordinary": "60000", "status": "MFJ"},
     "expect": "completed"},

    {"id": "federal-niit-threshold", "group": "Federal", "server": FEDERAL,
     "tool": "calculate_niit",
     "title": "Surtax on the lesser of investment income or the MAGI excess",
     "shows": "At $260,000 MAGI only $10,000 is over the threshold, so the 3.8% "
              "applies to that, not to the whole $45,000 of investment income.",
     "arguments": {"nii": "45000", "magi": "260000", "status": "MFJ"},
     "expect": "completed"},

    {"id": "federal-amt-bites", "group": "Federal", "server": FEDERAL,
     "tool": "calculate_amt",
     "title": "When the alternative minimum tax actually applies",
     "shows": "Tentative minimum tax against regular tax, with the exemption "
              "phasing out at 50 cents per dollar over the threshold.",
     "arguments": {"amti": "1400000", "regular_tax": "300000", "status": "MFJ"},
     "expect": "completed"},

    {"id": "federal-amt-keeps-rates", "group": "Federal", "server": FEDERAL,
     "tool": "calculate_amt",
     "title": "AMT does not reprice long-term gains",
     "shows": "A gain inside AMTI keeps its preferential rate. Taxing it at 26/28% "
              "would overstate the liability substantially.",
     "arguments": {"amti": "900000", "regular_tax": "180000", "gain": "300000",
                   "status": "MFJ"},
     "expect": "completed"},

    {"id": "federal-qbi-phase-in", "group": "Federal", "server": FEDERAL,
     "tool": "calculate_qbi",
     "title": "The wage limit phasing in, not switching on",
     "shows": "Above the threshold the wage limit applies in proportion to how far "
              "into the phase-in range the taxpayer is, not all at once.",
     "arguments": {"qbi": "300000", "taxable_income": "450000", "wages": "80000",
                   "sstb": "no", "status": "MFJ"},
     "expect": "completed"},

    {"id": "federal-qbi-sstb", "group": "Federal", "server": FEDERAL,
     "tool": "calculate_qbi",
     "title": "A service business past the phase-out",
     "shows": "The same figures as a consultancy rather than a manufacturer: the "
              "applicable percentage falls to zero and the deduction goes with it.",
     "arguments": {"qbi": "300000", "taxable_income": "560000", "wages": "80000",
                   "sstb": "yes", "status": "MFJ"},
     "expect": "completed"},

    {"id": "federal-loss-limit", "group": "Federal", "server": FEDERAL,
     "tool": "calculate_loss",
     "title": "$12,000 of losses, $3,000 deductible",
     "shows": "The annual limit and what carries forward, rather than deducting "
              "the whole loss against ordinary income.",
     "arguments": {"loss": "12000", "status": "MFJ"},
     "expect": "completed"},

    {"id": "federal-rejects-float", "group": "Federal", "server": FEDERAL,
     "tool": "calculate_capgains",
     "title": "A figure that cannot be represented exactly is refused",
     "shows": "Money is decimal. A value that would silently lose a fraction of a "
              "cent is rejected at the boundary rather than rounded quietly.",
     "arguments": {"gain": "not-a-number", "ordinary": "90000", "status": "MFJ"},
     "expect": "VALIDATION_FAILED"},

    {"id": "federal-capabilities", "group": "Federal", "server": FEDERAL,
     "tool": "get_federal_capabilities",
     "title": "What the server will not compute",
     "shows": "The boundary stated by the server itself: no return, no credits, no "
              "state tax, and AMT and section 199A never folded together.",
     "arguments": {},
     "expect": "completed"},

    # --- portfolio: real holdings, entitlement-gated -----------------------
    {"id": "portfolio-discover", "group": "Portfolio", "server": PORTFOLIO,
     "tool": "resolve_subject",
     "title": "Finding what you are allowed to analyse",
     "shows": "The first call of a session. Account metadata comes from the "
              "document, so it is named here; entitlements still decide the answer.",
     "arguments": {"subject_ref": "acct_schwab_joint", "document_ref": HOUSEHOLD},
     "expect": "completed"},

    {"id": "portfolio-denied", "group": "Portfolio", "server": PORTFOLIO,
     "tool": "resolve_subject",
     "title": "An account in the document you are not entitled to",
     "shows": "The document describes it, so it exists. It is still refused, and "
              "with the same wording as an account that does not exist at all.",
     "arguments": {"subject_ref": "acct_spouse_ext", "document_ref": HOUSEHOLD},
     "expect": "ACCESS_DENIED"},

    {"id": "portfolio-lots", "group": "Portfolio", "server": PORTFOLIO,
     "tool": "review_lots",
     "title": "Gain and loss by lot, with a wash-sale screen",
     "shows": "What is held and what it carries. Proposes nothing: this is the "
              "tool for 'where do we stand', not 'what should we do'.",
     "arguments": _run(idempotency_key="uc_lots_1"),
     "expect": "completed_with_limitations"},

    {"id": "portfolio-harvest", "group": "Portfolio", "server": PORTFOLIO,
     "tool": "review_harvest",
     "title": "Raise $10,000 by harvesting losses",
     "shows": "A proposal, the losses it realises, and the lots it held back — one "
              "for wash-sale risk, one for unknown basis.",
     "arguments": _run(cash_target="10000.00", gain_budget="15000.00",
                       netting_basis="net_gains", period="2026",
                       idempotency_key="uc_harvest_1"),
     "expect": "completed_with_limitations"},

    {"id": "portfolio-missing-netting", "group": "Portfolio", "server": PORTFOLIO,
     "tool": "review_harvest",
     "title": "A gain budget with no netting basis",
     "shows": "'Keep gains under $15,000' reads two ways and they give different "
              "answers, so the schema requires the caller to say which.",
     "arguments": _run(cash_target="10000.00", gain_budget="15000.00",
                       idempotency_key="uc_netting_1"),
     "expect": "protocol_or_needs_input"},

    {"id": "portfolio-infeasible", "group": "Portfolio", "server": PORTFOLIO,
     "tool": "review_harvest",
     "title": "A target the budget cannot reach",
     "shows": "The shortfall is reported. The constraint is never quietly relaxed "
              "to manufacture a result that looks like success.",
     "arguments": _run(cash_target="50000.00", gain_budget="15000.00",
                       netting_basis="net_gains", period="2026",
                       idempotency_key="uc_infeasible_1"),
     "expect": "INFEASIBLE_CONSTRAINTS"},

    {"id": "portfolio-planned-purchase", "group": "Portfolio", "server": PORTFOLIO,
     "tool": "review_harvest",
     "title": "A purchase you have not placed yet",
     "shows": "Screening a planned buy before it happens turns a disallowed loss "
              "into a decision the advisor still gets to make.",
     "arguments": _run(cash_target="10000.00",
                       planned_purchases=[{"account_id": "acct_schwab_joint",
                                           "security_id": "VTI", "quantity": "100",
                                           "trade_date": "2026-09-20",
                                           "unit_id": "advisor"}],
                       idempotency_key="uc_planned_1"),
     "expect": "completed_with_limitations"},

    {"id": "portfolio-idempotent", "group": "Portfolio", "server": PORTFOLIO,
     "tool": "review_harvest",
     # Only meaningful after the run it replays, so the dependency is declared
     # rather than left to the order of the list.
     "depends_on": "portfolio-harvest",
     "title": "The same key twice returns the same run",
     "shows": "Repeating a request must not produce a second analysis with a new "
              "evidence package. The key is the caller's guarantee.",
     "arguments": _run(cash_target="10000.00", gain_budget="15000.00",
                       netting_basis="net_gains", period="2026",
                       idempotency_key="uc_harvest_1"),
     "expect": "accepted"},

    {"id": "portfolio-household-refused", "group": "Portfolio", "server": PORTFOLIO,
     "tool": "review_harvest",
     "title": "A household asked of the account server",
     "shows": "Running an account engine over a household is refused rather than "
              "approximated, and the refusal names what is available instead.",
     "arguments": _run(subject_ref="household_tu_patel", cash_target="10000.00",
                       idempotency_key="uc_hh_refused_1"),
     "expect": "UNSUPPORTED_SCOPE"},

    {"id": "portfolio-wrong-year", "group": "Portfolio", "server": PORTFOLIO,
     "tool": "review_harvest",
     "title": "An analysis dated to a year with no rule pack",
     "shows": "No reviewed pack covers 2019, so the run is blocked rather than "
              "computed from this year's rules under last decade's law.",
     "arguments": _run(as_of="2019-09-15", cash_target="10000.00",
                       idempotency_key="uc_year_1"),
     "expect": "WRONG_RULE_YEAR"},

    {"id": "portfolio-intake", "group": "Portfolio", "server": PORTFOLIO,
     "tool": "check_holdings",
     "title": "What in this document cannot be used",
     "shows": "Run first on a new export. Lots with unknown basis are named here "
              "rather than silently treated as zero-basis later.",
     "arguments": _run(idempotency_key="uc_intake_1"),
     "expect": "completed_with_limitations"},

    {"id": "portfolio-rebalance", "group": "Portfolio", "server": PORTFOLIO,
     "tool": "review_rebalance",
     "title": "Rebalance to target weights, tax-aware",
     "shows": "Closes drift within a gain budget, never selling a security below its "
              "target. With chunky lots it under-trades rather than overshooting, and "
              "reports the residual.",
     "arguments": _run(target_weights={"VTI": "0.40", "AAPL": "0.30",
                                       "ARKK": "0.20", "TLT": "0.10"},
                       gain_budget="15000.00", netting_basis="gross_gains",
                       period="2026", idempotency_key="uc_rebal_1"),
     "expect": "completed_with_limitations"},

    {"id": "portfolio-rebalance-needs-targets", "group": "Portfolio",
     "server": PORTFOLIO, "tool": "review_rebalance",
     "title": "A rebalance with no target weights",
     "shows": "Refused rather than inferred. Deriving a target from the current "
              "holdings would make the portfolio its own benchmark.",
     "arguments": _run(idempotency_key="uc_rebal_2"),
     "expect": "protocol_or_needs_input"},

    {"id": "portfolio-no-execution", "group": "Portfolio", "server": PORTFOLIO,
     "tool": "execute_trade",
     "title": "There is no tool that places a trade",
     "shows": "Not disabled, not permissioned off — absent. A caller asking for "
              "one is refused at the boundary.",
     "arguments": {"lot_id": "lot_vti_b"},
     "expect": "UNSUPPORTED_SCOPE"},

    # --- household and UMA -------------------------------------------------
    {"id": "household-coordinated", "group": "Household & UMA",
     "server": HOUSEHOLD_SERVER, "tool": "coordinate_household",
     "title": "Two accounts, one shared budget",
     "shows": "Budget is reserved per tax unit as each account is served, so no "
              "two runs spend the same allowance.",
     "arguments": _run(cash_target="10000.00", gain_budget="15000.00",
                       netting_basis="net_gains", period="2026",
                       idempotency_key="uc_coord_1"),
     "expect": "completed_with_limitations"},

    {"id": "household-uma-wash", "group": "Household & UMA",
     "server": HOUSEHOLD_SERVER, "tool": "coordinate_household",
     "title": "One sleeve would wash another sleeve's loss",
     "shows": "Same account, same taxpayer, two managers. Neither did anything "
              "wrong and the loss would still be disallowed, so it is caught first.",
     "arguments": _run(subject_ref="acct_uma", document_ref=UMA,
                       cash_target="10000.00", gain_budget="15000.00",
                       netting_basis="net_gains", period="2026",
                       planned_purchases=[{"account_id": "acct_uma",
                                           "security_id": "ARKK", "quantity": "100",
                                           "trade_date": "2026-09-20",
                                           "unit_id": "sleeve_growth"}],
                       idempotency_key="uc_uma_1"),
     "expect": "completed_with_limitations"},

    {"id": "household-gap", "group": "Household & UMA", "server": HOUSEHOLD_SERVER,
     "tool": "coordinate_household",
     "title": "Falling short, and the ceiling it was measured against",
     "shows": "Sequential allocation depends on the order units are served, so the "
              "run reports an LP bound on what any allocation could have raised.",
     "arguments": _run(subject_ref="acct_a", document_ref=TWO_ACCOUNTS,
                       cash_target="30000.00", gain_budget="5000.00",
                       netting_basis="gross_gains", period="2026",
                       idempotency_key="uc_gap_1"),
     "expect": "INFEASIBLE_CONSTRAINTS"},

    {"id": "household-joint-optimal", "group": "Household & UMA",
     "server": HOUSEHOLD_SERVER, "tool": "optimize_household",
     "title": "Solved jointly, with a proof",
     "shows": "No ordering at all. Where the search exhausts, the run proves no other "
              "selection raises more cash within the budget — which the sequential "
              "coordinator can never say.",
     "arguments": _run(subject_ref="household_tu_patel", cash_target="10000.00",
                       gain_budget="15000.00", netting_basis="net_gains",
                       period="2026", idempotency_key="uc_joint_1"),
     "expect": "completed_with_limitations"},

    {"id": "household-joint-infeasible-is-proven", "group": "Household & UMA",
     "server": HOUSEHOLD_SERVER, "tool": "optimize_household",
     "title": "An unreachable target, proven unreachable",
     "shows": "Because each tax unit was searched exhaustively, the shortfall is a "
              "proof rather than a failure to find a plan.",
     "arguments": _run(subject_ref="household_tu_patel", cash_target="500000.00",
                       gain_budget="0", netting_basis="gross_gains", period="2026",
                       idempotency_key="uc_joint_2"),
     "expect": "INFEASIBLE_CONSTRAINTS"},

    {"id": "household-capabilities", "group": "Household & UMA",
     "server": HOUSEHOLD_SERVER, "tool": "get_capabilities",
     "title": "Which engine claims what",
     "shows": "Two engines on one server: the joint optimizer proves optimality where "
              "its search exhausts, the coordinator never does. The server states the "
              "difference rather than letting a coordinated result read as an optimal "
              "one.",
     "arguments": {},
     "expect": "completed"},
]

USE_CASES += [
    # --- estate and gift --------------------------------------------------
    {"id": "estate-within-exclusion", "group": "Estate & gift", "server": ESTATE,
     "tool": "calculate_estate_tax",
     "title": "An estate under the exclusion",
     "shows": "No federal estate tax at $12m against a $15m exclusion. A return may "
              "still be due, and several states tax far lower estates.",
     "arguments": {"gross_estate": "12000000"},
     "expect": "completed"},

    {"id": "estate-gifts-added-back", "group": "Estate & gift", "server": ESTATE,
     "tool": "calculate_estate_tax",
     "title": "Lifetime gifts are added back, not subtracted",
     "shows": "$2m of prior gifts does not simply shrink the estate: it is added to it "
              "before the rate schedule, so it pushes the estate into higher brackets.",
     "arguments": {"gross_estate": "20000000", "deductions": "500000",
                   "lifetime_taxable_gifts": "2000000"},
     "expect": "completed"},

    {"id": "estate-portability", "group": "Estate & gift", "server": ESTATE,
     "tool": "calculate_estate_tax",
     "title": "A deceased spouse's unused exclusion",
     "shows": "Doubles the shelter to $30m. Taken as given: portability needs a timely "
              "return on the first death, which this server cannot check.",
     "arguments": {"gross_estate": "28000000", "dsue_amount": "15000000"},
     "expect": "completed"},

    {"id": "estate-gift-splitting", "group": "Estate & gift", "server": ESTATE,
     "tool": "calculate_gift_exclusion",
     "title": "Two parents, three children",
     "shows": "$19,000 times three recipients times two donors shelters $114,000. "
              "Splitting needs both spouses to consent on a return.",
     "arguments": {"gift_amount": "100000", "recipients": "3", "donors": "2"},
     "expect": "completed"},

    {"id": "estate-noncitizen-spouse", "group": "Estate & gift", "server": ESTATE,
     "tool": "calculate_gift_exclusion",
     "title": "A gift to a non-citizen spouse",
     "shows": "The unlimited marital deduction does not apply, so a separate and much "
              "larger annual figure governs instead of the ordinary $19,000.",
     "arguments": {"gift_amount": "250000", "noncitizen_spouse": "yes"},
     "expect": "completed"},

    {"id": "estate-splitting-refused-for-spouse-gift", "group": "Estate & gift",
     "server": ESTATE, "tool": "calculate_gift_exclusion",
     "title": "Splitting a gift to your own spouse is refused",
     "shows": "The non-citizen spouse figure applies to one donor and one spouse. "
              "Multiplying it would invent an exclusion that does not exist.",
     "arguments": {"gift_amount": "250000", "noncitizen_spouse": "yes", "donors": "2"},
     "expect": "VALIDATION_FAILED"},

    {"id": "estate-fiduciary-compression", "group": "Estate & gift", "server": ESTATE,
     "tool": "calculate_fiduciary_tax",
     "title": "A trust hits the top rate at $16,000",
     "shows": "The compression that drives most distribution planning: an individual "
              "reaches 37% in the hundreds of thousands, a trust at $16,000.",
     "arguments": {"ordinary_income": "20000", "long_term_gain": "10000"},
     "expect": "completed"},

    # --- international ----------------------------------------------------
    {"id": "intl-feie-with-housing", "group": "International", "server": INTERNATIONAL,
     "tool": "calculate_feie",
     "title": "Exclusion plus the housing amount",
     "shows": "The housing amount is the excess over a 16% base, capped at 30%, not a "
              "deduction for everything spent on rent.",
     "arguments": {"foreign_earned_income": "180000", "housing_expenses": "45000"},
     "expect": "completed"},

    {"id": "intl-feie-prorated", "group": "International", "server": INTERNATIONAL,
     "tool": "calculate_feie",
     "title": "A partial qualifying period",
     "shows": "183 days prorates both the exclusion and the housing figures. A day "
              "count that is off by one changes the answer.",
     "arguments": {"foreign_earned_income": "180000", "qualifying_days": "183"},
     "expect": "completed"},

    {"id": "intl-feie-housing-below-base", "group": "International",
     "server": INTERNATIONAL, "tool": "calculate_feie",
     "title": "Housing below the base gives nothing",
     "shows": "The base is a floor, not a starting allowance. Spending under it "
              "produces no housing amount at all.",
     "arguments": {"foreign_earned_income": "180000", "housing_expenses": "15000"},
     "expect": "completed"},

    {"id": "intl-foreign-gift-individual", "group": "International",
     "server": INTERNATIONAL, "tool": "calculate_foreign_gift",
     "title": "$150,000 from a relative abroad",
     "shows": "Reportable, and not income. The threshold for individuals is the flat "
              "statutory $100,000, which is not indexed.",
     "arguments": {"amount_received": "150000", "source": "individual"},
     "expect": "completed"},

    {"id": "intl-foreign-gift-entity", "group": "International", "server": INTERNATIONAL,
     "tool": "calculate_foreign_gift",
     "title": "The same money from a foreign company",
     "shows": "A far lower, inflation-adjusted threshold applies, so $25,000 is "
              "reportable where it would not be from an individual.",
     "arguments": {"amount_received": "25000", "source": "corporation"},
     "expect": "completed"},

    {"id": "intl-rejects-bad-day-count", "group": "International",
     "server": INTERNATIONAL, "tool": "calculate_feie",
     "title": "An impossible qualifying period is refused",
     "shows": "400 days in a year is not a typo the server will absorb.",
     "arguments": {"foreign_earned_income": "180000", "qualifying_days": "400"},
     "expect": "VALIDATION_FAILED"},

    # --- business ---------------------------------------------------------
    {"id": "biz-179-phaseout", "group": "Business entity", "server": BUSINESS,
     "tool": "calculate_section_179",
     "title": "Spending that erodes its own deduction",
     "shows": "The dollar limit falls one-for-one with spending above the threshold, "
              "so deferring a purchase across the year end can restore it.",
     "arguments": {"cost_placed_in_service": "4500000"},
     "expect": "completed"},

    {"id": "biz-179-eliminated", "group": "Business entity", "server": BUSINESS,
     "tool": "calculate_section_179",
     "title": "Enough spending removes the election entirely",
     "shows": "Past a point there is no section 179 deduction at all, which is the "
              "opposite of what buying more usually does.",
     "arguments": {"cost_placed_in_service": "6700000"},
     "expect": "completed"},

    {"id": "biz-179-income-limited", "group": "Business entity", "server": BUSINESS,
     "tool": "calculate_section_179",
     "title": "The second limit, and its carryforward",
     "shows": "Capped at business income, with the excess carried forward rather than "
              "lost. Two different limits that are easily confused.",
     "arguments": {"cost_placed_in_service": "500000",
                   "business_taxable_income": "300000"},
     "expect": "completed"},

    {"id": "biz-entity-close-call", "group": "Business entity", "server": BUSINESS,
     "tool": "calculate_entity_comparison",
     "title": "C corporation versus pass-through, fully distributed",
     "shows": "$368,000 against $370,000 — close enough that payroll or state tax, "
              "neither of which is included, would decide it.",
     "arguments": {"business_income": "1000000", "owner_marginal_rate": "0.37"},
     "expect": "completed"},

    {"id": "biz-entity-retained", "group": "Business entity", "server": BUSINESS,
     "tool": "calculate_entity_comparison",
     "title": "The same income, nothing distributed",
     "shows": "Only the corporate layer is paid this year. The second layer is "
              "deferred, not avoided, and the comparison flips.",
     "arguments": {"business_income": "1000000", "owner_marginal_rate": "0.37",
                   "distribution_fraction": "0.00"},
     "expect": "completed"},

    {"id": "biz-rejects-rate-over-one", "group": "Business entity", "server": BUSINESS,
     "tool": "calculate_entity_comparison",
     "title": "A rate given as 37 instead of 0.37",
     "shows": "Refused rather than taxing the income at 3,700%. The fraction is "
              "range-checked at the boundary.",
     "arguments": {"business_income": "1000000", "owner_marginal_rate": "37"},
     "expect": "VALIDATION_FAILED"},

    # --- compliance -------------------------------------------------------
    {"id": "comp-file-vs-pay", "group": "Compliance", "server": COMPLIANCE,
     "tool": "calculate_filing_penalty",
     "title": "Three months late, nothing filed",
     "shows": "Failure to file runs at ten times the failure-to-pay rate, with the "
              "section 6651(c) offset applied so the combined rate is 5%, not 5.5%.",
     "arguments": {"unpaid_tax": "10000", "days_late": "95"},
     "expect": "completed"},

    {"id": "comp-filed-not-paid", "group": "Compliance", "server": COMPLIANCE,
     "tool": "calculate_filing_penalty",
     "title": "The same debt, but the return was filed",
     "shows": "$200 instead of $2,000. This is why 'file even if you cannot pay' is "
              "the standard advice, and the numbers make the case.",
     "arguments": {"unpaid_tax": "10000", "days_late": "95", "filed": "yes"},
     "expect": "completed"},

    {"id": "comp-minimum-penalty", "group": "Compliance", "server": COMPLIANCE,
     "tool": "calculate_filing_penalty",
     "title": "A small balance more than 60 days late",
     "shows": "The percentage addition would be $360, so the statutory floor applies "
              "instead. Small balances are not proportionally cheap.",
     "arguments": {"unpaid_tax": "2000", "days_late": "95"},
     "expect": "completed"},

    {"id": "comp-partnership-penalty", "group": "Compliance", "server": COMPLIANCE,
     "tool": "calculate_entity_penalty",
     "title": "A partnership that owes no tax at all",
     "shows": "$8,320 for eight partners and four months. The penalty is per owner per "
              "month and does not depend on tax owed.",
     "arguments": {"entity_type": "partnership", "owners": "8", "months_late": "4"},
     "expect": "completed"},

    {"id": "comp-safe-harbour-jump", "group": "Compliance", "server": COMPLIANCE,
     "tool": "calculate_safe_harbour",
     "title": "Income jumped; what must be paid in",
     "shows": "110% of last year's known tax beats 90% of this year's guess. The "
              "prior-year route is what protects a year like this.",
     "arguments": {"prior_year_tax": "80000", "current_year_projected_tax": "200000",
                   "prior_year_agi": "400000", "withholding_and_payments": "50000"},
     "expect": "completed"},

    {"id": "comp-safe-harbour-fall", "group": "Compliance", "server": COMPLIANCE,
     "tool": "calculate_safe_harbour",
     "title": "Income fell; paying last year's figure would overpay",
     "shows": "The current-year route governs when income drops. Taking the prior-year "
              "route out of habit ties up cash for a year.",
     "arguments": {"prior_year_tax": "90000", "current_year_projected_tax": "20000",
                   "prior_year_agi": "400000", "withholding_and_payments": "18000"},
     "expect": "completed"},

    {"id": "comp-rejects-negative-days", "group": "Compliance", "server": COMPLIANCE,
     "tool": "calculate_filing_penalty",
     "title": "A negative number of days late",
     "shows": "Refused rather than producing a negative penalty.",
     "arguments": {"unpaid_tax": "10000", "days_late": "-5"},
     "expect": "VALIDATION_FAILED"},

    # --- state and local --------------------------------------------------
    {"id": "salt-full-cap", "group": "State & local", "server": SALT,
     "tool": "calculate_salt_cap",
     "title": "Under the threshold: the full cap",
     "shows": "$40,400 of a $60,000 bill is deductible; the rest is simply lost and "
              "does not carry forward.",
     "arguments": {"state_and_local_tax_paid": "60000", "magi": "400000"},
     "expect": "completed"},

    {"id": "salt-phasing-down", "group": "State & local", "server": SALT,
     "tool": "calculate_salt_cap",
     "title": "Inside the phase-down range",
     "shows": "A slope, not a cliff: 30 cents per dollar of MAGI above the threshold. "
              "Income in this band costs more than its own marginal rate.",
     "arguments": {"state_and_local_tax_paid": "60000", "magi": "550000"},
     "expect": "completed"},

    {"id": "salt-at-the-floor", "group": "State & local", "server": SALT,
     "tool": "calculate_salt_cap",
     "title": "Past the floor",
     "shows": "The cap stops falling at $10,000 however high income goes, so the "
              "penalty on extra income ends there too.",
     "arguments": {"state_and_local_tax_paid": "60000", "magi": "700000"},
     "expect": "completed"},

    {"id": "salt-separate-filer", "group": "State & local", "server": SALT,
     "tool": "calculate_salt_cap",
     "title": "Married filing separately halves everything",
     "shows": "Cap, threshold and floor are all halved, which is easy to miss when "
              "comparing a joint and separate return.",
     "arguments": {"state_and_local_tax_paid": "30000", "magi": "200000",
                   "status": "MFS"},
     "expect": "completed"},

    {"id": "salt-no-state-is-covered", "group": "State & local", "server": SALT,
     "tool": "calculate_state_coverage",
     "title": "Asking what California charges",
     "shows": "No state pack is bound, so no state's own tax is computed. The answer "
              "names what a state pack would have to contain.",
     "arguments": {"state": "CA"},
     "expect": "completed"},
]

USE_CASES += [
    {"id": "muni-naive-understates", "group": "Municipal bonds", "server": MUNI,
     "tool": "calculate_muni_yield",
     "title": "What a taxable bond would have to pay",
     "shows": "7.01% against the 5.56% the federal-only formula gives. State tax and "
              "the 3.8% surtax are both avoided, and both are routinely left out.",
     "arguments": {"municipal_yield": "0.035", "federal_rate": "0.37",
                   "state_rate": "0.093", "state_exempt": "yes",
                   "niit_applies": "yes"},
     "expect": "completed"},

    {"id": "muni-out-of-state", "group": "Municipal bonds", "server": MUNI,
     "tool": "calculate_muni_yield",
     "title": "The same bond, bought out of state",
     "shows": "Federally exempt, state taxable. 5.91% instead of 7.01% — a different "
              "proposition that looks identical on a screen.",
     "arguments": {"municipal_yield": "0.035", "federal_rate": "0.37",
                   "state_rate": "0.093", "state_exempt": "no",
                   "niit_applies": "yes"},
     "expect": "completed"},

    {"id": "muni-private-activity-in-amt", "group": "Municipal bonds", "server": MUNI,
     "tool": "calculate_muni_yield",
     "title": "A private activity bond held by someone in AMT",
     "shows": "The interest is a preference item, so nothing is avoided and the "
              "advantage is zero. The answer reverses rather than shades.",
     "arguments": {"municipal_yield": "0.035", "federal_rate": "0.37",
                   "state_rate": "0.093", "niit_applies": "yes",
                   "private_activity": "yes", "amt_applies": "yes"},
     "expect": "completed"},

    {"id": "muni-2009-window", "group": "Municipal bonds", "server": MUNI,
     "tool": "calculate_muni_yield",
     "title": "The same bond, issued in 2009",
     "shows": "Bonds issued in 2009 and 2010 are excepted from the preference, so the "
              "advantage survives AMT. A narrow window with a total effect.",
     "arguments": {"municipal_yield": "0.035", "federal_rate": "0.37",
                   "state_rate": "0.093", "niit_applies": "yes",
                   "private_activity": "yes", "amt_applies": "yes",
                   "issue_year": "2009"},
     "expect": "completed"},

    {"id": "muni-discount-is-ordinary", "group": "Municipal bonds", "server": MUNI,
     "tool": "calculate_muni_discount",
     "title": "Bought below par, taxed at the top rate",
     "shows": "A $50 discount against a $25 threshold: accrued market discount is "
              "ordinary income on disposition. The interest stays exempt; the "
              "discount does not.",
     "arguments": {"purchase_price": "950", "redemption_price": "1000",
                   "years_to_maturity": "10"},
     "expect": "completed"},

    {"id": "muni-discount-de-minimis", "group": "Municipal bonds", "server": MUNI,
     "tool": "calculate_muni_discount",
     "title": "A smaller discount stays capital",
     "shows": "$20 against the same $25 threshold is treated as zero, so the gain on "
              "redemption is capital. The margin is what a buyer can act on.",
     "arguments": {"purchase_price": "980", "redemption_price": "1000",
                   "years_to_maturity": "10"},
     "expect": "completed"},

    {"id": "muni-discount-short-maturity", "group": "Municipal bonds", "server": MUNI,
     "tool": "calculate_muni_discount",
     "title": "The same discount on a two-year bond",
     "shows": "The threshold is a quarter point per year, so two years allows only "
              "$5. Maturity drives this more than price does.",
     "arguments": {"purchase_price": "980", "redemption_price": "1000",
                   "years_to_maturity": "2"},
     "expect": "completed"},

    {"id": "muni-premium-no-loss", "group": "Municipal bonds", "server": MUNI,
     "tool": "calculate_muni_premium",
     "title": "Paid 105, redeems at 100, no loss",
     "shows": "Amortization is mandatory with no deduction, and basis falls to the "
              "redemption price. The expectation of a loss at maturity is the thing "
              "to correct before they buy.",
     "arguments": {"purchase_price": "1050", "redemption_price": "1000",
                   "years_to_maturity": "10"},
     "expect": "completed"},

    {"id": "muni-rejects-percentage-rate", "group": "Municipal bonds", "server": MUNI,
     "tool": "calculate_muni_yield",
     "title": "A rate given as 37 instead of 0.37",
     "shows": "Refused rather than producing a yield nobody could sanity-check.",
     "arguments": {"municipal_yield": "0.035", "federal_rate": "37"},
     "expect": "VALIDATION_FAILED"},

    {"id": "muni-capabilities", "group": "Municipal bonds", "server": MUNI,
     "tool": "get_muni_capabilities",
     "title": "What it will not decide",
     "shows": "Whether a bond is a private activity bond, what any state charges, and "
              "credit or call risk. Munis are also outside every portfolio engine.",
     "arguments": {},
     "expect": "completed"},
]

GROUPS = ["Federal", "Portfolio", "Household & UMA", "Estate & gift", "International",
          "Business entity", "Compliance", "State & local",
          "Municipal bonds"]


def by_id(case_id: str) -> Dict[str, Any]:
    found = next((c for c in USE_CASES if c["id"] == case_id), None)
    if found is None:
        raise KeyError(case_id)
    return found


def summary() -> List[Dict[str, Any]]:
    """What the console lists, without the arguments."""
    return [{**{k: c[k] for k in ("id", "group", "server", "tool", "title",
                                 "shows", "expect")},
             "depends_on": c.get("depends_on")}
            for c in USE_CASES]
