"""Interoperability helpers for the yadof framework (v0.8.3 and later).

yadof (https://github.com/YS-Pan/yadof) runs simulation-based design studies from a workspace:
``job_template/parameters_constraints.py`` declares the variables and ranges,
``job_template/workflow.py`` runs the simulator, ``submit/calc_cost.py`` turns raw outputs into
normalised objectives and ``submit/optimization.py`` owns the search. Its starter supports GA
for a single objective and NSGA-III for several, so the two systems speak the same language at
the two places this module connects them:

* :func:`parameters_constraints_source` writes our parameter definitions out in the shape the
  yadof workspace expects, so a yadof run describes exactly the variables our model drives;
* :func:`space_from_optimization_setup` reads back a table of AEDT variables (name, value,
  range, Include flag - the convention of yadof's ``aedt_parameters_gui``) into our
  :class:`~aster.core.ParamSpace`, exporting only the checked variables;
* :func:`to_recorded_data` writes evaluated candidates as recorded rows (parameters plus raw
  outputs plus cost), which is the interchange format yadof keeps under ``recorded_data/``
  and re-scores with ``calc_cost.py`` without re-running the simulator.
"""
from __future__ import annotations

import json
import pprint
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..core import ParamSpec, ParamSpace, SpecError

__all__ = [
    "parameters_constraints_source",
    "space_from_optimization_setup",
    "to_recorded_data",
    "write_recorded_data",
]

_KIND_TO_YADOF = {"float": "continuous", "int": "integer", "choice": "discrete"}


def parameters_constraints_source(
    space: ParamSpace,
    *,
    constraints: Sequence[Any] = (),
    module_name: str = "lens28_var",
) -> str:
    """Render a `job_template/parameters_constraints.py` for a yadof workspace.

    Written against the installed yadof 0.8.3 contract: `PARAMETERS` holds `Parameter`
    objects (name, ranges, unit) and `get_parameters()` returns them; `CONSTRAINTS` stays
    empty because yadof constraints are free-form strings for its own sampler, while our real
    feasibility rules live in `submit/calc_cost.py` (a violated run is charged the error
    cost), which is where they can actually see the metrics.
    """
    lines = [
        '"""Parameter definitions for the yadof workspace.',
        '',
        "Generated from the aster parameter file by",
        "`aster.interop.yadof.parameters_constraints_source` - edit the aster definition",
        "and regenerate instead of editing this file, or the two systems will diverge.",
        '"""',
        'from __future__ import annotations',
        '',
        'from yadof.job_template import Parameter',
        '',
        'PARAMETERS = (',
    ]
    for spec in space.params:
        if spec.kind == "choice":
            ranges = "((%r, %r),)" % (min(spec.choices), max(spec.choices))
        else:
            ranges = "((%r, %r),)" % (float(spec.low), float(spec.high))
        lines.append(
            "    Parameter(%r, %s, unit=%r)," % (spec.name, ranges, spec.unit))
    lines.extend([
        ')',
        '',
        'CONSTRAINTS = ()',
        '',
        '',
        'def get_parameters() -> tuple[Parameter, ...]:',
        '    return tuple(PARAMETERS)',
        '',
        '',
        'def describe() -> dict[str, object]:',
        '    """Physics notes from the aster definition, for humans reading the workspace."""',
        '    return %r' % ({spec.name: spec.physics for spec in space.params},),
        '',
    ])
    return "\n".join(lines)


def space_from_optimization_setup(
    rows: Iterable[Mapping[str, Any]],
    *,
    include_only: bool = True,
    group: str = "aedt",
) -> ParamSpace:
    """Build our parameter space from an AEDT variable table.

    Each row may carry ``name``, ``value``/``default``, ``low``/``high`` (or ``min``/``max``),
    ``unit`` and ``include``. With ``include_only`` the un-checked variables are skipped, which
    mirrors yadof's ``aedt_parameters_gui``: only the variables ticked in ``OptimizationSetup1``
    are exported, everything else stays a fixed design constant.
    """
    specs: list[ParamSpec] = []
    seen: set[str] = set()
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name:
            raise SpecError("row without a name")
        if name in seen:
            raise SpecError(f"duplicate parameter {name}")
        include = row.get("include", True)
        if include_only and not include:
            continue
        seen.add(name)
        low = row.get("low", row.get("min"))
        high = row.get("high", row.get("max"))
        default = row.get("value", row.get("default"))
        if low is None or high is None:
            raise SpecError(f"parameter {name} needs low/high")
        if default is None:
            raise SpecError(f"parameter {name} needs a value")
        kind = str(row.get("type") or "float").lower()
        if kind in {"int", "integer"}:
            kind = "int"
        elif kind in {"discrete", "choice", "categorical"}:
            kind = "choice"
        else:
            kind = "float"
        choices = tuple(row.get("choices") or ())
        if kind == "choice" and not choices:
            # a discrete AEDT variable is usually an enumerated list, but a stepped integer
            # stays an integer; refuse silently guessing and ask for the list instead
            raise SpecError(f"parameter {name}: choice parameters need choices")
        specs.append(
            ParamSpec(
                name=name,
                unit=str(row.get("unit") or "mm"),
                low=float(low),
                high=float(high),
                default=default if kind == "choice" else float(default),
                kind=kind,
                choices=choices,
                physics=str(row.get("physics") or ""),
                manufacturing=dict(row.get("manufacturing") or {}),
            )
        )
    if not specs:
        raise SpecError("no parameters selected")
    return ParamSpace(specs)


def to_recorded_data(
    entries: Iterable[Any],
    *,
    objective_names: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Convert evaluated candidates into yadof-style recorded rows.

    Each row keeps the parameters, every raw output we measured, the feasibility verdict and
    the cost value, so a yadof task can re-score the same run against a revised target without
    touching the solver again.
    """
    rows: list[dict[str, Any]] = []
    for entry in entries:
        metrics = dict(getattr(entry, "metrics", {}) or {})
        names = list(objective_names) if objective_names else sorted(metrics)
        cost = sum(float(metrics.get(name, 0.0) or 0.0) for name in names)
        rows.append(
            {
                "candidate_id": getattr(entry, "candidate_id", None),
                "family_id": getattr(entry, "family_id", None),
                "parameters": dict(getattr(entry, "params", {}) or {}),
                "outputs": metrics,
                "feasible": bool(getattr(entry, "feasible", False)),
                "violation": float(getattr(entry, "total_violation", 0.0) or 0.0),
                "cost": cost,
                "status": getattr(entry, "status", "UNKNOWN"),
            }
        )
    return rows


def write_recorded_data(path: str | Path, rows: Sequence[Mapping[str, Any]]) -> Path:
    """Write recorded rows as JSONL, one evaluation per line."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
    return target
