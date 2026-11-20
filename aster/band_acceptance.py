"""Conservative S11 band checks: sampled evidence is not continuous-band proof.

Frequencies are GHz and S11 is in dB (20*log10 of the reflection magnitude).
The caller must identify observed solver/measurement data as ``MEASURED`` and
may require a learning-contract identity. No interpolation enters acceptance.
"""
from __future__ import annotations

import bisect
import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .touchstone import FORMATS, FREQUENCY_TO_GHZ, read_touchstone

_FREQUENCY_TOLERANCE_GHZ = 1e-9


@dataclass(frozen=True)
class BandAcceptanceSpec:
    band_ghz: tuple[float, float]
    max_gap_ghz: float
    threshold_db: float = -10.0
    guard_margin_db: float = 0.0

    def __post_init__(self) -> None:
        try:
            if isinstance(self.band_ghz, (str, bytes)):
                raise ValueError("band must be a numeric pair")
            low, high = self.band_ghz
            if any(isinstance(v, bool) for v in (low, high, self.max_gap_ghz,
                                                 self.threshold_db, self.guard_margin_db)):
                raise ValueError("boolean is not an acceptance setting")
            low, high = float(low), float(high)
            gap = float(self.max_gap_ghz)
            threshold = float(self.threshold_db)
            guard = float(self.guard_margin_db)
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValueError("band, gap, threshold and guard margin must be numeric") from exc
        if not all(math.isfinite(v) for v in (low, high, gap, threshold, guard)):
            raise ValueError("band acceptance settings must be finite")
        if not 0 < low < high or high - low <= 2 * _FREQUENCY_TOLERANCE_GHZ:
            raise ValueError("band_ghz must be a positive increasing pair")
        if gap <= 2 * _FREQUENCY_TOLERANCE_GHZ:
            raise ValueError("max_gap_ghz must exceed the frequency comparison tolerance")
        if threshold > 0 or guard < 0:
            raise ValueError("threshold_db must be <= 0 and guard_margin_db must be >= 0")
        if not math.isfinite(threshold - guard):
            raise ValueError("guarded threshold must be finite")
        object.__setattr__(self, "band_ghz", (low, high))
        object.__setattr__(self, "max_gap_ghz", gap)
        object.__setattr__(self, "threshold_db", threshold)
        object.__setattr__(self, "guard_margin_db", guard)


def _finite_number(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean is not a response value")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite response value")
    return number


def _normalise(points: Iterable[Any], spec: BandAcceptanceSpec):
    parsed, issues = [], []
    low, high = spec.band_ghz
    for index, point in enumerate(points):
        frequency = None
        try:
            if isinstance(point, Mapping):
                raw_frequency = point.get("freq_ghz", point.get("frequency_ghz"))
                raw_value = point["s11_db"]
            else:
                if len(point) != 2:
                    raise ValueError("expected (frequency_ghz, s11_db)")
                raw_frequency, raw_value = point
            frequency = _finite_number(raw_frequency)
            if frequency <= 0:
                frequency = None
                raise ValueError("frequency must be positive")
            for endpoint in (low, high):
                if abs(frequency - endpoint) <= _FREQUENCY_TOLERANCE_GHZ:
                    frequency = endpoint
            value = _finite_number(raw_value)
            if not all(math.isfinite(limit - value) for limit in (
                    spec.threshold_db, spec.threshold_db - spec.guard_margin_db)):
                raise ValueError("response margin overflows")
        except (TypeError, ValueError, KeyError, OverflowError) as exc:
            issues.append({"index": index, "frequency_ghz": frequency, "reason": str(exc)})
            if frequency is not None:
                parsed.append((frequency, None))
            continue
        parsed.append((frequency, value))
    parsed.sort(key=lambda item: item[0])
    groups = []
    for frequency, value in parsed:
        if groups and frequency - groups[-1]["frequency_ghz"] <= _FREQUENCY_TOLERANCE_GHZ:
            group = groups[-1]
        else:
            group = {"frequency_ghz": frequency, "values": [], "invalid": False, "count": 0}
            groups.append(group)
        group["count"] += 1
        if value is None:
            group["invalid"] = True
        else:
            group["values"].append(value)
    for group in groups:
        values = group.pop("values")
        group["s11_db"] = max(values) if values else None
        group["conflicting_duplicate"] = bool(values and max(values) - min(values) > 1e-9)
    return groups, issues


