"""Compare geometry-only and mixed-action DesignStudy scheduling on synthetic responses.

Example: python benchmark_study_actions.py --package aster --source-tree ./aster-source
         --output study-actions.json
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import importlib
import json
import math
from pathlib import Path
import sys


COST = {"geometry": 12, "frequency": 3, "coverage": 9, "tolerance": 12}
BUDGET = 36
FREQ = (26, 27, 28, 29, 30)
FF_FREQ = (26, 28, 30)
KINDS = tuple(COST)


def fingerprint(data):
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def phase(case, params):
    return params["ring_outer"] if case == "lens28_ref" else params["p_mm"]


def s11(case, params, frequency):
    return -15 + 0.35 * math.cos(phase(case, params) + 0.4 * (frequency - 28))


def gain(case, params, frequency, theta, phi):
    level = 6.5 if case == "lens28_ref" else 9.0
    return (level + 0.2 * math.cos(phase(case, params) + 0.4 * (frequency - 28))
            + 0.15 * math.cos(math.radians(phi)) + 0.25 * math.sin(math.radians(theta)))


def snapshot(case, params, run_id, frequency):
    theta = [90] if case == "lens28_ref" else [60, 70, 80]
    phi = list(range(0, 360, 10)) if case == "lens28_ref" else [0, 10, 20]
    return {"frequency_ghz": frequency, "run_id": run_id, "source": "MEASURED",
            "coordinate_system": "Global", "quantity": "dB(RealizedGainTotal)",
            "sphere": {"theta_deg": theta, "phi_deg": phi,
                       "gain_db": [[gain(case, params, frequency, t, p) for p in phi] for t in theta]}}


def fresh(case, params, run_id):
    case_freqs = range(24, 31) if case == "lens28_ref" else (25, 26.5, 28, 29.5, 31)
    horizon = [gain(case, params, f, 90, p) for f in case_freqs for p in range(0, 360, 10)]
    if case == "lens28_ref":
        # This response is below -10 dB everywhere in the 24–30 GHz case band.
        metrics = {"peak_gain": max(horizon), "bandwidth": 6, "ripple": 0.3}
    else:
        metrics = {"gain_h": min(horizon), "s11": max(s11(case, params, f) for f in case_freqs),
                   "ripple": 0.3}
    # These test fixtures exercise the measured-data branch in memory only.
    return {"run_id": run_id, "case": case, "recipe_id": "synthetic-oracle-v1", "status": "COMPLETED",
            "source": "MEASURED", "fidelity": "high", "params": dict(params), "metrics": metrics,
            "s11_points": [[f, s11(case, params, f)] for f in FREQ],
            "coverage_snapshots": [snapshot(case, params, run_id, f) for f in FF_FREQ]}


def setup(package, case):
    module = importlib.import_module(f"{package}.design_study")
    reg = importlib.import_module(f"{package}.cases.{case}").register()
    lens = case == "lens28_ref"
    profile = {
        "case_id": case, "profile_id": "synthetic-" + case, "revision": 1, "recipe_id": "synthetic-oracle-v1",
        "band": {"band_ghz": [26, 30], "max_gap_ghz": 1, "threshold_db": -10},
        "coverage": {"frequencies_ghz": list(FF_FREQ), "min_realized_gain_dbi": 5 if lens else 8,
                     "theta_deg": [90, 90] if lens else [60, 80], "phi_deg": [0, 360] if lens else [0, 20],
                     "coordinate_system": "Global", "max_step_deg": 10},
        "objectives": ["min_realized_gain_dbi", "band_margin_db"], "cost_seconds": COST,
    }
    if not lens:
        profile["tolerance"] = {"tolerances": {"p_mm": 0.1}, "required_repeats": 1}
        profile["weights"] = {"tolerance": 2}
    study = module.DesignStudy(reg.build_case(), reg.build_model(), profile)
    p = study.space.default()
    rows = [fresh(case, p, "initial-1")]
    if lens:
        rows.append(fresh(case, {**p, "ring_outer": 2.8}, "initial-2"))
        rows[0]["s11_points"] = [r for r in rows[0]["s11_points"] if r[0] != 28]
        rows[1]["coverage_snapshots"] = [s for s in rows[1]["coverage_snapshots"] if s["frequency_ghz"] != 28]
    return study, rows


def choose(study, rows, remaining, only_geometry, seed):
    # Both policies ask the unchanged planner for one next action. For the
    # restricted policy, mask each disallowed proposal and query again.
    hidden = set()
    anchor = "initial-1" if study.profile.get("tolerance") else None
    while True:
        report = study.plan(rows, budget_seconds=remaining, count=1, seed=seed,
                            pending_ids=hidden, anchor_run_id=anchor)
        if not report["actions"]:
            return None
        action, = report["actions"]
        if not only_geometry or action["kind"] == "geometry":
            return action
        if action["id"] in hidden:
            raise RuntimeError("planner repeated a masked action")
        hidden.add(action["id"])


def apply(case, rows, action):
    kind = action["kind"]
    if kind in {"geometry", "tolerance"}:
        run_id = "synthetic-" + action["id"][:20]
        rows.append(fresh(case, action["params"], run_id))
        return run_id
    row = next(r for r in rows if r["run_id"] == action["run_id"])
    frequency = action["frequency_ghz"]
    if kind == "frequency":
        if any(f == frequency for f, _ in row["s11_points"]):
            raise RuntimeError("redundant synthetic S11 request")
        row["s11_points"].append([frequency, s11(case, row["params"], frequency)])
    elif kind == "coverage":
        if any(s["frequency_ghz"] == frequency for s in row["coverage_snapshots"]):
            raise RuntimeError("redundant synthetic coverage request")
        row["coverage_snapshots"].append(snapshot(case, row["params"], row["run_id"], frequency))
    else:
        raise ValueError(f"unsupported benchmark action: {kind}")
    return row["run_id"]


def accepted(report, design_ids):
    nominal = {r["run_id"] for r in report["assessments"] if r["complete"] and r["sampled_pass"]}
    robust = {r["run_id"] for r in report["tolerance_studies"]
              if r["complete"] and r["scenario_pass_fraction"] == 1}
    return {"accepted_nominal_designs": len(nominal & design_ids),
            "accepted_tolerance_verified_designs": len(nominal & robust & design_ids),
            "accepted_geometry_points_including_test_articles": len(
                {fingerprint(r["params"]) for r in report["assessments"] if r["run_id"] in nominal})}


def trial(package, case, seed, only_geometry):
    study, initial = setup(package, case)
    rows = deepcopy(initial)
    design_ids = {r["run_id"] for r in initial}
    initial_report = study.plan(rows, budget_seconds=BUDGET, count=1, seed=seed)
    before = accepted(initial_report, design_ids)
    trace = []
    remaining = BUDGET
    geometry_count = 0
    while remaining:
        # Verifying an existing design keeps the geometry generator's seed constant.
        action = choose(study, rows, remaining, only_geometry, seed + geometry_count)
        if not action:
            break
        kind = action["kind"]
        actual = COST[kind]
        if actual > remaining or action["estimated_seconds"] > remaining:
            raise RuntimeError("action exceeds budget")
        rid = apply(case, rows, action)
        if kind == "geometry":
            design_ids.add(rid)
            geometry_count += 1
        trace.append({"kind": kind, "action_id": action["id"], "params": action["params"], "run_id": rid,
                      "frequency_ghz": action["frequency_ghz"], "estimated_seconds": action["estimated_seconds"],
                      "analytic_cost_seconds": actual})
        remaining -= actual
    final = study.plan(rows, budget_seconds=BUDGET, count=1, seed=seed)
    counts = Counter(a["kind"] for a in trace)
    return {"case": case, "seed": seed, "policy": "geometry_only" if only_geometry else "mixed",
            "initial_history_sha256": fingerprint(initial), "profile_sha256": study.identity,
            "initial_designs": len(initial), "initial_acceptance": before,
            "budget_seconds": BUDGET, "estimated_spend_seconds": sum(a["estimated_seconds"] for a in trace),
            "analytic_spend_seconds": BUDGET - remaining, "unused_seconds": remaining,
            "actions_by_kind": {k: counts[k] for k in KINDS}, "verification_actions": len(trace) - geometry_count,
            "new_design_actions": geometry_count, **accepted(final, design_ids), "trace": trace}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", choices=["aster", "antopt"], default="aster")
    parser.add_argument("--source-tree", type=Path, help="directory containing the selected Python package")
    parser.add_argument("--seeds", type=int, nargs="+", default=[7, 23])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.source_tree:
        sys.path.insert(0, str(args.source_tree.resolve()))
    rows = []
    for case in ("lens28_ref", "lwa"):
        for seed in args.seeds:
            pair = [trial(args.package, case, seed, only) for only in (True, False)]
            for key in ("initial_history_sha256", "profile_sha256", "initial_acceptance", "budget_seconds"):
                if pair[0][key] != pair[1][key]:
                    raise RuntimeError(f"comparison has inconsistent {key}")
            rows.extend(pair)
    evidence = {
        "evidence": "SYNTHETIC ANALYTIC BENCHMARK; no AEDT, HFSS, hardware measurement or wall-clock timings",
        "package": args.package, "seeds": args.seeds, "analytic_cost_seconds_by_kind": COST,
        "method": "Same profile, initial evidence, budget and geometry-seed sequence in each pair. "
                  "Both use DesignStudy.plan one action at a time. Geometry-only masks verification actions. "
                  "All new geometries and tolerance articles return the full sample grid; partial actions reveal only the requested data.",
        "interpretation": "Lens starts with two favourable incomplete exports. LWA starts with one accepted nominal and untested ±0.1 mm scenarios. "
                          "Both LWA policies designate that nominal design as the verification anchor and declare a 2x scheduling weight for tolerance. "
                          "Analytic responses intentionally clear acceptance. Prices are fixed, accurate and omit overhead. "
                          "Nominal-design counts exclude tolerance test articles; the separate all-points count includes them. "
                          "Repeated seeds test replay mechanics and do not estimate generalization or improving HFSS geometry performance.",
        "trials": rows,
    }
    payload = json.dumps(evidence, indent=2, allow_nan=False, ensure_ascii=False)
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()
