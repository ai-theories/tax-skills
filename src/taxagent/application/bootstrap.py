"""Compose the service from on-disk registries.

Kept separate from AnalysisService so tests can assemble the same object graph
with a temporary store and no configuration files.
"""
from __future__ import annotations

import os
from typing import Iterable, Optional

from ..coordinator.template_registry import TemplateRegistry
from ..domain.identities import Account, Subject
from ..gateway.authorization import ScopeAuthorizer
from ..gateway.capability_registry import CapabilityRegistry
from ..storage.sqlite import SqliteStore
from .analysis_service import OPERATIONS, AnalysisService

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
CAPABILITIES_DIR = os.path.join(PROJECT_ROOT, "capabilities")
WORKFLOWS_DIR = os.path.join(PROJECT_ROOT, "workflows")
CONTRACTS_DIR = os.path.join(PROJECT_ROOT, "contracts", "v1")


def build_service(
    subjects: Iterable[Subject],
    accounts: Iterable[Account],
    store: Optional[SqliteStore] = None,
    capabilities_dir: str = CAPABILITIES_DIR,
    workflows_dir: str = WORKFLOWS_DIR,
) -> AnalysisService:
    capabilities = CapabilityRegistry.load(capabilities_dir)
    templates = TemplateRegistry.load(workflows_dir, OPERATIONS)
    return AnalysisService(
        store=store or SqliteStore(),
        authorizer=ScopeAuthorizer(subjects, accounts),
        capabilities=capabilities,
        templates=templates,
    )