def _sample_ranges(rows: list[dict[str, Any]], limit: float, max_gap: float):
    ranges = []
    current = None
    for row in rows:
        frequency, value = row["frequency_ghz"], row["s11_db"]
        if row["invalid"] or value is None or value > limit:
            current = None
            continue
        if current is None or frequency - current["high_ghz"] > max_gap + _FREQUENCY_TOLERANCE_GHZ:
            current = {"low_ghz": frequency, "high_ghz": frequency,
                       "span_ghz": 0.0, "sample_count": 0, "evidence": "DISCRETE_SAMPLES"}
            ranges.append(current)
        current["high_ghz"] = frequency
        current["span_ghz"] = frequency - current["low_ghz"]
        current["sample_count"] += 1
    return ranges


def _interpolation_preview(rows, frequencies, spec):
    if frequencies is None:
        return None
    queries = sorted(set(_finite_number(f) for f in frequencies))
    if any(f <= 0 for f in queries):
        raise ValueError("preview frequencies must be positive")
    axis = [row["frequency_ghz"] for row in rows]
    output = []
    for frequency in queries:
        point = {"frequency_ghz": frequency, "s11_db": None,
                 "at_input_sample": False, "bracket_gap_ghz": None,
                 "exceeds_max_gap": False, "reason": "OUTSIDE_INPUT_SUPPORT"}
        index = bisect.bisect_left(axis, frequency)
        exact = next((j for j in (index - 1, index) if 0 <= j < len(rows)
                      and abs(axis[j] - frequency) <= _FREQUENCY_TOLERANCE_GHZ), None)
        if exact is not None:
            row = rows[exact]
            if not row["invalid"] and row["s11_db"] is not None:
                point.update(s11_db=row["s11_db"], at_input_sample=True, reason="INPUT_SAMPLE")
            else:
                point["reason"] = "INVALID_INPUT_SAMPLE"
        elif 0 < index < len(rows):
            left, right = rows[index - 1], rows[index]
            if left["invalid"] or right["invalid"] or left["s11_db"] is None or right["s11_db"] is None:
                point["reason"] = "INVALID_BRACKET"
            else:
                gap = right["frequency_ghz"] - left["frequency_ghz"]
                fraction = (frequency - left["frequency_ghz"]) / gap
                point.update(s11_db=(1 - fraction) * left["s11_db"] + fraction * right["s11_db"],
                             bracket_gap_ghz=gap,
                             exceeds_max_gap=gap > spec.max_gap_ghz + _FREQUENCY_TOLERANCE_GHZ,
                             reason="LINEAR_DB_PREVIEW")
        output.append(point)
    return {"source": "PREDICTED", "method": "linear_db", "points": output,
            "usable_for_acceptance": False, "extrapolated": False,
            "warning": "Interpolation is visual preview, not measured or continuous-band evidence"}


