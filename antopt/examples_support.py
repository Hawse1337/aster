"""Helpers used by the examples and the ``antopt dry-demo`` command (no AEDT needed)."""
from __future__ import annotations

import math


def dry_demo(samples: int = 64) -> int:
    from .core import Objective, ParamSpace, ParamSpec, latin_hypercube
    from .optimize import Evaluation, pareto_archive

    space = ParamSpace([
        ParamSpec("r", "mm", 30.0, 70.0, 50.0),
        ParamSpec("h", "mm", 1.0, 12.0, 6.0),
        ParamSpec("pitch", "mm", 1.2, 4.0, 2.4),
    ])
    points = latin_hypercube(space, max(8, int(samples)), seed=7)
    objectives = [
        Objective(id="gain_obj", kind="peak_gain", direction="maximize"),
        Objective(id="bw_obj", kind="s11_bandwidth", direction="maximize"),
    ]
    evaluations = []
    for index, params in enumerate(points):
        r, h, pitch = params["r"], params["h"], params["pitch"]
        gain = 7.0 - 0.012 * (r - 52.0) ** 2 - 0.08 * (h - 6.0) ** 2 + 0.4 * math.sin(pitch)
        bandwidth = 7.0 - 0.03 * abs(pitch - 2.0) - 0.01 * abs(r - 55.0)
        evaluations.append(Evaluation(
            candidate_id="c%03d" % index,
            family_id="demo",
            params=params,
            metrics={"peak_gain": gain, "s11_bandwidth": bandwidth},
            feasible=gain > 4.0,
        ))
    archive = pareto_archive(evaluations, objectives)
    print("dry demo: %d candidates, %d Pareto-optimal" % (len(evaluations), len(archive)))
    for evaluation in archive[:10]:
        print(" gain %.2f dBi  bandwidth %.2f GHz  params=%s" % (
            evaluation.metrics["peak_gain"], evaluation.metrics["s11_bandwidth"],
            {key: round(value, 2) for key, value in evaluation.params.items()}))
    return 0
