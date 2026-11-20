"""The configuration layer: discovery, file overrides, environment overrides."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from antopt import config


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    return path


class DiscoverTests(unittest.TestCase):
    def test_missing_installations_are_reported_not_raised(self):
        # a machine with nothing installed must still produce an Environment
        env = config.Environment()
        problems = config.validate(env)
        self.assertTrue(any("AEDT" in problem for problem in problems))
        self.assertTrue(any("guard" in problem for problem in problems))

    def test_aedt_version_sorting_prefers_the_newer_release(self):
        older = Path(r"D:\ANSYS Inc\v251\AnsysEM\ansysedt.exe")
        newer = Path(r"D:\ANSYS Inc\v261\AnsysEM\ansysedt.exe")
        self.assertGreater(config._version_key(newer), config._version_key(older))

    def test_discovery_finds_a_guard_checkout_with_a_venv(self):
        with tempfile.TemporaryDirectory() as tmp:
            guard = Path(tmp) / "ansys-aedt-mcp"
            touch(guard / ".venv" / "Scripts" / "python.exe")
            original = config.GUARD_CANDIDATES
            config.GUARD_CANDIDATES = (str(guard),)
            try:
                env = config.discover()
            finally:
                config.GUARD_CANDIDATES = original
            self.assertEqual(env.guard_repo, guard)
            self.assertEqual(env.venv_python, guard / ".venv" / "Scripts" / "python.exe")
            self.assertEqual(env.source["guard_repo"], "discovered")


class ConfigFileTests(unittest.TestCase):
    def _env_with_paths(self, tmp: Path) -> config.Environment:
        return config.Environment(
            aedt_exe=touch(tmp / "ansysedt.exe"),
            guard_repo=tmp / "guard",
            venv_python=touch(tmp / "guard" / ".venv" / "Scripts" / "python.exe"),
            reference_project=touch(tmp / "LensFinal.aedt"),
            campaign_root=tmp / "campaigns",
            yadof_workspace=tmp / "yadof",
            aedt_version="v261",
        )

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            written = config.write(root / "config.toml", self._env_with_paths(root))
            self.assertTrue(written.is_file())
            loaded = config.load(written)
            env = self._env_with_paths(root)
            for name in ("aedt_exe", "venv_python", "reference_project", "campaign_root",
                         "yadof_workspace"):
                self.assertEqual(Path(getattr(loaded, name)), Path(getattr(env, name)))
            self.assertEqual(loaded.aedt_version, "v261")

    def test_env_variables_override_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            written = config.write(root / "config.toml", self._env_with_paths(root))
            other = touch(root / "elsewhere.aedt")
            os.environ["ANTOPT_REFERENCE_PROJECT"] = str(other)
            try:
                loaded = config.load(written)
            finally:
                os.environ.pop("ANTOPT_REFERENCE_PROJECT", None)
            self.assertEqual(loaded.reference_project, other)
            self.assertEqual(loaded.source["reference_project"], "env")

    def test_missing_file_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = config.load(Path(tmp) / "nothing.toml")
            self.assertIsInstance(env, config.Environment)

    def test_validate_accepts_a_complete_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self._env_with_paths(root)
            (root / "campaigns").mkdir()
            self.assertEqual(config.validate(env), [])

    def test_validate_mentions_the_guard_venv_specifically(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self._env_with_paths(root)
            env.venv_python = root / "gone" / "python.exe"
            problems = config.validate(env)
            self.assertTrue(any("venv_python" in problem for problem in problems))

    def test_describe_lists_missing_pieces(self):
        text = config.describe(config.Environment())
        self.assertIn("missing", text)
        self.assertIn("AEDT", text)

    def test_default_config_path_is_under_appdata(self):
        path = config.default_config_path()
        self.assertTrue(str(path).endswith("config.toml"))


if __name__ == "__main__":
    unittest.main()