def assess_s11_band(points: Iterable[Any], spec: BandAcceptanceSpec, *, source: str,
                    learning_contract: Mapping[str, Any] | None = None,
                    expected_contract: Mapping[str, Any] | None = None,
                    preview_frequencies_ghz: Iterable[float] | None = None) -> dict[str, Any]:
    """Assess a *single* S11 curve without inventing missing frequencies.

    ``SAMPLED_PASS`` means every supported sample meets the guarded threshold,
    endpoints exist and the configured sampling-gap rule is met. It NEVER means
    continuous-band matching. Observed threshold failures can disprove such a
    claim. ``guard_margin_db`` is a deterministic safety margin, not a calibrated
    probability of failure. Contract checks compare identities, as in the S11
    response model; no expected contract means no cross-run compatibility claim.
    """
    if not isinstance(spec, BandAcceptanceSpec):
        raise TypeError("spec must be BandAcceptanceSpec")
    source = str(source).upper()
    rows, issues = _normalise(points, spec)
    low, high = spec.band_ghz
    selected = [r for r in rows if low <= r["frequency_ghz"] <= high]
    finite = [r for r in selected if r["s11_db"] is not None]
    supported = [r for r in finite if not r["invalid"]]
    frequencies = [r["frequency_ghz"] for r in supported]
    relevant_issues = [i for i in issues if i["frequency_ghz"] is None
                       or low <= i["frequency_ghz"] <= high]
    limit = spec.threshold_db - spec.guard_margin_db
    contract_compatible = None
    warnings = ["Finite frequency samples cannot prove continuous-band matching"]
    if expected_contract is not None:
        expected_identity = expected_contract.get("identity")
        if not expected_identity:
            raise ValueError("expected_contract must contain an identity")
        contract_compatible = bool(learning_contract and learning_contract.get("identity") == expected_identity)
        if not contract_compatible:
            warnings.append("Missing or incompatible learning-contract identity")
    else:
        warnings.append("No expected contract supplied; cross-run compatibility was not checked")
    trusted = source == "MEASURED" and contract_compatible is not False
    rescans: dict[tuple[float, float], list[str]] = {}

    def rescan(start, end, reason):
        reasons = rescans.setdefault((float(start), float(end)), [])
        if reason not in reasons:
            reasons.append(reason)

    endpoints = {"low": bool(frequencies and frequencies[0] == low),
                 "high": bool(frequencies and frequencies[-1] == high)}
    gaps = [b - a for a, b in zip(frequencies, frequencies[1:])]
    if not frequencies:
        rescan(low, high, "NO_VALID_SAMPLES")
    else:
        if not endpoints["low"]:
            rescan(low, frequencies[0], "MISSING_LOW_ENDPOINT")
        if not endpoints["high"]:
            rescan(frequencies[-1], high, "MISSING_HIGH_ENDPOINT")
        for start, end in zip(frequencies, frequencies[1:]):
            if end - start > spec.max_gap_ghz + _FREQUENCY_TOLERANCE_GHZ:
                rescan(start, end, "SAMPLING_GAP")
    for issue in relevant_issues:
        frequency = issue["frequency_ghz"]
        if frequency is None:
            rescan(low, high, "UNLOCATED_INVALID_ROW")
        else:
            left = max((f for f in frequencies if f < frequency), default=low)
            right = min((f for f in frequencies if f > frequency), default=high)
            rescan(left, right, "INVALID_SAMPLE")
    complete = bool(frequencies and all(endpoints.values()) and not relevant_issues
                    and all(gap <= spec.max_gap_ghz + _FREQUENCY_TOLERANCE_GHZ for gap in gaps))
    nominal_pass = bool(finite and all(r["s11_db"] <= spec.threshold_db for r in finite))
    guarded_pass = bool(finite and all(r["s11_db"] <= limit for r in finite))
    nominal_failure = any(r["s11_db"] > spec.threshold_db for r in finite)
    guarded_failure = any(r["s11_db"] > limit for r in finite)
    worst = max(finite, key=lambda r: r["s11_db"], default=None)
    worst_point = None if worst is None else {
        "frequency_ghz": worst["frequency_ghz"], "s11_db": worst["s11_db"],
        "margin_db": spec.threshold_db - worst["s11_db"],
        "guarded_margin_db": limit - worst["s11_db"]}
    risk_points = [{"frequency_ghz": r["frequency_ghz"], "s11_db": r["s11_db"],
                    "margin_db": spec.threshold_db - r["s11_db"]}
                   for r in finite if 0 <= spec.threshold_db - r["s11_db"] <= spec.guard_margin_db]
    for r in finite:
        if limit < r["s11_db"] <= spec.threshold_db:
            index = bisect.bisect_left(frequencies, r["frequency_ghz"])
            left = frequencies[index - 1] if index > 0 else low
            right = frequencies[index + 1] if index + 1 < len(frequencies) else high
            rescan(left, right, "GUARD_MARGIN_NOT_MET")
    if source == "PREDICTED":
        status = "PREDICTED_ONLY"
    elif contract_compatible is False:
        status = "INCOMPATIBLE_EVIDENCE"
    elif not trusted:
        status = "INSUFFICIENT_EVIDENCE"
    elif guarded_failure:
        status = "FAIL"
    elif complete and guarded_pass:
        status = "SAMPLED_PASS"
    else:
        status = "INSUFFICIENT_EVIDENCE"
    if not trusted:
        warnings.append("Input values are not accepted as compatible measured evidence")
    if any(r["count"] > 1 for r in selected):
        warnings.append("Duplicate frequencies use the worst finite S11; invalid duplicates remain evidence gaps")
    continuous_failed = trusted and nominal_failure
    return {
        "schema_version": 1, "source": source, "status": status,
        "usable_for_acceptance": trusted and status in {"FAIL", "SAMPLED_PASS"},
        "contract_compatible": contract_compatible,
        "config": {"band_ghz": [low, high], "threshold_db": spec.threshold_db,
                   "guard_margin_db": spec.guard_margin_db, "acceptance_limit_db": limit,
                   "max_gap_ghz": spec.max_gap_ghz},
        "worst_point": worst_point,
        "discrete_evidence": {
            "trusted": trusted, "valid_sample_count": len(finite), "usable_sample_count": len(supported),
            "points": [{"frequency_ghz": r["frequency_ghz"], "s11_db": r["s11_db"],
                        "has_invalid_duplicate": r["invalid"]} for r in selected],
            "frequency_ghz": frequencies, "nominal_samples_pass": nominal_pass,
            "guarded_samples_pass": guarded_pass, "complete_for_sampling_policy": complete,
            "endpoints_present": endpoints, "largest_gap_ghz": max(gaps, default=None),
            "invalid_rows": relevant_issues, "out_of_band_sample_count": len(rows) - len(selected),
            "duplicate_frequency_count": sum(r["count"] - 1 for r in selected),
            "conflicting_duplicate_frequency_count": sum(r["conflicting_duplicate"] for r in selected)},
        "passing_sample_ranges": _sample_ranges(selected, limit, spec.max_gap_ghz),
        "rescan_bands": [{"low_ghz": a, "high_ghz": b, "reasons": reasons}
                         for (a, b), reasons in sorted(rescans.items())],
        "risk_points": risk_points,
        "continuous_band_pass": False if continuous_failed else None,
        "continuous_band_status": "FAILED_AT_MEASURED_POINT" if continuous_failed else "UNPROVEN_FROM_SAMPLES",
        "interpolation_preview": _interpolation_preview(rows, preview_frequencies_ghz, spec),
        "warnings": warnings, "artifact": None}


