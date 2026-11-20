"""Generality tests: the second antenna plugin plus an end-to-end system loop."""
from __future__ import annotations

import os
import math
import unittest

from antopt.cases import lens28_builder
from antopt.cases import lwa
from antopt.controller import ControllerContext, StrategyController
from antopt.core import CaseSpec
from antopt.executor import SyntheticExecutor
from antopt.optimize import ParetoArchive
from antopt.parametric.registry import (
    CaseRegistration,
    clear_registry,
    get_case,
    list_cases,
    register_case,
)
from antopt.synthetic import SyntheticProblem

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")


class LwaKnowledgeTests(unittest.TestCase):
    def test_spatial_harmonic_formula(self):
        beta = lwa.beta_n(0.0, 10.0, -1)
        self.assertAlmostEqual(beta, -2.0 * math.pi / 10.0)
        beta0 = lwa.beta_n(0.3, 8.0, -1)
        self.assertAlmostEqual(beta0, 0.3 - 2.0 * math.pi / 8.0)

    def test_beam_angle_is_physical(self):
        ok, beta, angle = lwa.radiating_harmonic(22.86, 10.0, 28.0)
        self.assertTrue(ok)
        self.assertTrue(math.isfinite(angle))
        self.assertLess(angle, 0.0)  # -1 harmonic radiates backwards
        self.assertFalse(math.isfinite(lwa.beam_angle_deg(-10.0, 28.0)))
        # a very short period pushes the harmonic outside the light cone at 24 GHz
        ok_low, _, _ = lwa.radiating_harmonic(22.86, 6.0, 24.0)
        self.assertFalse(ok_low)

    def test_open_stopband_heuristic(self):
        gain = {26.0: 10.0, 27.0: 10.0, 28.0: 5.0, 29.0: 10.0, 30.0: 10.0}
        reflection = {26.0: -15.0, 27.0: -15.0, 28.0: -6.0, 29.0: -15.0, 30.0: -15.0}
        result = lwa.open_stopband_suspected(gain, reflection)
        self.assertTrue(result["suspected"])
        self.assertEqual(result["points"][0]["frequency_ghz"], 28.0)
        clean = lwa.open_stopband_suspected(
            {26.0: 10.0, 27.0: 10.0, 28.0: 9.8, 29.0: 10.0, 30.0: 10.0},
            {26.0: -15.0, 27.0: -15.0, 28.0: -15.0, 29.0: -15.0, 30.0: -15.0},
        )
        self.assertFalse(clean["suspected"])


class LwaCaseTests(unittest.TestCase):
    def setUp(self):
        clear_registry()

    def tearDown(self):
        clear_registry()

    def test_case_and_model(self):
        case = lwa.build_case()
        self.assertEqual(case.family.antenna_type, "leaky_wave")
        spec = case.to_spec()
        self.assertIn("s11", [m["kind"] for m in spec["metrics"]])
        model = lwa.build_model()
        ops = model.build(model.space.default())
        names = [op["op"] for op in ops]
        self.assertIn("create_tapered_units", names)
        self.assertTrue(model.validate_geometry().valid)
        self.assertTrue(model.validate_em_setup().valid)

    def test_period_must_be_able_to_radiate(self):
        model = lwa.build_model()
        params = dict(model.space.default(), p_mm=6.0)
        model.current = params
        report = model.validate_em_setup()
        self.assertFalse(report.valid)

    def test_both_plugins_register_side_by_side(self):
        register_case(lens28_builder.register())
        register_case(lwa.register())
        self.assertEqual(list_cases(), ["lens28", "lwa"])
        for name in ("lens28", "lwa"):
            registration = get_case(name)
            self.assertIsInstance(registration.build_case(), CaseSpec)
            model = registration.build_model()
            self.assertTrue(model.space.default())

    def test_core_does_not_depend_on_case_plugins(self):
        import ast

        import antopt.controller as controller
        import antopt.core as core
        import antopt.optimize as optimize

        for module in (core, optimize, controller):
            tree = ast.parse(open(module.__file__, encoding="utf-8-sig").read())
            imported: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imported.append(node.module or "")
            for name in imported:
                self.assertFalse(
                    name.startswith("antopt.cases"),
                    f"{module.__name__} imports a case plugin: {name}",
                )
                self.assertNotIn(name, {"mcp_server", "aedt_guard", "ansys"},
                                 f"{module.__name__} depends on the guard/solver: {name}")


