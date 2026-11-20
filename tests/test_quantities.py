"""Category-by-category enumeration: the old probe stopped at the first non-empty list."""
from __future__ import annotations

import unittest

from antopt.quantities import ProbeRecord, candidate_categories, enumerate_quantities, summarise


class FakeProbe:
    """Answers like PostProcessor but only for the categories it is told about."""

    def __init__(self, answers: dict[tuple[str, str], object]) -> None:
        self.answers = answers
        self.calls: list[dict] = []

    def available_report_quantities(self, **kwargs):
        self.calls.append(dict(kwargs))
        key = (kwargs.get("report_category"), kwargs.get("quantities_category"))
        answer = self.answers.get(key, [])
        if isinstance(answer, Exception):
            raise answer
        return answer


class EnumerationTests(unittest.TestCase):
    def test_every_category_pair_is_asked(self):
        probe = FakeProbe({})
        records = enumerate_quantities(probe)
        pairs = {(record.category, record.quantity_category) for record in records}
        self.assertEqual(len(pairs), len(list(candidate_categories())))
        self.assertIn(("Antenna Parameters", ""), pairs)
        self.assertIn(("Far Fields", "Directivity"), pairs)

    def test_an_answer_later_in_the_list_is_not_lost(self):
        probe = FakeProbe({("Far Fields", "Directivity"): ["dB(DirectivityTotal)"],
                           ("Antenna Parameters", ""): ["RadiationEfficiency"]})
        summary = summarise(enumerate_quantities(probe))
        self.assertTrue(summary["supports_efficiency"])
        self.assertIn("dB(DirectivityTotal)", summary["directivity_or_efficiency_quantities"])
        self.assertIn("RadiationEfficiency", summary["directivity_or_efficiency_quantities"])

    def test_a_rejection_is_recorded_per_category_not_raised(self):
        probe = FakeProbe({("Far Fields", "Directivity"): RuntimeError("CreateReport failed")})
        records = enumerate_quantities(probe)
        failed = [record for record in records if not record.ok]
        self.assertTrue(failed)
        self.assertIn("CreateReport failed", failed[0].error)

    def test_every_solution_is_probed(self):
        probe = FakeProbe({("S-parameters", ""): ["S(1,1)"]})
        records = enumerate_quantities(probe, solutions=["Setup : Sweep", "Setup2 : Sweep2"])
        solutions = {record.solution for record in records}
        self.assertEqual(solutions, {"Setup : Sweep", "Setup2 : Sweep2"})

    def test_a_context_without_quantities_is_evidence_too(self):
        summary = summarise(enumerate_quantities(FakeProbe({})))
        self.assertFalse(summary["supports_efficiency"])
        self.assertEqual(summary["directivity_or_efficiency_quantities"], [])
        self.assertIn("only", summary["verdict"].lower())


class SummaryTests(unittest.TestCase):
    def test_categories_are_grouped(self):
        records = [ProbeRecord("Far Fields", "", "", True, ["dB(GainTotal)"]),
                   ProbeRecord("Antenna Parameters", "", "", True, ["RadiationEfficiency"]),
                   ProbeRecord("Fields", "", "", False, [], "boom")]
        summary = summarise(records, versions={"pyaedt": "1.9.0"})
        self.assertEqual(summary["categories"]["Far Fields"]["answered"], 1)
        self.assertEqual(summary["categories"]["Fields"]["failed"], 1)
        self.assertEqual(summary["versions"]["pyaedt"], "1.9.0")
        self.assertTrue(summary["supports_efficiency"])


if __name__ == "__main__":
    unittest.main()
