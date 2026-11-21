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

from .touchstone import FORMATS, FREQUENCY_TO_GHZ, _parse_touchstone

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


def _best_supported_window(samples, width, max_gap):
    """Find the lowest window maximum with a monotone deque in O(n) time."""
    from collections import deque

    worst = deque()
    left = 0
    best_key = best = None
    tolerance = _FREQUENCY_TOLERANCE_GHZ
    for right, sample in enumerate(samples):
        frequency, value = sample["frequency_ghz"], sample["s11_db"]
        if sample["has_invalid_duplicate"] or value is None:
            worst.clear()
            left = right + 1
            continue
        if right > left and frequency - samples[right - 1]["frequency_ghz"] > max_gap + tolerance:
            worst.clear()
            left = right
        while worst and samples[worst[-1]]["s11_db"] < value:
            worst.pop()
        worst.append(right)
        # Of all windows ending here, the shortest one spanning the requested
        # width cannot have a worse maximum than any longer one.
        while left < right and frequency - samples[left + 1]["frequency_ghz"] >= width - tolerance:
            left += 1
        while worst[0] < left:
            worst.popleft()
        start = samples[left]["frequency_ghz"]
        if frequency - start < width - tolerance:
            continue
        peak = samples[worst[0]]
        key = (peak["s11_db"], frequency - start, start)
        if best_key is None or key < best_key:
            best_key, best = key, ([start, frequency], peak)
    return best


def _frequency_suggestions(samples, band, spec, critical_margin, limit, predicted_worst=None):
    """Choose the next measurements, starting with missing endpoints and invalid rows."""
    if not limit:
        return []
    low, high = band
    selected = [r for r in samples if low <= r["frequency_ghz"] <= high]
    by_freq = {r["frequency_ghz"]: r["s11_db"] for r in selected
               if r["s11_db"] is not None and not r["has_invalid_duplicate"]}
    invalid = {r["frequency_ghz"] for r in selected if r["has_invalid_duplicate"]}
    tolerance = _FREQUENCY_TOLERANCE_GHZ
    threshold = spec.threshold_db - spec.guard_margin_db
    candidates = []

    def add(priority, score, frequency, reason, bracket):
        candidates.append((priority, score, frequency, {
            "frequency_ghz": frequency, "reason": reason, "bracket_ghz": list(bracket),
            "bracket_s11_db": [by_freq.get(f) for f in bracket]}))

    for endpoint, reason in ((low, "MISSING_LOW_ENDPOINT"), (high, "MISSING_HIGH_ENDPOINT")):
        if endpoint not in by_freq:
            add(0, 0, endpoint, reason, (endpoint, endpoint))
    for frequency in invalid:
        add(1, 0, frequency, "INVALID_SAMPLE", (frequency, frequency))
    if predicted_worst is not None:
        add(2, 0, predicted_worst, "VERIFY_PREDICTED_WORST", (predicted_worst, predicted_worst))
    axis = sorted(set(by_freq) | {low, high})
    for a, b in zip(axis, axis[1:]):
        gap = b - a
        if gap <= 2 * tolerance:
            continue
        midpoint = a + gap * 0.5
        margins = [threshold - by_freq[f] for f in (a, b) if f in by_freq]
        crossing = len(margins) == 2 and min(margins) < 0 <= max(margins)
        closest = min(map(abs, margins), default=math.inf)
        if gap > spec.max_gap_ghz + tolerance:
            # For equally wide holes, refine threshold crossings and the most
            # marginal sampled brackets before flat, comfortably matched areas.
            add(3, (-gap, not crossing, closest), midpoint, "SAMPLING_GAP", (a, b))
        elif crossing:
            add(4, -gap, midpoint, "THRESHOLD_CROSSING", (a, b))
        elif len(margins) == 2 and closest <= critical_margin:
            add(5, closest, midpoint, "NEAR_THRESHOLD", (a, b))
    suggestions, chosen = [], []
    for _, _, frequency, row in sorted(candidates, key=lambda item: item[:3]):
        if any(abs(old - frequency) <= tolerance for old in chosen):
            continue
        chosen.append(frequency)
        suggestions.append(row)
        if len(suggestions) >= limit:
            break
    return suggestions