class SystemLoopTests(unittest.TestCase):
    """Compose the pieces: propose -> evaluate -> archive -> decide."""

    def test_loop_with_controller_and_archive(self):
        from antopt.synthetic import build_case

        case = build_case()
        executor = SyntheticExecutor(case, SyntheticProblem())
        archive = ParetoArchive(case.objectives)
        controller = StrategyController()
        state = {"budget": 24, "stalled": 0, "hv": []}
        rounds = 0
        while state["budget"] > 0 and rounds < 6:
            from antopt.core import latin_hypercube

            params = latin_hypercube(case.family.param_space, 4, seed=rounds)
            for candidate_params in params:
                candidate = case.make_candidate(candidate_params, algorithm="lhs", round=rounds)
                archive.add(executor.evaluate(candidate))
                state["budget"] -= 1
            front = archive.feasible_front()
            from antopt.optimize import hypervolume_2d

            volume = hypervolume_2d(archive.feasible(), case.objectives[0],
                                   case.objectives[1], (0.0, -30.0))
            state["hv"].append(volume)
            context = ControllerContext(
                failing_constraints=[] if archive.feasible() else ["gain_h"],
                stalled_rounds=state["stalled"],
                hypervolume_history=state["hv"],
                has_sensitivity=rounds > 0,
                sensitivity_fixable=True,
                budget_left=state["budget"],
            )
            decision = controller.select(context)
            self.assertIn(decision.strategy,
                          {"LOCAL_OPTIMIZATION", "GLOBAL_SEARCH",
                           "SURROGATE_OPTIMIZATION", "PHYSICS_DIAGNOSIS",
                           "TOPOLOGY_SEARCH", "STOP"})
            rounds += 1
        self.assertGreater(rounds, 0)
        self.assertGreater(len(archive.entries), 0)
        self.assertTrue(archive.feasible())
        self.assertEqual(len(controller.log), rounds)


if __name__ == "__main__":
    unittest.main()



class LwaGeometryTests(unittest.TestCase):
    """S7: the second antenna must be buildable, not just specified."""

    def test_case_renders_a_full_geometry_snippet(self):
        from antopt.cases import lwa
        from antopt.parametric.aedt import render

        model = lwa.build_model()
        ops = model.build(model.space.default())
        code = render(ops, project_path=r"D:\sandbox\lwa.aedt", design_name="LWA")
        self.assertIn("app.modeler.create_box(origin=[0.0, 0.0, 0.0], sizes=[22.86, 10.16, 200.0]", code)
        self.assertIn("_antopt_units = []", code)
        self.assertIn("lwa_unit_", code)
        # the LWA now uses the additive law h_n = h0 + a1*u + a2*u^2
        self.assertIn("_h = 2.0 + 0.0 * _t + 0.0 * _t * _t", code)

    def test_taper_changes_the_unit_heights(self):
        from antopt.cases import lwa
        from antopt.parametric.aedt import render

        params = dict(lwa.build_model().space.default(), a1=0.5, a2=0.0)
        ops = lwa.build_model().build(params)
        code = render(ops, project_path=r"D:\sandbox\lwa.aedt", design_name="LWA")
        self.assertIn("0.5 * _t", code)

    def test_invalid_unit_arguments_are_rejected(self):
        from antopt.parametric.aedt import OperationError, validate_operations

        with self.assertRaises(OperationError):
            validate_operations([{"op": "create_tapered_units",
                                  "args": {"count": 0, "pitch": 10.0, "h0": 2.0,
                                           "width": 22.0, "depth": 0.5}}])
        with self.assertRaises(OperationError):
            validate_operations([{"op": "create_tapered_units",
                                  "args": {"count": 4, "pitch": 10.0, "h0": 2.0,
                                           "width": 0.0, "depth": 0.5}}])
        with self.assertRaises(OperationError):
            validate_operations([{"op": "create_box",
                                  "args": {"name": "b", "origin": [0, 0, 0],
                                           "sizes": [1, 0, 1]}}])
