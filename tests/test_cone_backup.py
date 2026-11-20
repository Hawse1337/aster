"""Cone backup/restore: the ring union may destroy cone_dn, so the model keeps a copy.

The first build unites the ring into ``coax_outer`` and ``cone_dn``, which absorbs the
cone. ``restore_object`` puts it back before ``gap_bowls`` runs and ``backup_object``
keeps the spare copy in place, so a design can be re-parameterised repeatedly.
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")

from antopt.cases import lens28_ref
from antopt.parametric.aedt import OperationError, render, validate_operations


class BackupRestoreRenderingTests(unittest.TestCase):
    def test_operations_are_available_but_not_emitted(self):
        # the backup/restore operators exist for a future in-place update path, but the
        # model does not emit them: doing so changed the geometry and broke the sweep
        # export (E_SWEEP_EXPORT_INCOMPLETE) on the next real run
        model = lens28_ref.build_model()
        params = model.space.default()
        params["ring_outer"] = 2.2
        names = [op["op"] for op in model.build(params)]
        self.assertNotIn("restore_object", names)
        self.assertNotIn("backup_object", names)

    def test_generated_code_copies_and_renames(self):
        code = render([
            {"op": "restore_object", "args": {"name": "cone_dn"}},
            {"op": "backup_object", "args": {"name": "cone_dn"}},
        ], project_path=r"X:\p.aedt", design_name="d", port=1)
        self.assertEqual(code.count("app.modeler.copy(assignment="), 2)
        self.assertIn("app.modeler[_copied[0]].name = 'cone_dn'", code)
        self.assertIn("app.modeler[_copied[0]].name = _bak_name", code)
        # the copy API is optional: a failure must not break the geometry pipeline
        self.assertEqual(code.count("except Exception:"), 2)

    def test_names_are_required(self):
        with self.assertRaises(OperationError):
            validate_operations([{"op": "backup_object", "args": {}}])
        with self.assertRaises(OperationError):
            validate_operations([{"op": "restore_object", "args": {"name": 3}}])


if __name__ == "__main__":
    unittest.main()
