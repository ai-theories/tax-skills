"""Tax rules live in a reviewed, versioned pack — never in source.

A constant in the calculation path has no version, no effective date and no
authority, and nothing catches it drifting. That is how an engine ends up
computing a confident answer from a stale number.
"""
import os
import re
from datetime import date

import pytest
import yaml

from taxagent.application.bootstrap import PROJECT_ROOT
from taxagent.decisions.rule_registry import (RulePackError, RuleRegistry, TaxRules,
                                              default_rules)
from taxagent.domain import codes

SRC = os.path.join(PROJECT_ROOT, "src", "taxagent")
RULES_DIR = os.path.join(PROJECT_ROOT, "rules", "tax")

#: module -> patterns that must not reappear once the rule moved to the pack
FORBIDDEN = {
    "tax/wash_sale_screen.py": [
        (r"^WINDOW_DAYS\s*=", "the 61-day window length"),
        (r"days\s*=\s*30", "a literal 30-day window"),
        (r"\.is_retirement", "registration treatment decided outside the pack"),
    ],
    "portfolio/lot_accounting.py": [
        (r"add_years\([a-z_]+,\s*1\)", "a literal one-year holding period"),
    ],
}


@pytest.mark.parametrize("module,patterns", sorted(FORBIDDEN.items()))
def test_no_tax_rule_is_hardcoded_in_source(module, patterns):
    source = open(os.path.join(SRC, module), encoding="utf-8").read()
    for pattern, description in patterns:
        assert not re.search(pattern, source, re.MULTILINE), (
            f"{module} reintroduced {description}; it belongs in the rule pack")


def test_rule_bearing_modules_read_the_registry():
    for module in FORBIDDEN:
        source = open(os.path.join(SRC, module), encoding="utf-8").read()
        assert "rule_registry" in source, f"{module} does not resolve rules from the pack"


# --- the pack's own content ---------------------------------------------

def test_pack_values_match_their_authorities():
    rules = default_rules(2026)
    assert rules.wash_sale_window_days == 30                    # IRC 1091(a)
    assert rules.long_term_holding_period_years == 1            # IRC 1222(3)
    assert rules.permanently_disallowing_registrations == frozenset(
        {"TRADITIONAL_IRA", "ROTH_IRA", "QUALIFIED_PLAN"})      # Rev. Rul. 2008-5
    assert rules.replacement_matching_basis == "exact_security_identifier"


def test_every_rule_cites_an_authority_and_an_effective_date():
    path = os.path.join(RULES_DIR, "parameter-packs", "us-federal-investment-rules.v1.yaml")
    pack = yaml.safe_load(open(path, encoding="utf-8"))
    for name, rule in pack["rules"].items():
        assert rule.get("authority"), f"{name} has no authority"
        assert rule.get("legal_effective_from"), f"{name} has no effective date"
        date.fromisoformat(rule["legal_effective_from"])


def test_every_cited_authority_appears_in_the_source_index():
    index = open(os.path.join(RULES_DIR, "source-index", "README.md"), encoding="utf-8").read()
    for authority in default_rules(2026).authorities.values():
        for citation in authority.split(";"):
            assert citation.strip() in index, f"{citation.strip()} is not in the source index"


# --- registry behaviour --------------------------------------------------