def assess_touchstone_band(path: str | Path, spec: BandAcceptanceSpec, *, source: str,
                           learning_contract: Mapping[str, Any] | None = None,
                           expected_contract: Mapping[str, Any] | None = None,
                           preview_frequencies_ghz: Iterable[float] | None = None) -> dict[str, Any]:
    """Read an S1P with the shared MA/DB/RI parser, retaining dropped-row risk.

    The permissive shared reader skips malformed rows. This wrapper counts them
    and invalidates a sampled-pass claim instead of hiding that lost evidence.
    A file's existence does not establish convergence, provenance or a contract;
    callers must obtain those from the completed run record.
    """
    path = Path(path)
    if path.suffix.lower() != ".s1p":
        raise ValueError("band acceptance file input must be a one-port .s1p")
    data = path.read_bytes()
    candidates = 0
    frequency_scale = 1.0
    fmt = "MA"
    invalid_points = []
    for line in data.decode("utf-8", errors="replace").splitlines():
        text = line.split("!", 1)[0].strip()
        if not text:
            continue
        if text.startswith("#"):
            options = text[1:].upper().split()
            if any(token in {"Y", "Z", "H", "G"} for token in options):
                raise ValueError("only S-parameter Touchstone data is supported")
            for token in options:
                if token not in set(FREQUENCY_TO_GHZ) | FORMATS | {"S", "R"}:
                    try:
                        _finite_number(token)
                    except (ValueError, TypeError, OverflowError) as exc:
                        raise ValueError("unsupported Touchstone option: %s" % token) from exc
            frequency_scale = next((FREQUENCY_TO_GHZ[token] for token in options
                                    if token in FREQUENCY_TO_GHZ), 1.0)
            fmt = next((token for token in options if token in FORMATS), "MA")
        else:
            candidates += 1
            parts = text.split()
            frequency = math.nan
            try:
                frequency = _finite_number(parts[0]) * frequency_scale
                if len(parts) != 3:
                    raise ValueError("one-port data row must have three columns")
                first = _finite_number(parts[1])
                _finite_number(parts[2])
                if fmt == "MA" and first < 0:
                    raise ValueError("reflection magnitude cannot be negative")
            except (ValueError, TypeError, IndexError, OverflowError):
                invalid_points.append((frequency, math.nan))
    points = read_touchstone(path)
    result = assess_s11_band([*points, *invalid_points], spec, source=source, learning_contract=learning_contract,
                             expected_contract=expected_contract,
                             preview_frequencies_ghz=preview_frequencies_ghz)
    omitted = max(0, candidates - len(points))
    result["artifact"] = {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(),
                          "data_rows": candidates, "parsed_rows": len(points), "omitted_rows": omitted}
    if omitted:
        result["discrete_evidence"]["complete_for_sampling_policy"] = False
        result["discrete_evidence"]["invalid_rows"].append({"index": None, "frequency_ghz": None,
                                                           "reason": "Touchstone parser omitted %d data rows" % omitted})
        result["rescan_bands"].append({"low_ghz": spec.band_ghz[0], "high_ghz": spec.band_ghz[1],
                                       "reasons": ["TOUCHSTONE_ROWS_OMITTED"]})
        result["warnings"].append("Dropped Touchstone rows prevent a complete sampled acceptance")
        if result["status"] == "SAMPLED_PASS":
            result["status"] = "INSUFFICIENT_EVIDENCE"
            result["usable_for_acceptance"] = False
    return result
