"""Study integration regressions with generated fixtures on registered antenna cases."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from aster.cases import lens28_ref, lwa
from aster.design_study import DesignStudy, cli, run_cli


def profile(name):
    return {
        "case_id": name, "profile_id": "integration_fixture", "revision": 1, "recipe_id": "recipe-v1",
        "band": {"band_ghz": [26, 30], "max_gap_ghz": 1, "threshold_db": -10},
        "coverage": {"frequencies_ghz": [26, 28, 30], "theta_deg": [90, 90], "phi_deg": [0, 20],
                     "min_realized_gain_dbi": 4, "max_step_deg": 10},
        "objectives": ["min_realized_gain_dbi", "band_margin_db"],
        "cost_seconds": {"geometry": 100, "coverage": 10, "frequency": 30, "interaction": 100, "tolerance": 100},
    }


def setup_study(name="lens28_ref", **overrides):
    reg = {"lens28_ref": lens28_ref, "lwa": lwa}[name].register()
    return DesignStudy(reg.build_case(), reg.build_model(), {**profile(name), **overrides})


def row(study, rid="r1", *, params=None, horizon=7., other=8.):
    case = study.case.case_id
    values = ({"bandwidth": 3.5, "peak_gain": 7., "ripple": 1.} if case=="lens28_ref"
              else {"s11": -12., "gain_h": 9., "ripple": 1.})
    snapshots = [{"frequency_ghz": f, "run_id": rid, "source": "MEASURED", "coordinate_system": "Global",
                  "quantity": "dB(RealizedGainTotal)", "sphere": {"theta_deg": [40,90], "phi_deg":[0,10,20],
                  "gain_db": [[other]*3, [horizon]*3]}} for f in (26,28,30)]
    return {"run_id": rid, "case": case, "recipe_id":"recipe-v1", "source":"MEASURED",
            "fidelity":"high", "status":"COMPLETED", "params": params or study.space.default(),
            "metrics": {key:{"value": v, "passed":True} for key,v in values.items()},
            "s11_points": [[f,-15.] for f in range(26,31)], "coverage_snapshots": snapshots}


class DesignStudyTests(unittest.TestCase):
    def test_builder_revision_selects_evidence_and_pending_actions(self):
        first, second = "b" * 64, "c" * 64
        study = setup_study()
        rows = [{**row(study, rid), **extra} for rid, extra in (
            ("original", {"builder_sha256": first}), ("updated", {"builder_sha256": second}), ("legacy", {}))]
        with self.assertRaisesRegex(ValueError, "different builder revisions"):
            study.plan(rows, budget_seconds=100)
        selected = setup_study(builder_sha256=first)
        result = selected.plan(rows, budget_seconds=100, count=1)
        self.assertEqual(result["nominal_front"], ["original"])
        self.assertEqual(result["builder_sha256"], first)
        self.assertEqual(len(result["excluded"]), 2)
        action = result["actions"][0]["id"]
        pending = selected.plan(rows, budget_seconds=100, count=1, pending_ids=iter([action]))
        self.assertNotIn(action, [a["id"] for a in pending["actions"]])
        with self.assertRaisesRegex(ValueError, "builder_sha256"):
            study.plan([{**rows[0], "builder_sha256": int("1" * 64)}], budget_seconds=100)

    def test_two_registered_cases_and_raw_guard_verdicts(self):
        for name, metric_id, expected in (("lens28_ref","bandwidth","s11_bandwidth"),("lwa","gain_h","realized_gain_h")):
            with self.subTest(name=name):
                study = setup_study(name)
                good, bad = row(study,"good"), row(study,"failed-guard")
                bad["metrics"][metric_id]["passed"] = False
                result = study.plan([good,bad],budget_seconds=100,seed=2,count=1)
                self.assertEqual(result["nominal_front"], ["good"])
                good_report, bad_report = result["assessments"]
                self.assertEqual(good_report["metrics"][expected], good["metrics"][metric_id]["value"])
                self.assertTrue(good_report["sampled_pass"])
                self.assertFalse(bad_report["base_pass"])
                self.assertTrue(bad_report["complete"])
                self.assertFalse(bad_report["sampled_pass"])

    def test_partial_and_failed_checks_cannot_enter_either_front(self):
        study = setup_study()
        missing_band = row(study,"partial-s11")
        missing_band["s11_points"] = [[26,-15],[30,-15]]
        missing_far = row(study,"partial-ff")
        missing_far["coverage_snapshots"].pop()
        missing_case = row(study,"partial-case")
        del missing_case["metrics"]["ripple"]
        missing_objective = row(study, "partial-objective")
        del missing_objective["metrics"]["peak_gain"]
        wrong_identity = row(study,"wrong-quantity")
        wrong_identity["coverage_snapshots"][1]["quantity"] = "dB(GainTotal)"
        poor_match = row(study,"poor-match")
        poor_match["s11_points"][1][1] = -8.
        poor_gain = row(study,"poor-gain",horizon=1.)
        poor_gain["metrics"]["coverage_margin_db"] = {"value": 99., "passed": True}
        result = study.plan([missing_band, missing_far, missing_case, missing_objective,
                             wrong_identity, poor_match, poor_gain],
                            budget_seconds=10, count=1)
        self.assertEqual(result["nominal_front"], [])
        self.assertEqual(result["tested_tolerance_front"], [])
        reports = {r["run_id"]:r for r in result["assessments"]}
        for rid in ("partial-s11", "partial-ff", "partial-case", "partial-objective", "wrong-quantity"):
            self.assertFalse(reports[rid]["complete"], rid)
        for rid in ("poor-match","poor-gain"):
            self.assertTrue(reports[rid]["complete"], rid)
            self.assertFalse(reports[rid]["sampled_pass"], rid)
        self.assertLess(reports["poor-gain"]["metrics"]["coverage_margin_db"],0)
        self.assertEqual(result["feasibility_validation"]["input_rows"], 2)

    def test_filters_conflicting_incompatible_or_unverified_runs(self):
        study = setup_study()
        changes = [{"case_name":"lwa"}, {"learning_contract":{"identity":"other"}}, {"source":"PREDICTED"},
                   {"source":""}, {"is_prediction":True}, {"fidelity":"coarse"}, {"invalid":True},
                   {"dry_run": True}, {"mode": "dry"}]
        rows = [{**row(study,f"exclude-{i}"),**change} for i,change in enumerate(changes)]
        rows.append(row(study, "dry-case-history"))
        result = study.plan([row(study),*rows],budget_seconds=100,count=1)
        self.assertEqual([r["run_id"] for r in result["assessments"]],["r1"])
        self.assertEqual({r["run_id"] for r in result["excluded"]},{r["run_id"] for r in rows})
        with self.assertRaisesRegex(ValueError,"unique run_id"):
            study.plan([row(study),row(study)],budget_seconds=100)
        bad = row(study)
        bad["coverage_snapshots"][1]["run_id"] = "another-run"
        with self.assertRaisesRegex(ValueError,"different run"):
            study.plan([bad],budget_seconds=100)

    def test_budget_competition_pending_and_fixed_seed(self):
        study = setup_study()
        sample = row(study)
        sample["s11_points"] = [[26,-15],[30,-15]]
        sample["coverage_snapshots"] = sample["coverage_snapshots"][:1]
        args = dict(budget_seconds=40,count=2,seed=7)
        result = study.plan([sample],**args)
        self.assertEqual(result["actions"],study.plan([sample],**args)["actions"])
        self.assertEqual([(a["kind"],a["frequency_ghz"]) for a in result["actions"]], [("coverage",28),("coverage",30)])
        pending = [a["id"] for a in result["actions"]]
        alternate = study.plan([sample],pending_ids=pending,**args)
        self.assertEqual([a["kind"] for a in alternate["actions"]],["frequency"])
        self.assertGreater(alternate["estimated_seconds"],0)
        self.assertLessEqual(alternate["estimated_seconds"],40)
        self.assertFalse(set(pending) & {a["id"] for a in alternate["actions"]})
        self.assertEqual(study.plan([sample],budget_seconds=9,count=2)["actions"],[])

    def test_profile_can_reverse_front_on_identical_source_data(self):
        a = setup_study(objectives=["min_realized_gain_dbi"])
        b = setup_study(coverage={**a.profile["coverage"],"theta_deg":[40,40]},
                        objectives=["min_realized_gain_dbi"], revision=2)
        rows = [row(a,"horizon",horizon=9,other=6),
                row(a,"sector",horizon=6,other=9,params={**a.space.default(),"ring_outer":2.8})]
        first = a.plan(rows,budget_seconds=100,count=1)
        second = b.plan(rows,budget_seconds=100,count=1)
        self.assertEqual(first["nominal_front"],["horizon"])
        self.assertEqual(second["nominal_front"],["sector"])
        self.assertNotEqual(first["profile_sha256"],second["profile_sha256"])

    def test_repeat_needs_an_independent_run_before_verification(self):
        tol = {"tolerances":{"ring_outer":.1}, "scenarios":{"outer-plus":{"ring_outer":.1}},
               "required_repeats":2}
        study = setup_study(tolerance=tol)
        base = study.space.default()
        upper = {**base,"ring_outer":3.1}
        history = [row(study,"nominal"),row(study,"cached"),row(study,"upper-1",params=upper),
                   row(study,"upper-2",params=upper)]
        history[1]["cache_hit"] = True
        first = study.plan(history,budget_seconds=1000,count=16,anchor_run_id="nominal")
        check = next(r for r in first["tolerance_studies"] if r["run_id"]=="nominal")
        self.assertFalse(check["complete"])
        self.assertNotIn("nominal",first["tested_tolerance_front"])
        repeats = [a for a in first["actions"] if a["kind"]=="tolerance" and a["params"]==base]
        self.assertEqual(len(repeats),1)
        second = study.plan([*history,row(study,"independent")],budget_seconds=1000,count=16,anchor_run_id="nominal")
        self.assertIn("nominal",second["tested_tolerance_front"])
        self.assertTrue(next(r for r in second["tolerance_studies"] if r["run_id"]=="nominal")["complete"])

    def test_geometry_acquisition_receives_only_supported_complete_probability(self):
        study = setup_study()
        def params(i):
            return {**study.space.default(),"ring_outer":2.0+i/10}
        history = [row(study, f"g-{i}", params=params(i), horizon=6 if i>=10 else 2) for i in range(21)]
        partial = row(study,"unknown",params=params(22))
        partial["coverage_snapshots"] = []
        offered = [params(4.5),params(15.5),{**params(15.5),"ring_shift":.2}]
        with patch("aster.design_study.recommend_from_runs",return_value={"method":"controlled_queries",
                   "trained_on":21, "recommendations":[{"params":p} for p in offered]}):
            result=study.plan([*history,partial],budget_seconds=10000,count=32)
        stats=result["feasibility_validation"]
        self.assertEqual(stats["input_rows"],21)
        self.assertTrue(stats["enabled"],stats)
        geo={ (a["params"]["ring_outer"],a["params"]["ring_shift"]):a for a in result["actions"] if a["kind"]=="geometry" }
        near_fail, near_pass = geo[(2.45,0.)], geo[(3.55,0.)]
        self.assertLess(near_fail["feasibility_probability"],.5)
        self.assertGreater(near_pass["feasibility_probability"],.5)
        self.assertLess(near_fail["priority"],near_pass["priority"])
        self.assertIsNone(geo[(3.55,.2)]["feasibility_probability"])

    def test_overlapping_interaction_and_tolerance_share_one_geometry(self):
        steps = {"ring_outer": 0.2, "ring_shift": 0.1}
        costs = {"geometry": 1000, "frequency": 1000, "coverage": 1000,
                 "interaction": 100, "tolerance": 110}
        study = setup_study(interaction_steps=steps, tolerance={"tolerances": steps, "corners": True},
                            cost_seconds=costs)
        nominal = row(study, "anchor")
        settings = dict(budget_seconds=800, count=16, anchor_run_id="anchor")
        first = study.plan([nominal], **settings)
        self.assertEqual(first["interaction"]["measured_corners"], 0)
        self.assertEqual(first["interaction"]["contrasts"], {})
        actions = first["actions"]
        expected = {(2.8, -0.1), (2.8, 0.1), (3.2, -0.1), (3.2, 0.1)}
        self.assertEqual(len(actions), 4)
        self.assertEqual({(a["params"]["ring_outer"], a["params"]["ring_shift"]) for a in actions}, expected)
        self.assertEqual(len({a["id"] for a in actions}), 4)

        history = [nominal]
        for index, (radius, shift) in enumerate(sorted(expected)):
            same_sign = (radius > 3.0) == (shift > 0.0)
            p = {**nominal["params"], "ring_outer": radius, "ring_shift": shift}
            history.append(row(study, f"corner-{index}", params=p, horizon=8.25 if same_sign else 7.75))
        complete = study.plan(history, **settings)["interaction"]
        self.assertEqual(complete["parameters"], ["ring_outer", "ring_shift"])
        self.assertEqual(complete["measured_corners"], 4)
        self.assertAlmostEqual(complete["contrasts"]["min_realized_gain_dbi"], 1.0)

    def test_cli_binds_touchstone_and_external_coverage_to_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            study = setup_study()
            measured = row(study)
            report_path=folder/"report.json"
            spec_path=folder/"profile.json"
            spec_path.write_text(json.dumps(study.profile))
            ts=folder/"curve.s1p"
            ts.write_text("# GHz S DB R 50\n"+"".join(f"{f} -15 0\n" for f in range(26,31)))
            ff=folder/"farfield.json"
            snapshots=measured.pop("coverage_snapshots")
            del measured["s11_points"]
            ff.write_text(json.dumps({"run_id":"r1", "quantity":"dB(RealizedGainTotal)", "source":"MEASURED",
                "coordinate_system":"Global","samples":[[s["frequency_ghz"],90,p,7] for s in snapshots for p in [0,10,20]]}))
            measured["touchstone"]={"path":ts.name,"run_id":"r1","sha256":hashlib.sha256(ts.read_bytes()).hexdigest()}
            measured["coverage_files"]=[ff.name]
            history=folder/"history.json"
            history.write_text(json.dumps({"runs":[measured]}))
            parser=argparse.ArgumentParser()
            cli(parser)
            args=parser.parse_args(["--profile",str(spec_path),"--history",str(history),"--budget-seconds","100",
                                    "--count","1","--output",str(report_path)])
            self.assertEqual(run_cli(args),0)
            self.assertEqual(json.loads(report_path.read_text())["nominal_front"],["r1"])
            ts.write_text(ts.read_text()+"! export changed\n")
            args.output = folder / "after-edit.json"
            with self.assertRaisesRegex(ValueError,"digest"):
                run_cli(args)
