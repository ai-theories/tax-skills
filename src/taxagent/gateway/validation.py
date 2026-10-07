"""Contract validation at the boundary.

Structured output from a model is untrusted until it validates. Unknown fields
are rejected rather than ignored, and an unknown major schema version is
refused instead of being coerced.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from jsonschema import Draft202012Validator

SUPPORTED_MAJOR = 1


class ContractError(ValueError):
    def __init__(self, message: str, errors: Optional[list] = None):
        super().__init__(message)
        self.errors = errors or []


class ContractValidator:
    def __init__(self, directory: str):
        self.directory = directory
        self._schemas: Dict[str, Dict[str, Any]] = {}
        for name in sorted(os.listdir(directory)):
            if name.endswith(".schema.json"):
                with open(os.path.join(directory, name), "r", encoding="utf-8") as handle:
                    self._schemas[name[: -len(".schema.json")]] = json.load(handle)

    def schema(self, name: str) -> Dict[str, Any]:
        if name not in self._schemas:
            raise ContractError(f"unknown contract: {name}")
        return self._schemas[name]

    def names(self):
        return sorted(self._schemas)

    def validate(self, name: str, payload: Dict[str, Any]) -> None:
        version = str(payload.get("schema_version", "1.0"))
        major = int(version.split(".")[0])
        if major != SUPPORTED_MAJOR:
            raise ContractError(
                f"{name}: schema_version {version} is not supported by this release "
                f"(major {SUPPORTED_MAJOR})")
        validator = Draft202012Validator(self.schema(name))
        errors = [
            {"path": "/".join(str(p) for p in error.path), "message": error.message}
            for error in sorted(validator.iter_errors(payload), key=lambda e: list(e.path))
        ]
        if errors:
            raise ContractError(f"{name}: payload does not satisfy the contract", errors)
