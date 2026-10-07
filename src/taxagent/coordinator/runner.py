"""Durable workflow execution.

The runner owns state transitions, dependency order, attempt history,
checkpoints and cooperative cancellation. Step handlers are ordinary functions
that receive and return a context dictionary; they never move the run's state
themselves.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from ..domain import codes
from ..decisions.rule_registry import RulePackError
from ..domain.constraints import AmbiguousConstraint
from ..optimization.interface import BudgetUnavailable, UnsupportedCapability
from .retries import PermanentError, RetryPolicy, TransientError
from .state_machine import RunState, TERMINAL, assert_transition
from .template_registry import WorkflowTemplate

StepHandler = Callable[[Dict[str, Any]], Dict[str, Any]]


class CancelledRun(RuntimeError):
    pass


@dataclass
class RunOutcome:
    run_id: str
    state: RunState
    context: Dict[str, Any]
    code: Optional[str] = None
    message: str = ""
    unresolved_fields: tuple = ()
    supported_alternative: str = ""

    def to_json(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id, "state": self.state.value, "code": self.code,
            "message": self.message, "unresolved_fields": list(self.unresolved_fields),
            "supported_alternative": self.supported_alternative or None,
        }


class WorkflowRunner:
    def __init__(self, store, handlers: Dict[str, StepHandler],
                 retry_policy: Optional[RetryPolicy] = None):
        self.store = store
        self.handlers = handlers
        self.retry_policy = retry_policy or RetryPolicy()

    def _transition(self, tenant_id: str, run_id: str, current: RunState,
                    target: RunState, detail: Optional[Dict[str, Any]] = None) -> RunState:
        assert_transition(current, target)
        self.store.set_state(tenant_id, run_id, target.value, detail)
        self.store.append(tenant_id, run_id, {
            "type": "state_changed", "from": current.value, "to": target.value,
            "at": datetime.now(timezone.utc).isoformat(), "detail": detail or {},
        })
        return target

    def execute(self, tenant_id: str, run_id: str, template: WorkflowTemplate,
                context: Dict[str, Any]) -> RunOutcome:
        run = self.store.get(tenant_id, run_id)
        if run is None:
            raise PermanentError(f"run {run_id} not found for this tenant")

        state = RunState(run["state"])
        if state in TERMINAL:
            return RunOutcome(run_id, state, context, message="Run already finished.")

        if state is RunState.RECEIVED:
            state = self._transition(tenant_id, run_id, state, RunState.VALIDATED)
        if state is RunState.VALIDATED:
            state = self._transition(tenant_id, run_id, state, RunState.QUEUED)
        if state is RunState.QUEUED:
            state = self._transition(tenant_id, run_id, state, RunState.RUNNING)

        completed: set = set()
        for step in template.ordered_steps():
            if self.store.is_cancel_requested(tenant_id, run_id):
                self._transition(tenant_id, run_id, state, RunState.CANCELLED,
                                 {"cancelled_before_step": step.step_id})
                return RunOutcome(run_id, RunState.CANCELLED, context,
                                  message="Cancelled before completing all steps.")

            missing = [d for d in step.requires if d not in completed]
            if missing:
                self._transition(tenant_id, run_id, state, RunState.FAILED,
                                 {"step": step.step_id, "missing_dependencies": missing})
                return RunOutcome(run_id, RunState.FAILED, context, codes.VALIDATION_FAILED,
                                  f"Step {step.step_id} ran before {missing}.")

            handler = self.handlers[step.operation]
            attempt = 0
            while True:
                attempt += 1
                try:
                    context = handler(context)
                    self.store.record_step(tenant_id, run_id, step.step_id, attempt, "succeeded")
                    self.store.append(tenant_id, run_id, {
                        "type": "step_succeeded", "step_id": step.step_id, "attempt": attempt,
                        "operation": step.operation,
                    })
                    completed.add(step.step_id)
                    break

                except AmbiguousConstraint as exc:
                    self.store.record_step(tenant_id, run_id, step.step_id, attempt,
                                           "needs_input", {"fields": exc.unresolved_fields})
                    self._transition(tenant_id, run_id, state, RunState.BLOCKED,
                                     {"step": step.step_id, "code": codes.AMBIGUOUS_CONSTRAINT})
                    # BLOCKED is repairable; NEEDS_INPUT is reported to the caller.
                    return RunOutcome(run_id, RunState.BLOCKED, context,
                                      codes.AMBIGUOUS_CONSTRAINT, str(exc),
                                      tuple(exc.unresolved_fields))

                except UnsupportedCapability as exc:
                    self.store.record_step(tenant_id, run_id, step.step_id, attempt,
                                           "unsupported", {"code": exc.code})
                    self._transition(tenant_id, run_id, state, RunState.BLOCKED,
                                     {"step": step.step_id, "code": exc.code})
                    return RunOutcome(run_id, RunState.BLOCKED, context, exc.code, str(exc),
                                      supported_alternative=exc.supported_alternative or "")

                except RulePackError as exc:
                    # No reviewed rule pack covers this request. Refusing is the
                    # point: computing from an assumed rule is the failure mode.
                    self.store.record_step(tenant_id, run_id, step.step_id, attempt,
                                           "blocked", {"code": exc.code})
                    self._transition(tenant_id, run_id, state, RunState.BLOCKED,
                                     {"step": step.step_id, "code": exc.code})
                    return RunOutcome(run_id, RunState.BLOCKED, context, exc.code, str(exc))

                except BudgetUnavailable as exc:
                    # Satisfiable in principle, but another run holds the capacity.
                    self.store.record_step(tenant_id, run_id, step.step_id, attempt,
                                           "blocked", {"code": exc.code})
                    self._transition(tenant_id, run_id, state, RunState.BLOCKED,
                                     {"step": step.step_id, "code": exc.code})
                    return RunOutcome(run_id, RunState.BLOCKED, context, exc.code, str(exc))

                except TransientError as exc:
                    self.store.record_step(tenant_id, run_id, step.step_id, attempt,
                                           "transient_failure", {"error": str(exc)})
                    if not self.retry_policy.should_retry(attempt, exc):
                        self._transition(tenant_id, run_id, state, RunState.FAILED,
                                         {"step": step.step_id,
                                          "code": codes.CONNECTOR_TEMPORARY_FAILURE})
                        return RunOutcome(run_id, RunState.FAILED, context,
                                          codes.CONNECTOR_TEMPORARY_FAILURE, str(exc))

                except Exception as exc:  # calculation or schema defect
                    self.store.record_step(tenant_id, run_id, step.step_id, attempt,
                                           "failed", {"error": str(exc)})
                    self._transition(tenant_id, run_id, state, RunState.FAILED,
                                     {"step": step.step_id, "error": str(exc)})
                    return RunOutcome(run_id, RunState.FAILED, context,
                                      codes.VALIDATION_FAILED, str(exc))

            self.store.append(tenant_id, run_id, {
                "type": "checkpoint", "step_id": step.step_id,
                "context_keys": sorted(context.keys()),
            })

        state = self._transition(tenant_id, run_id, state, RunState.REVIEW_READY)
        return RunOutcome(run_id, state, context, message="Ready for professional review.")
