"""
-------------------
- requirements.py -
-------------------

Requirement: something a plugin needs installed (a program, a Python module), so udl can say what
is missing (`udl plugins list`) instead of failing when the plugin runs
"""

# System Imports
import importlib.util
import shutil
from collections.abc import Iterable
from enum import StrEnum
from typing import Self

# Package Imports
from pydantic import BaseModel, ConfigDict


class RequirementKind(StrEnum):
    BINARY = "binary"  # an executable on PATH
    MODULE = "module"  # an importable Python module


class Requirement(BaseModel):
    """Something a plugin needs installed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: RequirementKind
    name: str
    hint: str | None = None  # how to install it

    @classmethod
    def binary(cls, name: str, *, hint: str | None = None) -> Self:
        return cls(kind=RequirementKind.BINARY, name=name, hint=hint)

    @classmethod
    def module(cls, name: str, *, hint: str | None = None) -> Self:
        return cls(kind=RequirementKind.MODULE, name=name, hint=hint)

    def met(self) -> bool:
        if self.kind is RequirementKind.BINARY:
            return shutil.which(self.name) is not None
        try:
            return importlib.util.find_spec(self.name) is not None
        except ModuleNotFoundError:  # a parent package is missing
            return False


def unmet(requirements: Iterable[Requirement]) -> list[Requirement]:
    """The requirements that are not met."""
    return [requirement for requirement in requirements if not requirement.met()]
