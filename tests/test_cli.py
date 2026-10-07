"""The CLI is the surface a person actually touches."""
import json

import pytest

from taxagent.cli import main


def run(capsys, *argv):
    code = main(list(argv))
    return code, capsys.readouterr()


def test_federal_tax_prints_a_readable_estimate(capsys):
    code, out = run(capsys, "federal", "tax", "--income", "185000", "--status", "MFJ")
    assert code == 0
    assert "Federal estimate — MFJ" in out.out
    assert "TOTAL" in out.out
    assert "Not tax advice" in out.out


def test_json_output_is_machine_readable(capsys):
    code, out = run(capsys, "federal", "tax", "--income", "185000", "--json")
    payload = json.loads(out.out)
    assert code == 0
    assert payload["total_tax"] == "23040.00"
    assert payload["rule_bundle_ref"] == "us-federal-income-2026.v1"
    assert "disclaimer" in payload


def test_capgains_shows_the_band_split(capsys):
    code, out = run(capsys, "federal", "capgains", "--gain", "100000",
                    "--ordinary", "90000", "--json")
    payload = json.loads(out.out)
    assert code == 0
    assert payload["tax"] == "13665.00"
    assert len(payload["bands"]) == 2


def test_niit_command(capsys):
    _, out = run(capsys, "federal", "niit", "--nii", "45000", "--magi", "260000", "--json")
    assert json.loads(out.out)["tax"] == "380.00"


def test_loss_command(capsys):
    _, out = run(capsys, "federal", "loss", "--loss", "12000", "--json")
    payload = json.loads(out.out)
    assert payload["deductible_this_year"] == "3000.00"
    assert payload["carryforward"] == "9000.00"


def test_capabilities_lists_what_can_be_asked(capsys):
    code, out = run(capsys, "capabilities", "--json")
    entries = json.loads(out.out)
    assert code == 0
    assert {e["command"] for e in entries} == {
        "federal tax", "federal capgains", "federal niit", "federal loss",
        "federal amt", "federal qbi"}
    for entry in entries:
        assert entry["typical_requests"] and entry["inputs"]


def test_capabilities_states_what_is_not_covered(capsys):
    _, out = run(capsys, "capabilities")
    assert "Not covered here" in out.out
    assert "state tax" in out.out


def test_amt_command(capsys):
    _, out = run(capsys, "federal", "amt", "--amti", "600000",
                 "--regular-tax", "120000", "--json")
    payload = json.loads(out.out)
    assert payload["tentative_minimum_tax"] == "123854.00"
    assert payload["amt_payable"] == "3854.00"


def test_qbi_command(capsys):
    _, out = run(capsys, "federal", "qbi", "--qbi", "200000",
                 "--taxable-income", "600000", "--wages", "50000", "--json")
    payload = json.loads(out.out)
    assert payload["deduction"] == "25000.00"
    assert payload["limit_applied"] == "wage_and_property_limit"


def test_qbi_sstb_flag_changes_the_answer(capsys):
    _, plain = run(capsys, "federal", "qbi", "--qbi", "200000",
                   "--taxable-income", "600000", "--wages", "50000", "--json")
    _, sstb = run(capsys, "federal", "qbi", "--qbi", "200000",
                  "--taxable-income", "600000", "--wages", "50000", "--sstb", "--json")
    assert json.loads(plain.out)["deduction"] == "25000.00"
    assert json.loads(sstb.out)["deduction"] == "0.00"


def test_rules_command_names_the_pack(capsys):
    _, out = run(capsys, "rules")
    assert "us-federal-income-2026.v1" in out.out
    assert "await sign-off" in out.out


def test_lowercase_status_is_accepted(capsys):
    code, out = run(capsys, "federal", "tax", "--income", "100000", "--status", "single",
                    "--json")
    assert code == 0
    assert json.loads(out.out)["filing_status"] == "SINGLE"


def test_uncovered_year_exits_non_zero(capsys):
    code, out = run(capsys, "federal", "tax", "--income", "100000", "--year", "2019")
    assert code == 2
    assert "WRONG_RULE_YEAR" in out.err


def test_bad_number_exits_non_zero(capsys):
    code, out = run(capsys, "federal", "tax", "--income", "not-a-number")
    assert code == 2
    assert "error" in out.err
