"""A dry aster study with no AEDT — good first run for an agent.

    python dry_demo.py --samples 64
"""
from __future__ import annotations

import argparse
import math


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=64)
    args = parser.parse_args()

    from aster.core import Objective, ParamSpace, ParamSpec, latin_hypercube
    from aster.optimize import Evaluation, pareto_archive

    space = ParamSpace([
        ParamSpec("ring_radius", "mm", 20.0, 60.0, 40.0),
        ParamSpec("ring_height", "mm", 1.0, 10.0, 4.0),
        ParamSpec("slot_depth", "mm", 0.0, 6.0, 2.0),
    ])
    objectives = [
        Objective("gain_obj", "peak_gain", "maximize"),
        Objective("bw_obj", "s11_bandwidth", "maximize"),
    ]

    evaluations = []
    for index, params in enumerate(latin_hypercube(space, args.samples, seed=7)):
        radius, height, depth = params["ring_radius"], params["ring_height"], params["slot_depth"]
        gain = 7.0 - 0.012 * (radius - 42.0) ** 2 - 0.08 * (height - 5.0) ** 2 + 0.4 * math.sin(depth)
        bandwidth = 7.0 - 0.03 * abs(depth - 2.0) - 0.01 * abs(radius - 45.0)
        evaluations.append(Evaluation(
            candidate_id="c%03d" % index,
            family_id="dry-demo",
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