def _write_pack(tmp_path, **overrides):
    rules = {
        "wash_sale_window_days": {
            "value": overrides.get("window", 30),
            "legal_effective_from": overrides.get("effective", "1921-01-01"),
            "authority": "IRC section 1091(a)"},
        "long_term_holding_period_years": {
            "value": overrides.get("years", 1), "legal_effective_from": "1977-01-01",
            "authority": "IRC section 1222(3)"},
        "permanently_disallowing_registrations": {
            "value": overrides.get("registrations", ["TRADITIONAL_IRA", "ROTH_IRA"]),
            "legal_effective_from": "2008-01-15", "authority": "Rev. Rul. 2008-5"},
        "replacement_matching_basis": {
            "value": "exact_security_identifier", "legal_effective_from": "1921-01-01",
            "authority": "IRC section 1091(a)"},
    }
    pack = {"schema_version": "1.0", "bundle_id": "test.v1", "jurisdiction": "US_FEDERAL",
            "tax_years": overrides.get("years_covered", [2026]),
            "status": overrides.get("status", "reviewed"),
            "knowledge_time": "2026-01-01T00:00:00Z", "rules": rules}
    packs = tmp_path / "parameter-packs"
    manifests = tmp_path / "manifests"
    packs.mkdir(parents=True, exist_ok=True)
    manifests.mkdir(parents=True, exist_ok=True)
    (packs / "test.v1.yaml").write_text(yaml.safe_dump(pack))
    (manifests / "index.yaml").write_text(yaml.safe_dump({
        "schema_version": "1.0",
        "default_bundles": {"US_FEDERAL": {y: "test.v1" for y in pack["tax_years"]}},
        "bundles": [{"bundle_id": "test.v1", "file": "test.v1.yaml", "status": pack["status"]}],
    }))
    return RuleRegistry.load(str(tmp_path))


def test_unreviewed_pack_is_refused(tmp_path):
    registry = _write_pack(tmp_path, status="draft")
    with pytest.raises(RulePackError) as excinfo:
        registry.resolve(2026)
    assert "reviewed" in str(excinfo.value)


def test_uncovered_year_is_refused(tmp_path):
    registry = _write_pack(tmp_path, years_covered=[2026])
    with pytest.raises(RulePackError) as excinfo:
        registry.resolve(2019)
    assert excinfo.value.code == codes.WRONG_RULE_YEAR


def test_rule_not_yet_effective_is_refused(tmp_path):
    registry = _write_pack(tmp_path, effective="2030-01-01")
    with pytest.raises(RulePackError) as excinfo:
        registry.resolve(2026, as_of=date(2026, 9, 30))
    assert "takes effect" in str(excinfo.value)


def test_manifest_and_pack_ids_must_agree(tmp_path):
    _write_pack(tmp_path)
    manifest_path = tmp_path / "manifests" / "index.yaml"
    manifest = yaml.safe_load(manifest_path.read_text())
    manifest["bundles"][0]["bundle_id"] = "mismatched.v1"
    manifest_path.write_text(yaml.safe_dump(manifest))
    with pytest.raises(RulePackError):
        RuleRegistry.load(str(tmp_path))


def test_hash_tracks_pack_content(tmp_path):
    baseline = _write_pack(tmp_path).resolve(2026)
    changed = _write_pack(tmp_path, window=45).resolve(2026)
    # A different rule value is a different bundle hash, or "pinned" means nothing.
    assert baseline.bundle_hash != changed.bundle_hash
    assert changed.wash_sale_window_days == 45


# --- the pack actually drives the calculations --------------------------

def _loss_snapshot(as_of=date(2026, 9, 30), replacement_days=40):
    from taxagent.domain.coverage import CoverageInterval
    from taxagent.domain.identities import Account, Registration
    from taxagent.domain.money import Money
    from taxagent.domain.tax_lots import Action, BasisSource, Transaction, make_lot
    from taxagent.portfolio.snapshot_builder import build_snapshot
    from datetime import timedelta
    from decimal import Decimal

    sale_date = date(2026, 6, 15)
    accounts = [
        Account("acct_tax", "tenant_ria_1", "tu_1", Registration.TAXABLE, "Schwab", "o"),
        Account("acct_roth", "tenant_ria_1", "tu_1", Registration.ROTH_IRA, "Fidelity", "o"),
    ]
    lots = [make_lot("lot_loss", "acct_tax", "ARKK", "100", date(2025, 1, 2),
                     Money.of("12000.00"), BasisSource.CUSTODIAN_COVERED, True)]
    txns = [Transaction("t1", "acct_roth", "ARKK", Action.BUY,
                        sale_date + timedelta(days=replacement_days), Decimal("100"),
                        Money.of("6200.00"))]
    coverage = [CoverageInterval(a.account_id, date(2024, 1, 1), as_of, "src") for a in accounts]
    return build_snapshot("tenant_ria_1", "acct_tax", as_of, accounts, lots, txns,
                          coverage, ["fx"]), sale_date


