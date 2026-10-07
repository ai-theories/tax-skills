"""Application service: wires ports into workflow step handlers and tools.

This is the only module that knows about every layer. Domain and calculation
modules stay free of storage, transport and host concerns so the financial
tests run without a database or an LLM.
"""
from __future__ import annotations

import os
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

from .. import __version__
from ..decisions.code_rules import evaluate_lot_restrictions
from ..decisions.constraint_compiler import compile_constraints
from ..decisions.rule_registry import RulePackError, default_rules
from ..domain import codes
from ..domain.constraints import AmbiguousConstraint, ConstraintSet, Restriction
from ..domain.coverage import Finding
from ..domain.identities import Principal, Subject
from ..domain.money import Money
from ..domain.snapshots import Snapshot
from ..evidence.package_builder import build_evidence_package
from ..evidence.provenance import EngineManifest
from ..evidence.report_renderer import render_review_brief
from ..coordinator.runner import WorkflowRunner
from ..coordinator.state_machine import RunState
from ..coordinator.template_registry import TemplateRegistry
from ..gateway.authorization import AccessDenied, ScopeAuthorizer
from ..gateway.capability_registry import CapabilityRegistry
from ..gateway import result_envelopes as envelopes
from ..domain.sleeves import CoordinationUnit, PlannedAcquisition
from ..optimization.harvest_selector import RuleBasedHarvestSelector
from ..optimization.household_coordinator import SequentialHouseholdCoordinator, derive_units
from ..optimization.interface import OptimizationProblem, UnsupportedCapability
from ..portfolio.snapshot_builder import build_snapshot
from ..storage.sqlite import SqliteStore
from ..tax.lot_gain_loss import ProposedSale, calculate_gain_loss
from ..tax.wash_sale_screen import screen_wash_sales
from ..validation.result_validator import (INCOMPLETE, ValidationResult,
                                           validate_scenario)

OPERATIONS = (
    "create_snapshot", "validate_lots", "compile_constraints", "select_harvest_candidates",
    "coordinate_units", "select_rebalance_trades", "select_household_jointly",
    "select_account_optimally",
    "calculate_gain_loss",
    "screen_wash_sales", "validate_scenario", "build_evidence_pack",
)


