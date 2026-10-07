"""Tool dispatch for the stateless calculation servers.

Six domains work the same way: the caller supplies figures, the engine returns
the tax on those figures, and nothing is stored. None of them holds client
data, so none needs a tenant, a principal, an entitlement or a document.

One dispatcher serves all of them, parameterised by domain. Six copies would
be six places for the boundary language to drift, and the boundary — what each
server will not compute — is the part that matters most here.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..decisions.rule_registry import RulePackError, domain_rules
from ..domain import codes
from ..domain.money import MoneyError
from ..tax import calculators
from . import result_envelopes as envelopes

# What each server declines, in its own words. Stated per domain because a
# generic "this is an estimate" tells a caller nothing about where the edge is.
BOUNDARIES: Dict[str, Dict[str, Any]] = {
    "federal": {
        "title": "US federal income tax over figures you supply",
        "rule_domain": None,
        "not_computed": [
            "A return. AMT and the section 199A deduction are computed separately and "
            "are not folded into one liability.",
            "Credits, self-employment tax and the additional Medicare tax.",
            "State and local tax.",
        ],
    },
    "estate": {
        "title": "Federal estate tax, gift exclusions and fiduciary income tax",
        "rule_domain": "estate",
        "not_computed": [
            "Whether property belongs in the gross estate, and what a closely held "
            "interest is worth. Both are facts, and both change the answer more than "
            "the rate schedule does.",
            "Generation-skipping transfer tax, and valuation discounts.",
            "State estate and inheritance taxes, which several states impose at far "
            "lower thresholds than the federal exclusion.",
            "Whether a portability election was made: a deceased spouse's unused "
            "exclusion is taken as given.",
        ],
    },
    "international": {
        "title": "Foreign earned income exclusion and foreign gift reporting",
        "rule_domain": "international",
        "not_computed": [
            "Eligibility for the exclusion. It requires a tax home abroad and either "
            "bona fide residence or physical presence, which are facts about where "
            "someone lived and worked.",
            "The foreign tax credit, which is often worth more than the exclusion and "
            "cannot be claimed on the same income.",
            "GILTI, subpart F, PFIC, treaty positions and FBAR.",
            "High-cost location housing limits.",
        ],
    },
    "business": {
        "title": "Section 179 expensing and entity-level comparison",
        "rule_domain": "business",
        "not_computed": [
            "Entity choice. The comparison is arithmetic over rates you supply; the "
            "decision turns on exit plans, qualified small business stock, payroll, "
            "fringe benefits and the cost of changing later.",
            "Bonus depreciation, the section 163(j) interest limitation, basis and "
            "at-risk limits, and recapture on disposition.",
            "Reasonable compensation, which is usually the larger number in an S "
            "corporation and is a facts-and-circumstances question.",
            "State entity-level taxes and franchise taxes.",
        ],
    },
    "compliance": {
        "title": "Filing penalties and the estimated tax safe harbour",
        "rule_domain": "compliance",
        "not_computed": [
            "Interest. The underpayment rate is set quarterly and compounds daily, so "
            "no rate is stored here and these figures are penalties only.",
            "Reasonable cause, first-time abatement and the fraud rate, any of which "
            "changes the result substantially.",
            "Whether estimated payments were timely by quarter, as opposed to "
            "sufficient in total.",
        ],
    },
    "muni": {
        "title": "Municipal bond tax treatment",
        "rule_domain": "muni",
        "not_computed": [
            "Whether a bond is a private activity bond. That is a fact about the "
            "issue, and it decides whether the interest is an AMT preference.",
            "Any state's treatment of its own or another state's bonds. The holder's "
            "state rate is supplied by the caller, not looked up.",
            "Credit risk, call features, duration and liquidity, none of which a "
            "yield comparison sees.",
            "Original issue discount accrual schedules, and the constant-yield "
            "premium schedule the statute requires: amortization is shown "
            "straight-line, which reaches the same place at maturity.",
            "The effect of exempt interest on how much Social Security is taxable, "
            "which is real and is why 'tax-free' overstates it.",
        ],
    },
    "salt": {
        "title": "The federal cap on deducting state and local tax",
        "rule_domain": "salt",
        "not_computed": [
            "Any state's own income tax. No state pack is bound in this deployment, "
            "and the cap is a federal deduction limit, not what a state charges.",
            "Residency, domicile, part-year allocation and reciprocity.",
            "Pass-through entity tax workarounds, which are a state question.",
            "Whether itemizing beats the standard deduction at all.",
        ],
    },
}


def tool_name(calc_id: str) -> str:
    return f"calculate_{calc_id}"


class CalculatorDispatcher:
    """One stateless calculation server, for one domain."""

    def __init__(self, domain: str, tax_year: int = 2026):
        if domain not in BOUNDARIES:
            raise KeyError(domain)
        self.domain = domain
        self.tax_year = tax_year
        self.capability_tool = f"get_{domain}_capabilities"
        self.calculators = calculators.ids_for(domain)
        self.registered = [tool_name(c) for c in self.calculators]
        self.registered.append(self.capability_tool)

    def list_tools(self) -> List[Dict[str, Any]]:
        tools = []
        for calc_id in self.calculators:
            spec = calculators.spec(calc_id)
            tools.append({
                "name": tool_name(calc_id),
                "description": f"{spec['title']}. {spec['summary']} "
                               f"Tax year {self.tax_year}, US federal. Estimate over the "
                               f"figures supplied; not a return and not tax advice.",
                "inputSchema": calculators.input_schema(calc_id),
            })
        tools.append({
            "name": self.capability_tool,
            "description": "What this server computes, what it will not, and which "
                           "published source its figures were verified against.",
            "inputSchema": {"type": "object", "properties": {},
                            "additionalProperties": False},
        })
        return tools

    def call(self, name: str, arguments: Dict[str, Any], request_id: str) -> Dict[str, Any]:
        if name not in self.registered:
            return envelopes.blocked(
                request_id, codes.UNSUPPORTED_SCOPE,
                f"Tool '{name}' is not registered in this deployment.")
        if name == self.capability_tool:
            return envelopes.completed(request_id, data=self._capabilities())

        calc_id = name[len("calculate_"):]
        try:
            result = calculators.run(calc_id, arguments)
        except (MoneyError, RulePackError, ValueError, ArithmeticError) as exc:
            # A figure the caller supplied, not a server fault. Returned as a
            # blocked envelope so an agent can repair the input and retry
            # rather than treating it as an outage.
            return envelopes.blocked(request_id, codes.VALIDATION_FAILED, str(exc))
        return envelopes.completed(request_id, data=result)

    def _capabilities(self) -> Dict[str, Any]:
        boundary = BOUNDARIES[self.domain]
        payload: Dict[str, Any] = {
            "server": f"taxagent-{self.domain}",
            "domain": self.domain,
            "title": boundary["title"],
            "tax_year": self.tax_year,
            "jurisdiction": "US_FEDERAL",
            "calculators": [
                {"tool": tool_name(c), "title": calculators.spec(c)["title"],
                 "summary": calculators.spec(c)["summary"]}
                for c in self.calculators
            ],
            "not_computed": list(boundary["not_computed"]),
            "note": "Analysis only. Figures are supplied by the caller and nothing is "
                    "stored.",
        }
        if boundary["rule_domain"]:
            # Report the pack actually bound, so a caller sees the authority
            # rather than taking the server's word for its own coverage.
            try:
                pack = domain_rules(boundary["rule_domain"], self.tax_year)
                payload["rule_pack"] = pack.to_json()
                payload["authorities"] = dict(pack.authorities)
            except RulePackError as exc:
                payload["rule_pack"] = None
                payload["rule_pack_error"] = str(exc)
        else:
            payload["engine_id"] = "federal-income-estimator"
            payload["verified_against"] = "Rev. Proc. 2025-32"
        return payload
