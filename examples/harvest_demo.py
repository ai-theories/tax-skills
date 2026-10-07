"""End-to-end harvest review against the synthetic Patel fixture.

Run: PYTHONPATH=src python3 examples/harvest_demo.py
"""
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from taxagent.application.bootstrap import build_service
from taxagent.connectors.uploaded_files import UploadedFileSource
from decimal import Decimal

from taxagent.domain.identities import Account, Principal, Registration, Subject
from taxagent.domain.sleeves import PlannedAcquisition

TENANT = "tenant_ria_1"
PRICES = {"VTI": "260.00", "AAPL": "205.00", "ARKK": "62.00", "TLT": "88.00",
          "AAA": "60.00", "BBB": "200.00"}
AS_OF = date(2026, 9, 15)


def intent(cash, netting_basis="net_gains", period="2026", budget="15000.00"):
    return {
        "schema_version": "1.0", "request_id": "req_1042",
        "intent": "harvest_review", "mode": "analysis_only",
        "subject_ref": "acct_schwab_joint",
        "objectives": [{"type": "raise_cash", "amount": cash, "currency": "USD",
                        "withdrawal_account": "acct_schwab_joint"}],
        "constraints": [{"type": "realized_gain_budget", "amount": budget,
                         "currency": "USD", "netting_basis": netting_basis,
                         "period": period}],
    }


