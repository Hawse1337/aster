"""The generated operator catalog must cover every renderer operation."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")

from antopt.parametric.aedt import KNOWN_OPERATIONS

CATALOG = HERE / "docs" / "operator_catalog.md"


class OperatorCatalogTests(unittest.TestCase):
    def test_catalog_exists_and_is_current(self):
        self.assertTrue(CATALOG.is_file(), "run scripts/gen_operator_catalog.py")
        text = CATALOG.read_text(encoding="utf-8")
        for name in sorted(KNOWN_OPERATIONS):
            self.assertIn("`%s`" % name, text, "missing operation in catalog: %s" % name)

    def test_catalog_states_the_plugin_scope(self):
        text = CATALOG.read_text(encoding="utf-8")
        # the honest statement about what adding a new antenna actually costs
        self.assertIn("两个通用算子", text)
        self.assertIn("cases/", text)


if __name__ == "__main__":
    unittest.main()
