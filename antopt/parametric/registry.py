"""Case registry: adding an antenna must not touch the core.

A case registers:

* ``case_factory``  → ``CaseSpec`` (parameters, constraints, objectives);
* ``build_model``   → ``ParametricModel`` (operations / variable updates);
* optional ``knowledge`` module path for domain helpers (dispersion, aperture).

The core code only ever sees ``CaseSpec`` and ``ParametricModel``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..core import CaseSpec


@dataclass
class CaseRegistration:
    name: str
    case_factory: Callable[[], CaseSpec]
    model_factory: Callable[[], Any] | None = None
    knowledge: str | None = None
    antenna_type: str = ""
    notes: str = ""
    unavailable_metrics: dict[str, str] = field(default_factory=dict)
    pending_metrics: dict[str, str] = field(default_factory=dict)
    """Metrics whose availability is being verified - shown as '接入验证中', not '不可用'."""
    """Metrics this case cannot produce, mapped to the reason; the page shows them as disabled."""

    def build_case(self) -> CaseSpec:
        case = self.case_factory()
        case.validate()
        return case

    def build_model(self) -> Any:
        if self.model_factory is None:
            raise LookupError(f"case {self.name!r} has no parametric model registered")
        return self.model_factory()


_REGISTRY: dict[str, CaseRegistration] = {}


def register_case(registration: CaseRegistration) -> CaseRegistration:
    if registration.name in _REGISTRY:
        raise ValueError(f"case already registered: {registration.name}")
    _REGISTRY[registration.name] = registration
    return registration


def get_case(name: str) -> CaseRegistration:
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"unknown case {name!r}; registered: {sorted(_REGISTRY)}") from exc


def list_cases() -> list[str]:
    return sorted(_REGISTRY)


def clear_registry() -> None:
    """Test helper: forget all registrations."""
    _REGISTRY.clear()