def assess_s11_requirement(points: Iterable[Any], spec: BandAcceptanceSpec, *, source: str,
                           required_width_ghz: float | None = None,
                           learning_contract: Mapping[str, Any] | None = None,
                           expected_contract: Mapping[str, Any] | None = None,
                           max_suggestions: int = 6,
                           critical_margin_db: float = 1.0) -> dict[str, Any]:
    """Score a fixed band or find a sample-supported window of at least the specified width.

    Positive margin is favourable. Window endpoints are input frequencies and can
    span more than the requested width; interpolated endpoints are never accepted.
    Coverage is local to the selected interval. The full search-domain coverage
    is reported separately. Suggestions are candidate frequencies for the next
    solve and carry no acceptance weight.
    """
    if isinstance(max_suggestions, bool) or not isinstance(max_suggestions, int) or max_suggestions < 0:
        raise ValueError("max_suggestions must be a nonnegative integer")
    critical_margin_db = _finite_number(critical_margin_db)
    if critical_margin_db < 0:
        raise ValueError("critical_margin_db must be nonnegative")
    low, high = spec.band_ghz
    width = None if required_width_ghz is None else _finite_number(required_width_ghz)
    if width is not None and not 2 * _FREQUENCY_TOLERANCE_GHZ < width <= high - low + _FREQUENCY_TOLERANCE_GHZ:
        raise ValueError("required_width_ghz must fit inside the search band and exceed frequency tolerance")
    fixed = assess_s11_band(points, spec, source=source, learning_contract=learning_contract,
                            expected_contract=expected_contract)
    evidence = fixed["discrete_evidence"]
    samples = evidence["points"]
    domain_complete = evidence["complete_for_sampling_policy"]
    support = evidence["frequency_ghz"]
    interval = [support[0], support[-1]] if support else None
    worst = fixed["worst_point"]
    coverage = domain_complete
    verdict = fixed["status"]
    if width is not None:
        match = _best_supported_window(samples, width, spec.max_gap_ghz)
        interval, worst = match if match else (None, None)
        # A malformed row with an unknown location could lie inside any window.
        coverage = bool(match and not any(i["frequency_ghz"] is None for i in evidence["invalid_rows"]))
        if evidence["trusted"]:
            if coverage and worst["s11_db"] <= spec.threshold_db - spec.guard_margin_db:
                verdict = "SAMPLED_PASS"
            elif domain_complete:
                verdict = "NO_PASSING_SAMPLED_WINDOW"
            else:
                verdict = "INSUFFICIENT_EVIDENCE"
    margin = spec.threshold_db - worst["s11_db"] if worst is not None else None
    guarded_margin = margin - spec.guard_margin_db if margin is not None else None
    worst_f = worst["frequency_ghz"] if worst else None
    # A passing window needs local refinement. In a sparse or failing search,
    # gaps elsewhere in the search band can still contain a better window.
    refine_band = interval if width is not None and coverage and guarded_margin >= 0 else spec.band_ghz
    proposals = _frequency_suggestions(samples, refine_band, spec, critical_margin_db,
                                       max_suggestions, worst_f if fixed["source"] == "PREDICTED" else None)
    longest = max(fixed["passing_sample_ranges"], key=lambda span: span["span_ghz"], default=None)
    passing_width = longest["span_ghz"] if longest else 0.0
    return {"mode": "fixed" if width is None else "window", "required_width_ghz": width,
            "longest_passing_span_ghz": passing_width,
            "longest_passing_interval_ghz": [longest["low_ghz"], longest["high_ghz"]] if longest else None,
            "sampled_width_shortfall_ghz": None if width is None else max(0.0, width - passing_width),
            "source": fixed["source"], "search_band_ghz": [low, high], "interval_ghz": interval,
            "worst_s11_db": worst["s11_db"] if worst else None, "worst_frequency_ghz": worst_f,
            "worst_margin_db": margin, "worst_guarded_margin_db": guarded_margin,
            "measured_pass": verdict == "SAMPLED_PASS", "verdict": verdict,
            "coverage_complete": coverage, "search_coverage_complete": domain_complete,
            "continuous_band_proven": False, "contract_compatible": fixed["contract_compatible"],
            "config": fixed["config"], "suggestions": proposals}


def _read_s1p_evidence(path):
    path = Path(path)
    if path.suffix.lower() != ".s1p":
        raise ValueError("band acceptance file input must be a one-port .s1p")
    data = path.read_bytes()
    candidates = 0
    frequency_scale = 1.0
    fmt = "MA"
    invalid_points = []
    text_image = data.decode("utf-8", errors="replace")
    for line in text_image.splitlines():
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
    points = _parse_touchstone(text_image)
    omitted = max(0, candidates - len(points))
    artifact = {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(),
                          "data_rows": candidates, "parsed_rows": len(points), "omitted_rows": omitted}
    return points, invalid_points, artifact


def assess_touchstone_requirement(path: str | Path, spec: BandAcceptanceSpec, *, source: str,
                                  required_width_ghz: float | None = None,
                                  learning_contract: Mapping[str, Any] | None = None,
                                  expected_contract: Mapping[str, Any] | None = None,
                                  max_suggestions: int = 6, critical_margin_db: float = 1.0):
    """Score a one-port artifact after strict row validation, retaining its byte digest."""
    points, invalid, artifact = _read_s1p_evidence(path)
    if not points or invalid or artifact["omitted_rows"] or any(f <= 0 for f, _ in points):
        raise ValueError("Touchstone file is empty or contains invalid or omitted data rows")
    result = assess_s11_requirement(points, spec, source=source, required_width_ghz=required_width_ghz,
                                    learning_contract=learning_contract, expected_contract=expected_contract,
                                    max_suggestions=max_suggestions, critical_margin_db=critical_margin_db)
    result["artifact"] = artifact
    return result


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
    points, invalid_points, artifact = _read_s1p_evidence(path)
    result = assess_s11_band([*points, *invalid_points], spec, source=source, learning_contract=learning_contract,
                             expected_contract=expected_contract,
                             preview_frequencies_ghz=preview_frequencies_ghz)
    result["artifact"] = artifact
    omitted = artifact["omitted_rows"]
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