def main():
    accounts = [
        Account("acct_schwab_joint", TENANT, "tu_patel", Registration.JOINT_TAXABLE,
                "Schwab", "owner_patel"),
        Account("acct_fid_roth", TENANT, "tu_patel", Registration.ROTH_IRA,
                "Fidelity", "owner_patel"),
        Account("acct_spouse_ext", TENANT, "tu_patel", Registration.TAXABLE,
                "Vanguard", "owner_spouse"),
        Account("acct_uma", TENANT, "tu_patel", Registration.TAXABLE, "Schwab", "owner_patel"),
        Account("acct_a", TENANT, "tu_x", Registration.TAXABLE, "Schwab", "owner_x"),
        Account("acct_b", TENANT, "tu_x", Registration.TAXABLE, "Fidelity", "owner_x"),
    ]
    subjects = [
        Subject("acct_schwab_joint", TENANT, "account", ("acct_schwab_joint",), ("tu_patel",)),
        Subject("household_patel", TENANT, "household",
                ("acct_schwab_joint", "acct_fid_roth"), ("tu_patel",)),
        Subject("acct_uma", TENANT, "account", ("acct_uma",), ("tu_patel",)),
        Subject("household_x", TENANT, "household", ("acct_a", "acct_b"), ("tu_x",)),
    ]
    service = build_service(subjects, accounts)
    principal = Principal("adv_1", TENANT, frozenset({"analysis"}),
                          frozenset({"acct_schwab_joint", "acct_fid_roth", "acct_uma",
                                     "acct_a", "acct_b"}))
    source = UploadedFileSource().load_file(
        TENANT, os.path.join(ROOT, "tests/fixtures/patel_household.json"))

    def run(label, subject_ref, payload, key, template="harvest-review.v1",
            data=None, planned=()):
        print(f"\n{'=' * 72}\n{label}\n{'=' * 72}")
        result = service.start_analysis(principal, "req_1042", template, subject_ref,
                                        data or source, AS_OF, PRICES, payload, key,
                                        planned_acquisitions=planned)
        print(f"status: {result['status']}  code: {result.get('code', '-')}")
        if result["status"] == "needs_input":
            print(f"unresolved: {result['unresolved_fields']}")
        if result.get("supported_alternative"):
            print(f"alternative: {result['supported_alternative']}")
        if result.get("data", {}).get("diagnostics", {}).get("cash_shortfall"):
            print(f"shortfall: {result['data']['diagnostics']['cash_shortfall']}")
        for limitation in result.get("limitations", []):
            detail = limitation["refs"].get("account_id") or limitation["refs"].get("lot_id") or ""
            print(f"  limitation: {limitation['code']}"
                  + (f" ({detail})" if detail else ""))
        return result

    run("1. Household scope under harvest-review — no joint optimizer exists",
        "household_patel", intent("50000.00"), "demo_household")

    run("2. Gain budget with no netting basis or period",
        "acct_schwab_joint", intent("50000.00", None, None), "demo_ambiguous")

    run("3. $50,000 cash target — cannot be met within the gain budget",
        "acct_schwab_joint", intent("50000.00"), "demo_infeasible")

    achievable = run("4. $10,000 cash target — completes, with limitations",
                     "acct_schwab_joint", intent("10000.00"), "demo_ok")

    coordinated = run(
        "5. Same household under household-coordination.v1 — coordinated, not optimal",
        "household_patel", intent("10000.00"), "demo_coordinated",
        template="household-coordination.v1")
    if coordinated.get("evidence_ref"):
        pack = service.store.get_artifact(TENANT, coordinated["evidence_ref"])
        diagnostics = pack["results"]["candidate"]["diagnostics"]
        print(f"coordination: {diagnostics['coordination']}  "
              f"optimality_claimed: {diagnostics['optimality_claimed']}")
        print(f"order rule:   {diagnostics['order_rule']}")
        print(f"order:        {diagnostics['allocation_order']}")
        print(f"budget by tax unit: {diagnostics['budget_consumed_by_tax_unit']}")
        gap = diagnostics["optimality_gap"]
        if gap["provably_optimal"]:
            print("optimality:   no reallocation of this budget raises more cash")
        else:
            print(f"optimality:   achieved {gap['achieved_cash']} of a {gap['bound_cash']} "
                  f"ceiling — gap {gap['gap']} ({gap['gap_percent']})")

    uma_source = UploadedFileSource().load_file(
        TENANT, os.path.join(ROOT, "tests/fixtures/patel_uma.json"))
    planned = [PlannedAcquisition("acct_uma", "ARKK", Decimal("300"),
                                  date(2026, 9, 25), "sleeve_beta")]
    uma = run("6. UMA: sleeve_beta plans to buy ARKK while sleeve_alpha would harvest it",
              "acct_uma", intent("10000.00"), "demo_uma",
              template="household-coordination.v1", data=uma_source, planned=planned)
    if uma.get("evidence_ref"):
        pack = service.store.get_artifact(TENANT, uma["evidence_ref"])
        diagnostics = pack["results"]["candidate"]["diagnostics"]
        for conflict in diagnostics["cross_unit_conflicts_prevented"]:
            print(f"prevented: {conflict['selling_unit']} selling {conflict['lot_id']} "
                  f"would be washed by {conflict['conflicting_units']}")
        for unit in diagnostics["per_unit"]:
            if "skipped" in unit:
                print(f"  {unit['unit_id']:<16} skipped: {unit['skipped']}")
            else:
                print(f"  {unit['unit_id']:<16} trades={unit['trades']} "
                      f"cash={unit['cash_raised']:>10} budget={unit['budget_consumed']:>10}")

    order_source = UploadedFileSource().load_file(
        TENANT, os.path.join(ROOT, "tests/fixtures/order_dependence.json"))
    starved = run(
        "7. Order dependence measured: acct_a is served first and spends the budget badly",
        "household_x", intent("30000.00", "gross_gains", budget="5000.00"), "demo_gap",
        template="household-coordination.v1", data=order_source)
    if starved.get("evidence_ref"):
        pack = service.store.get_artifact(TENANT, starved["evidence_ref"])
        gap = pack["results"]["candidate"]["diagnostics"]["optimality_gap"]
        print(f"achieved {gap['achieved_cash']} against a {gap['bound_cash']} ceiling")
        print(f"gap {gap['gap']} ({gap['gap_percent']}) — "
              f"provably_optimal={gap['provably_optimal']}")
        print(f"{gap['interpretation']}")

    print()
    print(achievable["data"]["report_markdown"])


if __name__ == "__main__":
    main()
