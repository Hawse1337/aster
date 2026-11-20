"""Run a small dry optimisation study without AEDT.

    python examples/dry_run_demo.py --samples 128
"""
from __future__ import annotations

import argparse
import math


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=64)
    args = parser.parse_args()

    from antopt.core import Objective, ParamSpace, ParamSpec, latin_hypercube
    from antopt.optimize import Evaluation, pareto_archive

    space = ParamSpace([
        ParamSpec("r", "mm", 30.0, 70.0, 50.0),
        ParamSpec("h", "mm", 1.0, 12.0, 6.0),
        ParamSpec("pitch", "mm", 1.2, 4.0, 2.4),
    ])
    objectives = [
        Objective("gain_obj", "peak_gain", "maximize"),
        Objective("bw_obj", "s11_bandwidth", "maximize"),
    ]

    evaluations = []
    for index, params in enumerate(latin_hypercube(space, args.samples, seed=7)):
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
    print("%d candidates, %d Pareto-optimal" % (len(evaluations), len(archive)))
    for evaluation in archive[:10]:
        print("  gain %.2f dBi  bandwidth %.2f GHz  %s" % (
            evaluation.metrics["peak_gain"], evaluation.metrics["s11_bandwidth"],
            {key: round(value, 2) for key, value in evaluation.params.items()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
