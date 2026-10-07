"""Run lifecycle: transitions, idempotency, retries, cancellation, recovery."""
import pytest

from taxagent.coordinator.retries import RetryPolicy, TransientError
from taxagent.coordinator.runner import WorkflowRunner
from taxagent.coordinator.state_machine import (ALLOWED, InvalidTransition, RunState,
                                                assert_transition)
from taxagent.coordinator.template_registry import Step, TemplateError, WorkflowTemplate


def _template(steps=None):
    return WorkflowTemplate("t.v1", "analysis_only", "account", tuple(steps or [
        Step("a", "op_a"), Step("b", "op_b", ("a",)),
    ]))


def test_illegal_transition_raises():
    with pytest.raises(InvalidTransition):
        assert_transition(RunState.COMPLETED, RunState.RUNNING)


def test_failed_and_cancelled_are_terminal():
    assert ALLOWED[RunState.FAILED] == frozenset()
    assert ALLOWED[RunState.CANCELLED] == frozenset()


def test_blocked_can_resume_through_validated():
    assert RunState.VALIDATED in ALLOWED[RunState.BLOCKED]


def test_cycle_in_template_is_rejected():
    template = WorkflowTemplate("cycle.v1", "analysis_only", "account",
                                (Step("a", "op_a", ("b",)), Step("b", "op_b", ("a",))))
    with pytest.raises(TemplateError):
        template.ordered_steps()


def test_unknown_dependency_is_rejected():
    template = WorkflowTemplate("x.v1", "analysis_only", "account",
                                (Step("a", "op_a", ("nope",)),))
    with pytest.raises(TemplateError):
        template.ordered_steps()


def test_steps_run_in_dependency_order(store):
    order = []
    handlers = {"op_a": lambda c: (order.append("a"), c)[1],
                "op_b": lambda c: (order.append("b"), c)[1]}
    run, _ = store.create_or_get("t1", "key", {})
    outcome = WorkflowRunner(store, handlers).execute(
        "t1", run["run_id"], _template([Step("b", "op_b", ("a",)), Step("a", "op_a")]), {})
    assert order == ["a", "b"]
    assert outcome.state is RunState.REVIEW_READY


def test_transient_failure_retries_then_succeeds(store):
    attempts = {"n": 0}

    def flaky(context):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise TransientError("connector timeout")
        return context

    run, _ = store.create_or_get("t1", "key", {})
    runner = WorkflowRunner(store, {"op_a": flaky, "op_b": lambda c: c},
                            RetryPolicy(max_attempts=3))
    outcome = runner.execute("t1", run["run_id"], _template(), {})
    assert outcome.state is RunState.REVIEW_READY
    assert attempts["n"] == 3
    recorded = [s for s in store.steps("t1", run["run_id"]) if s["step_id"] == "a"]
    assert [s["status"] for s in recorded] == ["transient_failure", "transient_failure", "succeeded"]


def test_calculation_error_does_not_retry(store):
    attempts = {"n": 0}

    def broken(context):
        attempts["n"] += 1
        raise ZeroDivisionError("defect")

    run, _ = store.create_or_get("t1", "key", {})
    outcome = WorkflowRunner(store, {"op_a": broken, "op_b": lambda c: c}).execute(
        "t1", run["run_id"], _template(), {})
    assert outcome.state is RunState.FAILED
    assert attempts["n"] == 1   # a defect is not retried


def test_cancellation_stops_before_the_next_step(store):
    run, _ = store.create_or_get("t1", "key", {})
    run_id = run["run_id"]

    def first(context):
        store.request_cancel("t1", run_id)
        return context

    outcome = WorkflowRunner(store, {"op_a": first, "op_b": lambda c: c}).execute(
        "t1", run_id, _template(), {})
    assert outcome.state is RunState.CANCELLED
    assert "b" not in [s["step_id"] for s in store.steps("t1", run_id)]


def test_events_are_sequenced_for_replay(store):
    run, _ = store.create_or_get("t1", "key", {})
    WorkflowRunner(store, {"op_a": lambda c: c, "op_b": lambda c: c}).execute(
        "t1", run["run_id"], _template(), {})
    events = store.events("t1", run["run_id"])
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert events[0]["type"] == "state_changed"


def test_idempotency_key_returns_the_same_run(store):
    first, created_first = store.create_or_get("t1", "same-key", {"a": 1})
    second, created_second = store.create_or_get("t1", "same-key", {"a": 1})
    assert created_first is True and created_second is False
    assert first["run_id"] == second["run_id"]
