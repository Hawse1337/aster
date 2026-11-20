"""Enumerate what a solved project can actually report, per category, without solving anything.

Astra's point: the earlier probe stored only three flat lists, because PyAEDT's
``available_report_quantities()`` returns the first non-empty category when no category is given.
That cannot prove "this design has no Directivity or power quantity". This module is the pure part of
the corrected probe: it walks every report category and, for each, every quantity category and
solution, recording what answered and what raised - in an isolated process, because one failed report
call can poison the PyAEDT session.

Nothing here resolves a field or starts a solver: it reads the saved project as it is.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

__all__ = ["ProbeRecord", "candidate_categories", "enumerate_quantities", "summarise"]

REPORT_CATEGORIES = (
    "Far Fields",
    "Fields",
    "Antenna Parameters",
    "S-parameters",
    "Modal Solution Data",
    "Terminal Solution Data",
)
"""Report categories worth asking about, including the ones the old probe never reached."""

QUANTITY_CATEGORIES = ("", "Directivity", "Gain", "Power", "Efficiency", "Realized Gain")


@dataclass
class ProbeRecord:
    category: str
    quantity_category: str
    solution: str
    ok: bool
    quantities: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"category": self.category, "quantity_category": self.quantity_category,
                "solution": self.solution, "ok": self.ok,
                "quantities": list(self.quantities), "error": self.error}


def candidate_categories() -> Iterable[tuple[str, str]]:
    """Every (report category, quantity category) pair the probe should ask about, once each."""
    seen: set[tuple[str, str]] = set()
    for category in REPORT_CATEGORIES:
        for quantity in QUANTITY_CATEGORIES:
            pair = (category, quantity)
            if pair not in seen:
                seen.add(pair)
                yield pair


def enumerate_quantities(probe: Any,
                         solutions: Iterable[str] = (),
                         categories: Iterable[tuple[str, str]] | None = None,
                         ) -> list[ProbeRecord]:
    """Ask the probe for every category pair; record answers and failures separately.

    ``probe`` needs one method - ``available_report_quantities(report_category=...,
    quantities_category=..., solution=...)`` - which is how the real PostProcessor behaves and what
    the fake in the tests provides.
    """
    pairs = list(categories if categories is not None else candidate_categories())
    solution_list = [solution for solution in solutions] or [""]
    records: list[ProbeRecord] = []
    for category, quantity_category in pairs:
        for solution in solution_list:
            kwargs: dict[str, Any] = {"report_category": category,
                                      "quantities_category": quantity_category}
            if solution:
                kwargs["solution"] = solution
            try:
                quantities = probe.available_report_quantities(**kwargs)
                cleaned = [str(item) for item in (quantities or [])]
                records.append(ProbeRecord(category=category,
                                           quantity_category=quantity_category,
                                           solution=solution, ok=True, quantities=cleaned))
            except Exception as exc:  # noqa: BLE001 - a rejection is the evidence
                records.append(ProbeRecord(category=category,
                                           quantity_category=quantity_category,
                                           solution=solution, ok=False,
                                           error="%s: %s" % (type(exc).__name__,
                                                             str(exc)[:200])))
    return records


def summarise(records: Iterable[ProbeRecord], versions: dict[str, Any] | None = None) -> dict[str, Any]:
    """Group the records per category and state the verdict the evidence actually supports."""
    records = list(records)
    per_category: dict[str, dict[str, Any]] = {}
    for record in records:
        bucket = per_category.setdefault(record.category, {"answered": 0, "failed": 0,
                                                           "quantities": [], "errors": []})
        if record.ok and record.quantities:
            bucket["answered"] += 1
            for quantity in record.quantities:
                if quantity not in bucket["quantities"]:
                    bucket["quantities"].append(quantity)
        elif record.ok:
            bucket["answered"] += 0
        else:
            bucket["failed"] += 1
            if len(bucket["errors"]) < 5:
                bucket["errors"].append(record.error)
    interesting = [name for name, value in per_category.items()
                   if value["quantities"]]
    directivity = sorted({quantity for value in per_category.values()
                          for quantity in value["quantities"]
                          if "direct" in quantity.lower() or "efficien" in quantity.lower()})
    return {
        "versions": dict(versions or {}),
        "categories": per_category,
        "categories_with_quantities": interesting,
        "directivity_or_efficiency_quantities": directivity,
        "supports_efficiency": bool(directivity),
        "records": [record.to_dict() for record in records],
        "verdict": ("a directivity or efficiency quantity is enumerable in this context"
                    if directivity else
                    "no directivity or efficiency quantity answered in any probed category; "
                    "this is evidence for this project/design/context only"),
    }
