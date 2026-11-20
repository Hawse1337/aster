"""Antenna optimisation layer (``aster``).

Generic by design: a design case is a parameter space + constraints +
objectives + a case config. Antenna-specific knowledge lives in ``cases`` and
``domains``; nothing here imports PyAEDT.
"""
from __future__ import annotations

from .core import (
    Candidate,
    CaseSpec,
    Constraint,
    FamilySpec,
    Objective,
    ParamSpace,
    ParamSpec,
    latin_hypercube,
    sample_population,
)
from .optimize import (
    Evaluation,
    ParetoArchive,
    compare,
    dominates,
    evaluate_constraints,
    fast_non_dominated_sort,
    hypervolume_2d,
    pareto_archive,
)

__all__ = [
    "Candidate",
    "CaseSpec",
    "Constraint",
    "Evaluation",
    "FamilySpec",
    "Objective",
    "ParamSpace",
    "ParamSpec",
    "ParetoArchive",
    "compare",
    "dominates",
    "evaluate_constraints",
    "fast_non_dominated_sort",
    "hypervolume_2d",
    "latin_hypercube",
    "pareto_archive",
    "sample_population",
]
