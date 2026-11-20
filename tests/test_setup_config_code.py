"""The setup/sweep instructions live in one builder so every path runs the same code."""
from __future__ import annotations

import unittest

from antopt.executor import setup_config_code


class SetupConfigCodeTests(unittest.TestCase):
    def test_settings_come_before_the_sweep(self):
        code = setup_config_code(project_path=r"D:\\p.aedt", design_name="f24g05",
                                 setup_name="P4", passes=8)
        self.assertLess(code.index("MaximumPasses"), code.index("create_linear_step_sweep"))
        self.assertIn("MaxDeltaS", code)
        self.assertIn("setup.update()", code)

    def test_band_and_sweep_name_are_parameterised(self):
        code = setup_config_code(project_path="p", design_name="d", setup_name="P4",
                                 passes=12, band_ghz=(25.0, 29.0), sweep_name="BAND",
                                 step_ghz=0.5, max_delta_s=0.01)
        self.assertIn("25.0", code)
        self.assertIn("29.0", code)
        self.assertIn("0.5", code)
        self.assertIn("BAND", code)
        self.assertIn("int(12)", code)
        self.assertIn("0.01", code)

    def test_the_old_sweep_is_deleted_first(self):
        code = setup_config_code(project_path="p", design_name="d", setup_name="P4",
                                 passes=8)
        self.assertLess(code.index("delete_sweep"), code.index("create_linear_step_sweep"))

    def test_code_compiles(self):
        code = setup_config_code(project_path="p", design_name="d", setup_name="P4",
                                 passes=8)
        compile(code, "setup_config.py", "exec")


if __name__ == "__main__":
    unittest.main()
