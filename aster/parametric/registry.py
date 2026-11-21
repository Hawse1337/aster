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
    if name in _REGISTRY:
        return _REGISTRY[name]
    from importlib import import_module, metadata, util
    import re
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,95}", name):
        raise KeyError("invalid case identifier")
    builtin = __package__.rsplit(".", 1)[0] + ".cases." + name
    if name.isidentifier() and util.find_spec(builtin):
        factory = getattr(import_module(builtin), "register", None)
    else:
        entries = list(metadata.entry_points(group="aster.antenna_cases", name=name))
        if len(entries) > 1:
            raise ValueError(f"ambiguous installed case: {name}")
        factory = entries[0].load() if entries else None
    if factory is None:
        raise KeyError(f"unknown case {name!r}; registered: {sorted(_REGISTRY)}")
    registration = factory()
    if registration.name != name:
        raise ValueError("case registration returned a different name")
    return _REGISTRY.setdefault(name, registration)


def list_cases() -> list[str]:
    return sorted(_REGISTRY)


def clear_registry() -> None:
    """Test helper: forget all registrations."""
    _REGISTRY.clear()