class AnalysisService:
    def __init__(self, store: SqliteStore, authorizer: ScopeAuthorizer,
                 capabilities: CapabilityRegistry, templates: TemplateRegistry,
                 selector: Optional[RuleBasedHarvestSelector] = None,
                 rebalancer: Optional[Any] = None,
                 household_optimizer: Optional[Any] = None,
                 account_optimizer: Optional[Any] = None):
        from ..optimization.household_optimizer import JointHouseholdOptimizer
        from ..optimization.oracle_account_adapter import OracleAccountAdapter
        from ..optimization.rebalance_engine import TaxAwareRebalanceEngine

        self.store = store
        self.authorizer = authorizer
        self.capabilities = capabilities
        self.templates = templates
        self.selector = selector or RuleBasedHarvestSelector()
        self.rebalancer = rebalancer or TaxAwareRebalanceEngine()
        self.household_optimizer = household_optimizer or JointHouseholdOptimizer()
        self.account_optimizer = account_optimizer or OracleAccountAdapter()
        self.snapshots: Dict[str, Snapshot] = {}
        self.runner = WorkflowRunner(store, self._handlers())

    # --- step handlers ---------------------------------------------------
    def _handlers(self):
        return {
            "create_snapshot": self._step_create_snapshot,
            "validate_lots": self._step_validate_lots,
            "compile_constraints": self._step_compile_constraints,
            "select_harvest_candidates": self._step_select_candidates,
            "coordinate_units": self._step_coordinate_units,
            "select_rebalance_trades": self._step_select_rebalance_trades,
            "select_household_jointly": self._step_optimize_household,
            "select_account_optimally": self._step_optimize_account,
            "calculate_gain_loss": self._step_gain_loss,
            "screen_wash_sales": self._step_wash_sales,
            "validate_scenario": self._step_validate,
            "build_evidence_pack": self._step_evidence,
        }

    def _step_create_snapshot(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        # Resolve the reviewed rule pack before any calculation reads a rule.
        if "rules" not in ctx:
            ctx["rules"] = default_rules(ctx["as_of"].year, as_of=ctx["as_of"])
        if "snapshot" in ctx:
            return ctx
        source = ctx["source_data"]
        snapshot = build_snapshot(
            tenant_id=ctx["tenant_id"], subject_id=ctx["subject"].subject_id,
            as_of=ctx["as_of"], accounts=source.accounts, lots=source.lots,
            transactions=source.transactions, coverage=source.coverage,
            source_refs=source.source_refs, reported_positions=source.reported_positions,
            sleeves=source.sleeves, sleeve_assignments=source.sleeve_assignments,
        )
        self.snapshots[snapshot.snapshot_id] = snapshot
        ctx["snapshot"] = snapshot
        return ctx

    def _step_validate_lots(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        snapshot: Snapshot = ctx["snapshot"]
        ctx["lot_findings"] = [f.to_json() for f in snapshot.findings]
        ctx["blocking_lot_findings"] = [f.to_json() for f in snapshot.blocking_findings()]
        return ctx

    def _step_compile_constraints(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        if ctx.get("constraints") is not None:
            return ctx
        compiled = compile_constraints(
            tenant_id=ctx["tenant_id"], subject_id=ctx["subject"].subject_id,
            intent=ctx["intent"], restrictions=ctx.get("restrictions", ()),
            rule_bundle_ref=ctx["rules"].bundle_ref,
        )
        target = ctx.get("target_allocation")
        if target is not None:
            # Targets come from the caller, not from the intent document: they
            # are the advisor's model, not a tax position to be compiled.
            compiled = replace(compiled, target_allocation=target)
        ctx["constraints"] = compiled
        return ctx

    def _step_select_candidates(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        snapshot: Snapshot = ctx["snapshot"]
        subject: Subject = ctx["subject"]
        scope = "household" if subject.kind == "household" else "account"
        asset_classes = ctx.get("asset_classes", ["US_EQUITY"])
        # Raises UNSUPPORTED_SCOPE before any work when coverage is absent.
        engine = self.capabilities.require(
            kind="selector", scope=scope, asset_classes=asset_classes,
            tax_year=ctx["as_of"].year)
        ctx["engine_coverage"] = engine.to_json()
        problem = OptimizationProblem(
            snapshot=snapshot, constraints=ctx["constraints"], scope=scope,
            as_of=ctx["as_of"], prices=ctx["prices"], objective=ctx.get("objective", "harvest_losses"),
        )
        ctx["candidate"] = self.selector.solve(problem)
        return ctx

    def _step_coordinate_units(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        snapshot: Snapshot = ctx["snapshot"]
        engine = self.capabilities.require(
            kind="coordinator", scope="household_coordinated",
            asset_classes=ctx.get("asset_classes", ["US_EQUITY"]), tax_year=ctx["as_of"].year)
        ctx["engine_coverage"] = engine.to_json()

        units = ctx.get("units") or derive_units(snapshot)
        ctx["units"] = units
        ctx["unit_of_lot"] = {
            lot_id: unit.unit_id for unit in units for lot_id in unit.lot_ids}

        coordinator = SequentialHouseholdCoordinator(
            selector=self.selector, reservation_store=self.store,
            tenant_id=ctx["tenant_id"], run_id=ctx["run_id"])
        problem = OptimizationProblem(
            snapshot=snapshot, constraints=ctx["constraints"], scope="household_coordinated",
            as_of=ctx["as_of"], prices=ctx["prices"], units=tuple(units),
            planned_acquisitions=tuple(ctx.get("planned_acquisitions", ())),
        )
        ctx["candidate"] = coordinator.solve(problem)
        ctx["coordinator"] = coordinator
        return ctx

    def _step_select_rebalance_trades(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """Rebalancing runs on its own engine, never the harvest selector.

        Pricing a rebalance with the harvest selector would optimise for losses
        rather than for the target weights, and the evidence package would
        record a harvest run. The registry gate stays in front of it, so an
        engine that is not registered for this scope and year still refuses.
        """
        engine = self.capabilities.require(
            kind="rebalancer", scope="account",
            asset_classes=ctx.get("asset_classes", ["US_EQUITY"]), tax_year=ctx["as_of"].year)
        ctx["engine_coverage"] = engine.to_json()
        problem = OptimizationProblem(
            snapshot=ctx["snapshot"], constraints=ctx["constraints"], scope="account",
            as_of=ctx["as_of"], prices=ctx["prices"], objective="rebalance",
            planned_acquisitions=tuple(ctx.get("planned_acquisitions", ())),
        )
        ctx["candidate"] = self.rebalancer.solve(problem)
        return ctx

    def _step_optimize_account(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """Account-scope optimization, as distinct from greedy selection.

        `review_harvest` runs the rule-based selector, which is fast and makes
        no optimality claim. This runs the bound optimizer, which proves it
        where the search exhausts. Both exist because they make different
        claims, and a caller must be able to ask for the one they want.
        """
        engine = self.capabilities.require(
            kind="optimizer", scope="account",
            asset_classes=ctx.get("asset_classes", ["US_EQUITY"]), tax_year=ctx["as_of"].year)
        ctx["engine_coverage"] = engine.to_json()
        problem = OptimizationProblem(
            snapshot=ctx["snapshot"], constraints=ctx["constraints"], scope="account",
            as_of=ctx["as_of"], prices=ctx["prices"],
            objective=ctx.get("objective", "harvest_losses"),
            planned_acquisitions=tuple(ctx.get("planned_acquisitions", ())),
        )
        ctx["candidate"] = self.account_optimizer.solve(problem)
        return ctx

    def _step_optimize_household(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """Joint household optimization, as distinct from sequential coordination.

        The coordinator serves accounts in an order and reports what the order
        cost. This solves every eligible lot in the household at once and, when
        the search exhausts, proves there was nothing better.
        """
        engine = self.capabilities.require(
            kind="optimizer", scope="household",
            asset_classes=ctx.get("asset_classes", ["US_EQUITY"]), tax_year=ctx["as_of"].year)
        ctx["engine_coverage"] = engine.to_json()
        problem = OptimizationProblem(
            snapshot=ctx["snapshot"], constraints=ctx["constraints"], scope="household",
            as_of=ctx["as_of"], prices=ctx["prices"],
            objective=ctx.get("objective", "harvest_losses"),
            planned_acquisitions=tuple(ctx.get("planned_acquisitions", ())),
        )
        ctx["candidate"] = self.household_optimizer.solve(problem)
        return ctx

    def _step_gain_loss(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        snapshot: Snapshot = ctx["snapshot"]
        sales: Optional[List[ProposedSale]] = ctx.get("proposed_sales")
        if sales is None and "candidate" in ctx:
            sales = [
                ProposedSale(t.account_id, t.security_id, Decimal(t.quantity),
                             Money.of(t.price_per_share), lot_id=t.lot_id,
                             sale_date=ctx["as_of"])
                for t in ctx["candidate"].trades
            ]
        if sales is None:
            # A review with nothing proposed: value every lot where it stands.
            # The template that does this has no selector step, so reaching for
            # a candidate here raised KeyError and the whole template could
            # never run. These are valuations, not disposals, and the flag
            # below keeps anything downstream from reading them as realized.
            sales = self._hypothetical_sales(ctx)
            ctx["hypothetical"] = True
        ctx["gain_loss"] = calculate_gain_loss(snapshot, sales, ctx["as_of"],
                                               rules=ctx["rules"])
        return ctx

    @staticmethod
    def _hypothetical_sales(ctx: Dict[str, Any]) -> List[ProposedSale]:
        """Every lot in scope, valued at the as-of price.

        A lot with no price cannot be valued, and guessing one would put an
        invented figure into an evidence package. Those lots are left out here
        and reported as a limitation, the same way unknown basis is.
        """
        snapshot: Snapshot = ctx["snapshot"]
        prices = ctx.get("prices") or {}
        sales, unpriced = [], []
        for lot in snapshot.lots:
            price = prices.get(lot.security_id)
            if price is None:
                unpriced.append(lot.lot_id)
                continue
            sales.append(ProposedSale(lot.account_id, lot.security_id, lot.quantity,
                                      Money.of(price), lot_id=lot.lot_id,
                                      sale_date=ctx["as_of"]))
        ctx["unpriced_lot_ids"] = tuple(unpriced)
        return sales

    def _step_wash_sales(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        snapshot: Snapshot = ctx["snapshot"]
        ctx["wash_sale"] = screen_wash_sales(
            snapshot, ctx["gain_loss"].dispositions,
            scope_account_ids=snapshot.account_ids(),
            known_related_account_ids=ctx.get("known_related_account_ids", ()),
            planned_acquisitions=tuple(ctx.get("planned_acquisitions", ())),
            unit_of_lot=ctx.get("unit_of_lot"),
            rules=ctx["rules"],
        )
        return ctx

    def _step_validate(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        ctx["validation"] = validate_scenario(
            ctx["snapshot"], ctx["constraints"], ctx["candidate"],
            ctx.get("wash_sale"), ctx["as_of"])
        return ctx

    @staticmethod
    def _review_constraints(ctx: Dict[str, Any]) -> ConstraintSet:
        """An empty constraint set for a template that compiles none.

        A position review asks nothing of the portfolio, so there is no cash
        target and no gain budget. Recording that explicitly keeps the evidence
        package honest: the run was unconstrained, not constrained and passing.
        """
        return ConstraintSet(constraints_ref=f"constraints_none_{ctx['run_id']}",
                             tenant_id=ctx["tenant_id"],
                             subject_id=ctx["subject"].subject_id,
                             rule_bundle_ref=ctx["rules"].bundle_ref)

    @staticmethod
    def _review_validation(ctx: Dict[str, Any]) -> ValidationResult:
        """Validation for a run that proposed nothing.

        `validate_scenario` recomputes a proposal against its constraints.
        There is no proposal here, so recording "passed" would assert a check
        that never ran. The checks are marked incomplete and the reason is
        carried as a limitation rather than left for a reader to infer.
        """
        return ValidationResult(
            checks={"scenario_validation": INCOMPLETE},
            violations=(),
            limitations=(Finding.make(
                codes.NO_PROPOSAL_TO_VALIDATE, codes.SEVERITY_LIMITING,
                "This run values positions and screens them; it proposes no trade, so "
                "there is nothing for the independent validator to recompute. Figures "
                "here are unrealized.",
                run_id=ctx["run_id"]),),
            recomputed={})

    def _step_evidence(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        snapshot: Snapshot = ctx["snapshot"]
        candidate = ctx.get("candidate")
        if ctx.get("constraints") is None:
            ctx["constraints"] = self._review_constraints(ctx)
        if ctx.get("validation") is None:
            ctx["validation"] = self._review_validation(ctx)
        rules = ctx["rules"]
        manifest = EngineManifest.capture(
            optimizer_name=(candidate.engine_name if candidate else ""),
            optimizer_version=(candidate.engine_version if candidate else ""),
            rule_bundle_ref=rules.bundle_ref,
            rule_bundle_hash=rules.bundle_hash,
        )
        package = build_evidence_package(
            run_id=ctx["run_id"], request_id=ctx["request_id"], tenant_id=ctx["tenant_id"],
            snapshot=snapshot, constraints=ctx["constraints"], candidate=candidate,
            gain_loss=ctx["gain_loss"], screen=ctx.get("wash_sale"),
            validation=ctx["validation"], manifest=manifest,
            interpreted_request=ctx.get("intent", {}), assumptions=ctx.get("assumptions", []),
        )
        ctx["evidence"] = package
        ctx["evidence_ref"] = self.store.put(ctx["tenant_id"], "evidence", package)
        ctx["report_markdown"] = render_review_brief(package)
        return ctx

    # --- tool surface ----------------------------------------------------
    def get_capabilities(self, principal: Principal, request_id: str) -> Dict[str, Any]:
        payload = self.capabilities.to_json()
        payload["workflow_templates"] = self.templates.ids()
        return envelopes.completed(request_id, data=payload)

    def resolve_subject(self, principal: Principal, request_id: str,
                        subject_ref: str) -> Dict[str, Any]:
        try:
            subject = self.authorizer.resolve(principal, subject_ref)
        except AccessDenied as exc:
            return envelopes.blocked(request_id, codes.ACCESS_DENIED, str(exc))
        return envelopes.completed(request_id, data={
            "subject_id": subject.subject_id, "kind": subject.kind,
            "account_ids": list(subject.account_ids), "tax_unit_ids": list(subject.tax_unit_ids),
            "spans_multiple_tax_units": subject.spans_multiple_tax_units,
        })

    def start_analysis(self, principal: Principal, request_id: str, template_id: str,
                       subject_ref: str, source_data, as_of: date, prices: Dict[str, str],
                       intent: Dict[str, Any], idempotency_key: str,
                       restrictions: Sequence[Restriction] = (),
                       asset_classes: Sequence[str] = ("US_EQUITY",),
                       rule_bundle_ref: str = "",
                       planned_acquisitions: Sequence[PlannedAcquisition] = (),
                       units: Sequence[CoordinationUnit] = (),
                       target_allocation: Optional[Any] = None) -> Dict[str, Any]:
        try:
            subject = self.authorizer.resolve(principal, subject_ref)
            self.authorizer.authorize_accounts(
                principal, [a.account_id for a in source_data.accounts])
            # Source accounts may extend beyond the subject only when they belong
            # to the same tax unit, which is what makes a wash-sale screen able to
            # see a related IRA without widening the analysis scope.
            in_scope = set(subject.account_ids)
            tax_units = set(subject.tax_unit_ids)
            for account in source_data.accounts:
                if account.account_id not in in_scope and account.tax_unit_id not in tax_units:
                    raise AccessDenied()
        except AccessDenied as exc:
            return envelopes.blocked(request_id, codes.ACCESS_DENIED, str(exc))

        template = self.templates.get(template_id)
        run, created = self.store.create_or_get(
            principal.tenant_id, idempotency_key,
            {"request_id": request_id, "template_id": template_id, "subject_ref": subject_ref})
        run_id = run["run_id"]
        if not created and run["state"] in {s.value for s in (RunState.COMPLETED,
                                                              RunState.REVIEW_READY)}:
            return envelopes.replayed(request_id, run_id)

        related = self.authorizer.related_accounts_in_tax_units(principal, subject.tax_unit_ids)
        context: Dict[str, Any] = {
            "tenant_id": principal.tenant_id, "request_id": request_id, "run_id": run_id,
            "subject": subject, "source_data": source_data, "as_of": as_of, "prices": prices,
            "intent": intent, "restrictions": tuple(restrictions),
            "target_allocation": target_allocation,
            "asset_classes": list(asset_classes), "rule_bundle_ref": rule_bundle_ref,
            "known_related_account_ids": related,
            "planned_acquisitions": tuple(planned_acquisitions),
            "units": tuple(units),
            "required_scope": template.required_scope,
        }
        outcome = self.runner.execute(principal.tenant_id, run_id, template, context)

        if outcome.state is RunState.BLOCKED and outcome.code == codes.AMBIGUOUS_CONSTRAINT:
            return envelopes.needs_input(request_id, outcome.unresolved_fields, outcome.message)
        if outcome.state is RunState.BLOCKED:
            return envelopes.blocked(request_id, outcome.code or codes.UNSUPPORTED_SCOPE,
                                     outcome.message, outcome.supported_alternative or None)
        if outcome.state is RunState.FAILED:
            return envelopes.failed(request_id, outcome.code or codes.VALIDATION_FAILED,
                                    outcome.message)
        if outcome.state is RunState.CANCELLED:
            return envelopes.blocked(request_id, "CANCELLED", outcome.message)

        ctx = outcome.context
        validation = ctx.get("validation")
        screen = ctx.get("wash_sale")
        # Carry each finding's refs: three UNKNOWN_RELATED_ACCOUNTS entries are
        # three different accounts, and a reviewer needs to know which.
        limitations = []
        seen_limitations = set()
        # Lot findings first. A template that only builds and checks a snapshot
        # runs no validator, so taking limitations from the validator alone
        # dropped them entirely — on the one template whose whole purpose is to
        # report them. The key below collapses the duplicates this creates on
        # templates where the validator reports the same finding again.
        for finding in [*ctx.get("lot_findings", []),
                        *(validation.to_json()["limitations"] if validation else [])]:
            key = (finding["code"], tuple(sorted(finding["refs"].items())))
            if key in seen_limitations:
                continue
            seen_limitations.add(key)
            limitations.append({"code": finding["code"], "impact": finding["message"],
                                "refs": finding["refs"]})
        checks = dict(validation.checks) if validation else {}
        if screen is not None:
            checks["external_account_coverage"] = (
                "passed" if screen.is_complete else "incomplete")

        if validation is not None and not validation.passed:
            # REVIEW_READY -> INVALIDATED: diagnostics are kept, but a scenario that
            # breaches a hard constraint never reaches a reviewer as a recommendation.
            self.store.set_state(principal.tenant_id, run_id, RunState.INVALIDATED.value)
            if ctx.get("coordinator") is not None:
                # An invalidated scenario must not keep holding household budget.
                ctx["coordinator"].release_all()
            violation = validation.violations[0]
            envelope = envelopes.blocked(
                request_id, violation.code, violation.message,
                "Relax a constraint explicitly, or accept a smaller result; constraints are "
                "never relaxed automatically.")
            envelope["validation"] = checks
            envelope["limitations"] = limitations
            envelope["evidence_ref"] = ctx.get("evidence_ref")
            # An invalidated run is the one a reviewer scrutinises hardest, so it
            # carries the same provenance as a published one.
            envelope["snapshot_ref"] = ctx["snapshot"].snapshot_id
            envelope["data"] = {
                "run_id": run_id,
                "violations": [v.to_json() for v in validation.violations],
                "diagnostics": (candidate.diagnostics if (candidate := ctx.get("candidate")) else {}),
                "report_markdown": ctx.get("report_markdown", ""),
            }
            return envelope

        self.store.set_state(principal.tenant_id, run_id, RunState.COMPLETED.value)
        return envelopes.completed(
            request_id,
            result_ref=ctx.get("evidence_ref"), evidence_ref=ctx.get("evidence_ref"),
            snapshot_ref=ctx["snapshot"].snapshot_id,
            engine_manifest_ref=ctx.get("engine_coverage", {}).get("engine_id"),
            validation=checks, limitations=limitations,
            data={"run_id": run_id, "report_markdown": ctx.get("report_markdown", "")},
        )

    def get_run_status(self, principal: Principal, request_id: str, run_id: str) -> Dict[str, Any]:
        run = self.store.get(principal.tenant_id, run_id)
        if run is None:
            return envelopes.blocked(request_id, codes.ACCESS_DENIED,
                                     "The requested run is not available to this principal.")
        return envelopes.completed(request_id, data={
            "run_id": run_id, "state": run["state"],
            "steps": self.store.steps(principal.tenant_id, run_id),
            "events": self.store.events(principal.tenant_id, run_id),
        })

    def cancel_run(self, principal: Principal, request_id: str, run_id: str) -> Dict[str, Any]:
        run = self.store.get(principal.tenant_id, run_id)
        if run is None:
            return envelopes.blocked(request_id, codes.ACCESS_DENIED,
                                     "The requested run is not available to this principal.")
        self.store.request_cancel(principal.tenant_id, run_id)
        return envelopes.completed(request_id, data={
            "run_id": run_id, "cancel_requested": True,
            "note": "Cancellation is cooperative; a step already running may finish, but its "
                    "output is not published as an active recommendation.",
        })

    def get_scenario(self, principal: Principal, request_id: str,
                     evidence_ref: str) -> Dict[str, Any]:
        package = self.store.get_artifact(principal.tenant_id, evidence_ref)
        if package is None:
            return envelopes.blocked(request_id, codes.ACCESS_DENIED,
                                     "The requested result is not available to this principal.")
        return envelopes.completed(request_id, evidence_ref=evidence_ref, data=package)
