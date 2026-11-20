"""Family baseline registry tests (offline)."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from aster.core import SpecError
from aster.families import FamilyBase, FamilyRegistry, file_sha256

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")


class FamilyRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.getcwd())
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _family(self, name="op1", path=None):
        project = Path(path or (self.dir / f"{name}.aedt"))
        project.write_bytes(b"fake-aedt")
        return FamilyBase(family_id=name, parent="lens28_disc_v1",
                          operator="aperture_scale",
                          values={"aperture_scale": 1.06},
                          project_path=str(project),
                          sha256=file_sha256(project),
                          created_utc="2026-11-10T00:00:00Z")

    def test_register_and_load(self):
        registry = FamilyRegistry(self.dir / "families.json")
        family = self._family()
        registry.register(family)
        loaded = registry.load()
        self.assertEqual(loaded["op1"].operator, "aperture_scale")
        self.assertEqual(loaded["op1"].values["aperture_scale"], 1.06)

    def test_duplicate_registration_rejected(self):
        registry = FamilyRegistry(self.dir / "families.json")
        registry.register(self._family())
        with self.assertRaises(SpecError):
            registry.register(self._family())

    def test_verify_detects_project_changes(self):
        registry = FamilyRegistry(self.dir / "families.json")
        family = self._family()
        registry.register(family)
        ok, reason = registry.verify("op1")
        self.assertTrue(ok, reason)
        Path(family.project_path).write_bytes(b"modified")
        ok, reason = registry.verify("op1")
        self.assertFalse(ok)
        self.assertIn("changed", reason)

    def test_verify_missing_family_and_file(self):
        registry = FamilyRegistry(self.dir / "families.json")
        ok, reason = registry.verify("nope")
        self.assertFalse(ok)
        self.assertIn("unknown", reason)
        family = self._family()
        registry.register(family)
        Path(family.project_path).unlink()
        ok, reason = registry.verify("op1")
        self.assertFalse(ok)
        self.assertIn("missing", reason)

    def test_roundtrip_dict(self):
        family = self._family()
        clone = FamilyBase.from_dict(family.to_dict())
        self.assertEqual(clone.family_id, family.family_id)
        self.assertEqual(clone.sha256, family.sha256)


if __name__ == "__main__":
    unittest.main()
