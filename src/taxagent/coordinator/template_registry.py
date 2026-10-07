"""Workflow template loading and validation.

A template is a declarative dependency graph of approved operations. It cannot
express arbitrary code, and an operation the backend does not implement is a
load-time error rather than a runtime surprise.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple

import yaml


class TemplateError(ValueError):
    pass


@dataclass(frozen=True)
class Step:
    step_id: str
    operation: str
    requires: Tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowTemplate:
    template_id: str
    mode: str
    required_scope: str
    steps: Tuple[Step, ...]
    on_unsupported: str = "blocked"
    on_infeasible: str = "return_diagnostics"

    def ordered_steps(self) -> List[Step]:
        """Topological order; raises on a cycle or an unknown dependency."""
        by_id = {s.step_id: s for s in self.steps}
        ordered: List[Step] = []
        visiting: set = set()
        done: set = set()

        def visit(step: Step) -> None:
            if step.step_id in done:
                return
            if step.step_id in visiting:
                raise TemplateError(f"cycle at step {step.step_id}")
            visiting.add(step.step_id)
            for dependency in step.requires:
                if dependency not in by_id:
                    raise TemplateError(
                        f"step {step.step_id} requires unknown step {dependency}")
                visit(by_id[dependency])
            visiting.discard(step.step_id)
            done.add(step.step_id)
            ordered.append(step)

        for step in self.steps:
            visit(step)
        return ordered


class TemplateRegistry:
    def __init__(self, templates: Dict[str, WorkflowTemplate]):
        self._templates = templates

    @classmethod
    def load(cls, directory: str, supported_operations: Sequence[str]) -> "TemplateRegistry":
        templates: Dict[str, WorkflowTemplate] = {}
        for name in sorted(os.listdir(directory)):
            if not name.endswith((".yaml", ".yml")):
                continue
            with open(os.path.join(directory, name), "r", encoding="utf-8") as handle:
                raw = yaml.safe_load(handle)
            steps = tuple(
                Step(s["id"], s["operation"], tuple(s.get("requires", [])))
                for s in raw.get("steps", [])
            )
            template = WorkflowTemplate(
                template_id=raw["id"], mode=raw.get("mode", "analysis_only"),
                required_scope=raw.get("required_scope", "account"), steps=steps,
                on_unsupported=raw.get("on_unsupported", "blocked"),
                on_infeasible=raw.get("on_infeasible", "return_diagnostics"),
            )
            if template.mode != "analysis_only":
                raise TemplateError(
                    f"{template.template_id}: this release supports analysis_only templates")
            for step in template.steps:
                if step.operation not in supported_operations:
                    raise TemplateError(
                        f"{template.template_id}: operation '{step.operation}' is not implemented")
            template.ordered_steps()
            templates[template.template_id] = template
        return cls(templates)

    def get(self, template_id: str) -> WorkflowTemplate:
        if template_id not in self._templates:
            raise TemplateError(f"unknown workflow template: {template_id}")
        return self._templates[template_id]

    def ids(self) -> List[str]:
        return sorted(self._templates)
