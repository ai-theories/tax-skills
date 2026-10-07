"""Domain tool surfaces for the portfolio and household servers.

The generic `start_analysis` tool asked the caller for a `template_id` and a
nested intent document. Both are implementation detail leaking across the
boundary: an agent had to know that "review harvesting" means the literal
string "harvest-review.v1", and had to assemble objectives and constraints by
hand, where omitting `netting_basis` silently produced a needs_input rather
than a schema error.

These dispatchers name the operation instead and take flat arguments. The
template id is chosen here, where it is checked, and the intent is assembled
from typed fields. Nothing about entitlement, validation or evidence changes:
the same service runs the same template and returns the same envelope.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..domain import codes
from ..domain.identities import Principal
from ..domain.sleeves import PlannedAcquisition
from . import result_envelopes as envelopes

_SUBJECT = {"type": "string",
            "description": "Account or household id you are entitled to read."}
_DOCUMENT = {"type": "string",
             "description": "Path to the normalized holdings export to analyse."}
_AS_OF = {"type": "string", "format": "date",
          "description": "Analysis date, YYYY-MM-DD. Holding periods and the wash-sale "
                         "window are measured from it."}
_PRICES = {"type": "object", "additionalProperties": {"type": "string"},
           "description": "Price per share by security id, as decimal strings."}
_IDEMPOTENCY = {"type": "string",
                "description": "Caller-chosen key. Repeating it returns the first run "
                               "rather than running again."}
_NETTING = {"type": "string", "enum": ["net_gains", "gross_gains"],
            "description": "Whether the gain budget is measured net of losses or on gains "
                           "alone. The two readings give different answers and the server "
                           "will not pick one for you."}
_PERIOD = {"type": "string",
           "description": "Tax period the budget applies to, e.g. \"2026\"."}

_PLANNED = {
    "type": "array",
    "description": "Purchases already planned but not yet placed. Supplying them lets the "
                   "screen catch a wash sale before it is created rather than after.",
    "items": {
        "type": "object",
        "properties": {
            "account_id": {"type": "string"},
            "security_id": {"type": "string"},
            "quantity": {"type": "string"},
            "trade_date": {"type": "string", "format": "date"},
            "unit_id": {"type": "string",
                        "description": "Which sleeve or manager intends the purchase."},
        },
        "required": ["account_id", "security_id", "quantity", "trade_date"],
        "additionalProperties": False,
    },
}


_TARGETS = {
    "type": "object",
    "description": "Target weight by security id, as decimal strings summing to 1. "
                   "Supplied by the advisor: inferring a target from the current "
                   "holdings would make the portfolio its own benchmark.",
    "additionalProperties": {"type": "string"},
}
_TOLERANCE = {"type": "string",
              "description": "Drift below this fraction is left alone, so small "
                             "deviations do not generate trades. Default 0."}


def _run_shape(extra: Dict[str, Any], required: Sequence[str]) -> Dict[str, Any]:
    properties = {"subject_ref": _SUBJECT, "document_ref": _DOCUMENT, "as_of": _AS_OF,
                  "prices": _PRICES, "idempotency_key": _IDEMPOTENCY}
    properties.update(extra)
    return {"type": "object", "properties": properties,
            "required": ["subject_ref", "document_ref", "as_of", "prices",
                         "idempotency_key", *required],
            "additionalProperties": False}


_SHARED_TOOLS: Dict[str, Dict[str, Any]] = {
    "resolve_subject": {
        "description": "Resolve an account or household you are entitled to read and report "
                       "its tax units. Pass document_ref on the first call of a session: "
                       "account metadata comes from the document, and nothing resolves "
                       "until one has been read.",
        "inputSchema": {"type": "object",
                        "properties": {"subject_ref": _SUBJECT, "document_ref": _DOCUMENT},
                        "required": ["subject_ref"], "additionalProperties": False},
    },
    "get_run_status": {
        "description": "Status of a run. Transport success is not workflow completion.",
        "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}},
                        "required": ["run_id"], "additionalProperties": False},
    },
    "get_scenario": {
        "description": "Retrieve a stored evidence package by reference, including the "
                       "rule pack and snapshot the run was pinned to.",
        "inputSchema": {"type": "object", "properties": {"evidence_ref": {"type": "string"}},
                        "required": ["evidence_ref"], "additionalProperties": False},
    },
}


class _WorkflowDispatcher:
    """Shared plumbing: entitlement, document loading, envelope passthrough."""

    tools: Dict[str, Dict[str, Any]] = {}
    capability_tool = "get_capabilities"
    capability_description = "What this server covers and what it declines."

    def __init__(self, service, principal: Principal,
                 document_loader: Callable[[str], Any]):
        self.service = service
        self.principal = principal
        self.document_loader = document_loader
        self.registered = [*self.tools, *_SHARED_TOOLS, self.capability_tool]

    def list_tools(self) -> List[Dict[str, Any]]:
        table = {**self.tools, **_SHARED_TOOLS}
        tools = [{"name": name, "description": table[name]["description"],
                  "inputSchema": table[name]["inputSchema"]}
                 for name in self.registered if name in table]
        tools.append({"name": self.capability_tool,
                      "description": self.capability_description,
                      "inputSchema": {"type": "object", "properties": {},
                                      "additionalProperties": False}})
        return tools

    def call(self, name: str, arguments: Dict[str, Any], request_id: str) -> Dict[str, Any]:
        if name not in self.registered:
            return envelopes.blocked(
                request_id, codes.UNSUPPORTED_SCOPE,
                f"Tool '{name}' is not registered in this deployment.")
        return getattr(self, f"_{name}")(arguments, request_id)

    # --- shared handlers -------------------------------------------------
    def _get_capabilities(self, arguments, request_id):
        return self.service.get_capabilities(self.principal, request_id)

    def _resolve_subject(self, arguments, request_id):
        if arguments.get("document_ref"):
            self.document_loader(arguments["document_ref"])
        return self.service.resolve_subject(self.principal, request_id,
                                            arguments["subject_ref"])

    def _get_run_status(self, arguments, request_id):
        return self.service.get_run_status(self.principal, request_id, arguments["run_id"])

    def _get_scenario(self, arguments, request_id):
        return self.service.get_scenario(self.principal, request_id,
                                         arguments["evidence_ref"])

    # --- helpers ---------------------------------------------------------
    def _start(self, template_id: str, arguments: Dict[str, Any], request_id: str,
               intent: Dict[str, Any],
               planned: Sequence[PlannedAcquisition] = (),
               target_allocation: Any = None) -> Dict[str, Any]:
        source_data = self.document_loader(arguments["document_ref"])
        return self.service.start_analysis(
            principal=self.principal, request_id=request_id, template_id=template_id,
            subject_ref=arguments["subject_ref"], source_data=source_data,
            as_of=date.fromisoformat(arguments["as_of"]), prices=arguments["prices"],
            intent=intent, idempotency_key=arguments["idempotency_key"],
            planned_acquisitions=list(planned),
            target_allocation=target_allocation)

    @staticmethod
    def _planned(arguments: Dict[str, Any]) -> List[PlannedAcquisition]:
        from decimal import Decimal
        return [
            PlannedAcquisition(p["account_id"], p["security_id"], Decimal(str(p["quantity"])),
                               date.fromisoformat(p["trade_date"]), p.get("unit_id", ""))
            for p in arguments.get("planned_purchases") or []
        ]

    @staticmethod
    def _intent(kind: str, subject_ref: str, request_id: str,
                objectives: Sequence[Dict[str, Any]] = (),
                constraints: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
        return {"schema_version": "1.0", "request_id": request_id, "intent": kind,
                "mode": "analysis_only", "subject_ref": subject_ref,
                "objectives": list(objectives), "constraints": list(constraints)}

    @staticmethod
    def _gain_budget(arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not arguments.get("gain_budget"):
            return []
        return [{"type": "realized_gain_budget", "amount": arguments["gain_budget"],
                 "currency": "USD", "netting_basis": arguments["netting_basis"],
                 "period": arguments["period"]}]


class PortfolioDispatcher(_WorkflowDispatcher):
    """Account-scope lot analysis."""

    capability_description = (
        "Which engines, asset classes and tax years this server covers for account-scope "
        "analysis, with each engine's limitations. Harvesting, exact optimization and "
        "rebalancing are separate engines making separate claims.")
    tools = {
        "check_holdings": {
            "description": "Build a snapshot from an uploaded export and report the lots "
                           "that cannot support a tax calculation: unknown basis, missing "
                           "acquisition date, gaps in coverage. Run this first when a "
                           "document is new.",
            "inputSchema": _run_shape({}, ()),
        },
        "review_rebalance": {
            "description": "Rebalance one account toward target weights, tax-aware. "
                           "Never sells a security below its target, so with chunky "
                           "lots it under-trades rather than overshooting and reports "
                           "the residual drift. Withholds a purchase that would wash a "
                           "loss sold inside the window.",
            "inputSchema": _run_shape({
                "target_weights": _TARGETS,
                "tolerance": _TOLERANCE,
                "gain_budget": {"type": "string",
                                "description": "Cap on realized gains from the sells."},
                "netting_basis": _NETTING,
                "period": _PERIOD,
            }, ("target_weights",)),
        },
        "review_lots": {
            "description": "Gain and loss by lot with a wash-sale screen across the accounts "
                           "in scope. Reports what is there; proposes nothing.",
            "inputSchema": _run_shape({}, ()),
        },
        "optimize_harvest": {
            "description": "The same harvest problem as review_harvest, solved exactly "
                           "rather than greedily. Where the search exhausts it proves "
                           "no other selection raises the cash at lower tax cost. "
                           "Slower, and it reports a certificate and an optimality gap.",
            "inputSchema": _run_shape({
                "cash_target": {"type": "string",
                                "description": "Cash to raise, as a decimal string."},
                "withdrawal_account": {"type": "string",
                                       "description": "Account the cash is raised from."},
                "gain_budget": {"type": "string",
                                "description": "Cap on realized gains."},
                "netting_basis": _NETTING,
                "period": _PERIOD,
                "planned_purchases": _PLANNED,
            }, ("cash_target",)),
        },
        "review_harvest": {
            "description": "Loss-harvesting scenario for one taxable account against a cash "
                           "target, screened for wash sales. Produces a proposal for "
                           "professional review, never a trade instruction.",
            "inputSchema": _run_shape({
                "cash_target": {"type": "string",
                                "description": "Cash to raise, as a decimal string."},
                "withdrawal_account": {"type": "string",
                                       "description": "Account the cash is raised from."},
                "gain_budget": {"type": "string",
                                "description": "Cap on realized gains. Omit for no cap; "
                                               "supplying it requires netting_basis and "
                                               "period."},
                "netting_basis": _NETTING,
                "period": _PERIOD,
                "planned_purchases": _PLANNED,
            }, ("cash_target",)),
        },
    }

    def _check_holdings(self, arguments, request_id):
        intent = self._intent("intake", arguments["subject_ref"], request_id)
        return self._start("intake.v1", arguments, request_id, intent)

    def _review_rebalance(self, arguments, request_id):
        from decimal import Decimal

        from ..domain.constraints import TargetAllocation

        weights = arguments["target_weights"]
        allocation = TargetAllocation(
            weights=tuple(sorted((str(s), Decimal(str(w))) for s, w in weights.items())),
            tolerance=Decimal(str(arguments.get("tolerance") or "0")))
        intent = self._intent("rebalance_review", arguments["subject_ref"], request_id,
                              (), self._gain_budget(arguments))
        return self._start("account-rebalance.v1", arguments, request_id, intent,
                           target_allocation=allocation)

    def _review_lots(self, arguments, request_id):
        intent = self._intent("lot_review", arguments["subject_ref"], request_id)
        return self._start("lot-review.v1", arguments, request_id, intent)

    def _optimize_harvest(self, arguments, request_id):
        objectives = [{"type": "raise_cash", "amount": arguments["cash_target"],
                       "currency": "USD",
                       "withdrawal_account": arguments.get("withdrawal_account")
                       or arguments["subject_ref"]}]
        intent = self._intent("harvest_review", arguments["subject_ref"], request_id,
                              objectives, self._gain_budget(arguments))
        return self._start("account-optimize.v1", arguments, request_id, intent,
                           self._planned(arguments))

    def _review_harvest(self, arguments, request_id):
        objectives = [{"type": "raise_cash", "amount": arguments["cash_target"],
                       "currency": "USD",
                       "withdrawal_account": arguments.get("withdrawal_account")
                       or arguments["subject_ref"]}]
        intent = self._intent("harvest_review", arguments["subject_ref"], request_id,
                              objectives, self._gain_budget(arguments))
        return self._start("harvest-review.v1", arguments, request_id, intent,
                           self._planned(arguments))


class HouseholdDispatcher(_WorkflowDispatcher):
    """Household and UMA-sleeve coordination over one shared budget per tax unit."""

    capability_description = (
        "What household analysis covers, and which engine makes which claim. A joint "
        "optimizer is available and proves optimality where its search exhausts; the "
        "coordinator is sequential, order-dependent and never optimal.")
    tools = {
        "optimize_household": {
            "description": "Solve the whole household at once rather than account by "
                           "account. No ordering, and where the search exhausts it "
                           "proves no selection does better. Budgets are still per tax "
                           "unit and never pooled. Prefer this over coordinate_household "
                           "unless you need the reservation behaviour.",
            "inputSchema": _run_shape({
                "cash_target": {"type": "string",
                                "description": "Cash to raise across the household."},
                "withdrawal_account": {"type": "string",
                                       "description": "Account the cash is raised from."},
                "gain_budget": {"type": "string",
                                "description": "Cap on realized gains, per tax unit."},
                "netting_basis": _NETTING,
                "period": _PERIOD,
                "planned_purchases": _PLANNED,
            }, ("cash_target",)),
        },
        "coordinate_household": {
            "description": "Run several accounts or UMA sleeves against one shared gain "
                           "budget per tax unit, reserving budget so no two runs spend the "
                           "same allowance and screening cross-sleeve wash sales. Sequential, "
                           "not a joint optimum; the optimality gap is reported.",
            "inputSchema": _run_shape({
                "cash_target": {"type": "string",
                                "description": "Cash to raise across the household."},
                "withdrawal_account": {"type": "string",
                                       "description": "Account the cash is raised from."},
                "gain_budget": {"type": "string",
                                "description": "Shared cap on realized gains per tax unit."},
                "netting_basis": _NETTING,
                "period": _PERIOD,
                "planned_purchases": _PLANNED,
            }, ("cash_target",)),
        },
    }

    def _optimize_household(self, arguments, request_id):
        objectives = [{"type": "raise_cash", "amount": arguments["cash_target"],
                       "currency": "USD",
                       "withdrawal_account": arguments.get("withdrawal_account")
                       or arguments["subject_ref"]}]
        intent = self._intent("harvest_review", arguments["subject_ref"], request_id,
                              objectives, self._gain_budget(arguments))
        return self._start("household-optimize.v1", arguments, request_id, intent,
                           self._planned(arguments))

    def _coordinate_household(self, arguments, request_id):
        objectives = [{"type": "raise_cash", "amount": arguments["cash_target"],
                       "currency": "USD",
                       "withdrawal_account": arguments.get("withdrawal_account")
                       or arguments["subject_ref"]}]
        intent = self._intent("harvest_review", arguments["subject_ref"], request_id,
                              objectives, self._gain_budget(arguments))
        return self._start("household-coordination.v1", arguments, request_id, intent,
                           self._planned(arguments))
