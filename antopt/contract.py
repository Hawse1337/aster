"""Wire contract between the design system and the AEDT guard service.

This module intentionally does **not** import ``aedt_guard``: the design system
must be able to run against any deployed guard version that accepts the same
JSON contract. It re-states the schema it produces so a candidate is rejected
locally before it ever reaches the simulation service; the authoritative
validation still happens on the server side.
"""
from __future__ import annotations

from typing import Any

SCHEMA_VERSION = 1

DEFAULT_EVALUATION = {
    "theta_cut_phi_deg": [0, 90],
    "horiz_theta_deg": 90,
    "main_lobe_exclusion_deg": 10,
    "min_freq_points": 9,
    "realized_gain_mode": "gain_plus_mismatch",
}

DEFAULT_EXECUTION_POLICY = {
    "max_run_minutes": 60,
    "save_before_solve": False,
    "require_ff_sphere": True,
    "blocking_timeout_s": 3600,
    "require_convergence": True,
    "num_cores": None,
    "num_tasks": None,
    "num_gpus": None,
    "enforce_cpu_budget": False,
    "max_total_cores": 32,
}

REQUIRED_SPEC_FIELDS = (
    "schema_version",
    "project",
    "design_type",
    "version",
    "frequency_ghz",
    "metrics",
    "evaluation",
    "execution_policy",
)

KNOWN_METRIC_KINDS = {
    "s11",
    "realized_gain_h",
    "peak_gain",
    "ripple",
    "sll",
    "beam_theta",
    "effective_bandwidth",
    "s11_bandwidth",
    "directivity_db",
    "directivity_horiz_db",
    "radiation_efficiency",
}

OPERATORS = {"<=", ">=", "=="}


def validate_spec(spec: dict[str, Any]) -> list[str]:
    """Return a list of problems; empty means the spec may be submitted."""
    problems: list[str] = []
    if not isinstance(spec, dict):
        return ["spec must be an object"]
    for field in REQUIRED_SPEC_FIELDS:
        if field not in spec:
            problems.append(f"missing field: {field}")
    if problems:
        return problems
    if spec["schema_version"] != SCHEMA_VERSION:
        problems.append(f"schema_version must be {SCHEMA_VERSION}")
    band = spec["frequency_ghz"]
    if (
        not isinstance(band, (list, tuple))
        or len(band) != 2
        or not all(isinstance(v, (int, float)) for v in band)
        or band[0] >= band[1]
    ):
        problems.append("frequency_ghz must be [low, high] with low < high")
    metrics = spec["metrics"]
    if not isinstance(metrics, list) or not metrics:
        problems.append("metrics must be a non-empty list")
        return problems
    seen: set[str] = set()
    for index, metric in enumerate(metrics):
        where = f"metrics[{index}]"
        if not isinstance(metric, dict):
            problems.append(f"{where} must be an object")
            continue
        metric_id = metric.get("id")
        if not isinstance(metric_id, str) or not metric_id:
            problems.append(f"{where}.id must be a non-empty string")
        elif metric_id in seen:
            problems.append(f"duplicate metric id: {metric_id}")
        else:
            seen.add(metric_id)
        if metric.get("kind") not in KNOWN_METRIC_KINDS:
            problems.append(f"{where}.kind is unknown: {metric.get('kind')!r}")
        if metric.get("operator") not in OPERATORS:
            problems.append(f"{where}.operator must be one of {sorted(OPERATORS)}")
        if not isinstance(metric.get("target"), (int, float)):
            problems.append(f"{where}.target must be numeric")
    objectives = spec.get("objectives")
    if objectives is not None:
        if not isinstance(objectives, list) or not objectives:
            problems.append("objectives must be a non-empty list when present")
        else:
            kinds = {m.get("kind") for m in metrics if isinstance(m, dict)}
            for index, objective in enumerate(objectives):
                where = f"objectives[{index}]"
                if not isinstance(objective, dict):
                    problems.append(f"{where} must be an object")
                    continue
                if objective.get("metric") not in seen:
                    problems.append(f"{where}.metric must reference a metric id")
                if objective.get("direction") not in {"maximize", "minimize"}:
                    problems.append(f"{where}.direction must be maximize/minimize")
            _ = kinds
    evaluation = spec["evaluation"]
    if not isinstance(evaluation, dict):
        problems.append("evaluation must be an object")
    else:
        for key, default in DEFAULT_EVALUATION.items():
            value = evaluation.get(key, default)
            if key == "theta_cut_phi_deg":
                if not isinstance(value, (list, tuple)) or not value:
                    problems.append("evaluation.theta_cut_phi_deg must be a non-empty list")
            elif key == "min_freq_points":
                if not isinstance(value, int) or value < 2:
                    problems.append("evaluation.min_freq_points must be an integer >= 2")
            elif key == "realized_gain_mode":
                if value not in {"gain_plus_mismatch", "reported"}:
                    problems.append("evaluation.realized_gain_mode is unknown")
            elif not isinstance(value, (int, float)):
                problems.append(f"evaluation.{key} must be numeric")
        frequencies = evaluation.get("frequencies_ghz")
        if frequencies is not None:
            if not isinstance(frequencies, (list, tuple)) or not frequencies:
                problems.append("evaluation.frequencies_ghz must be a non-empty list")
            elif any(not isinstance(v, (int, float)) for v in frequencies):
                problems.append("evaluation.frequencies_ghz must be numeric")
            elif len(frequencies) > int(evaluation.get("max_frequency_points", 9)):
                problems.append("evaluation.frequencies_ghz exceeds max_frequency_points")
    policy = spec["execution_policy"]
    if not isinstance(policy, dict):
        problems.append("execution_policy must be an object")
    return problems

