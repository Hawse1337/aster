import unittest
from unittest.mock import Mock, patch

from aster.parametric.registry import CaseRegistration, _REGISTRY, get_case


class InstalledCaseTests(unittest.TestCase):
    def test_explicit_installed_case_is_shared_by_planner_and_worker(self):
        from aster.design_study import resolve_registration
        name = "external-probe"
        registration = CaseRegistration(name, lambda: None)
        plugin = Mock()
        plugin.load.return_value.return_value = registration
        with patch.dict(_REGISTRY, {}, clear=True), patch("importlib.metadata.entry_points", return_value=[plugin]) as lookup:
            self.assertIs(resolve_registration(name), registration)
            self.assertIs(get_case(name), registration)
            lookup.assert_called_once_with(group="aster.antenna_cases", name=name)

    def test_ambiguous_installed_entry_rejected_before_loading(self):
        entry = Mock()
        with patch("importlib.metadata.entry_points", return_value=[entry, entry]), self.assertRaises(ValueError):
            get_case("ambiguous-example")
        entry.load.assert_not_called()