def _dispositions(snapshot, sale_date):
    from decimal import Decimal

    from taxagent.domain.money import Money
    from taxagent.tax.lot_gain_loss import ProposedSale, calculate_gain_loss

    return calculate_gain_loss(
        snapshot,
        [ProposedSale("acct_tax", "ARKK", Decimal("100"), Money.of("62.00"),
                      lot_id="lot_loss", sale_date=sale_date)],
        rules=default_rules(2026)).dispositions


def test_screener_window_comes_from_the_pack(tmp_path):
    from taxagent.tax.wash_sale_screen import screen_wash_sales

    snapshot, sale_date = _loss_snapshot(replacement_days=40)
    dispositions = _dispositions(snapshot, sale_date)

    narrow = screen_wash_sales(snapshot, dispositions, rules=default_rules(2026))
    wide = screen_wash_sales(snapshot, dispositions,
                             rules=_write_pack(tmp_path, window=45).resolve(2026))
    # A purchase 40 days out is outside a 30-day window and inside a 45-day one.
    assert narrow.screens[0].matched_quantity == 0
    assert wide.screens[0].matched_quantity == 100


def test_permanent_disallowance_set_comes_from_the_pack(tmp_path):
    from taxagent.tax.wash_sale_screen import screen_wash_sales

    snapshot, sale_date = _loss_snapshot(replacement_days=10)
    dispositions = _dispositions(snapshot, sale_date)

    with_roth = screen_wash_sales(snapshot, dispositions, rules=default_rules(2026))
    without_roth = screen_wash_sales(
        snapshot, dispositions,
        rules=_write_pack(tmp_path, registrations=["TRADITIONAL_IRA"]).resolve(2026))
    assert with_roth.screens[0].matches[0].treatment == codes.PERMANENT_DISALLOWANCE
    # Drop ROTH_IRA from the reviewed set and the same facts get basis adjustment.
    assert without_roth.screens[0].matches[0].treatment == codes.BASIS_ADJUSTMENT


def test_holding_period_threshold_comes_from_the_pack(tmp_path):
    from taxagent.portfolio.lot_accounting import Character, holding_period

    acquired, sold = date(2025, 1, 15), date(2026, 6, 15)   # ~17 months
    assert holding_period(acquired, sold, default_rules(2026)) is Character.LONG_TERM
    two_year = _write_pack(tmp_path, years=2).resolve(2026)
    assert holding_period(acquired, sold, two_year) is Character.SHORT_TERM


def test_evidence_pins_the_rule_bundle(service, principal, source_data, prices, harvest_intent):
    envelope = service.start_analysis(
        principal, "req_rules", "harvest-review.v1", "acct_schwab_joint", source_data,
        date(2026, 9, 15), prices, harvest_intent, "idem_rules")
    package = service.store.get_artifact(principal.tenant_id, envelope["evidence_ref"])
    manifest = package["engine_manifest"]
    rules = default_rules(2026)
    # Previously always empty: the package claimed pinned rules and pinned nothing.
    assert manifest["rule_bundle_ref"] == rules.bundle_ref
    assert manifest["rule_bundle_hash"] == rules.bundle_hash
    assert manifest["rule_bundle_ref"]
    assert package["results"]["wash_sale"]["rule_bundle_ref"] == rules.bundle_ref
    assert package["results"]["gain_loss"]["rule_bundle_ref"] == rules.bundle_ref


def test_run_is_blocked_when_no_pack_covers_the_year(service, principal, source_data, prices,
                                                     harvest_intent):
    envelope = service.start_analysis(
        principal, "req_old", "harvest-review.v1", "acct_schwab_joint", source_data,
        date(2019, 9, 15), prices, harvest_intent, "idem_old")
    assert envelope["status"] == "blocked"
    assert envelope["code"] == codes.WRONG_RULE_YEAR
