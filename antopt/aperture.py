"""Offline complex-field diagnostics for a cylindrical rim-slot aperture.

This module neither imports AEDT nor infers near fields from a gain pattern.
All integrals below are squared *electric-field* integrals (V^2), not radiated
power (W). Magnetic fields, equivalent currents, materials and an exterior
Green function would be needed for a radiation calculation.

CSV contract (``python -m antopt.aperture --example`` prints a complete file):
metadata lines ``# key=value`` precede a table with columns
``phi,z,frequency,phase_reference,ex_re,ex_im,ey_re,ey_im,ez_re,ez_im``.
Required metadata: frequency, frequency_unit, radius, length_unit, angle_unit,
field_unit, coordinate_system=Global, cylinder_axis=global_z,
phasor_convention=exp(+jwt) or exp(-jwt), phase_reference, source.
Also declare phi_count (unique angles, excluding the seam) and z_count, so
omitted whole rows/columns cannot silently become a coarser grid.
The cylinder is centred on the Global z axis; z is a Global coordinate.
Cartesian complex fields must share the declared excitation phase reference,
not be individually normalised at each sample. This declaration is checked for
consistency; a CSV alone cannot prove that the export used a common reference.

Only a complete, uniformly spaced periodic phi grid crossed with >=2 distinct
z coordinates is supported. z may be nonuniform. The seam may be omitted or
repeated once at a 2*pi separation with matching complex fields. Diagnostics
use periodic rectangular quadrature in phi and trapezoidal quadrature in z.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import io
import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np


_FREQUENCY_UNITS = {"Hz": 1.0, "kHz": 1e3, "MHz": 1e6, "GHz": 1e9}
_LENGTH_UNITS = {"m": 1.0, "cm": 1e-2, "mm": 1e-3, "um": 1e-6}
_ANGLE_UNITS = {"rad": 1.0, "deg": math.pi / 180.0}
_FIELD_UNITS = {"V/m": 1.0, "mV/m": 1e-3, "kV/m": 1e3}
_CONVENTIONS = {"exp(+jwt)", "exp(-jwt)"}
_COLUMNS = ("phi", "z", "frequency", "phase_reference", "ex_re", "ex_im",
            "ey_re", "ey_im", "ez_re", "ez_im")
_METADATA = ("frequency", "frequency_unit", "radius", "length_unit",
             "angle_unit", "field_unit", "coordinate_system", "cylinder_axis",
             "phasor_convention", "phase_reference", "source", "phi_count", "z_count")


def _number(value: Any, name: str, *, positive: bool = False) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError(f"{name} must be finite" + (" and positive" if positive else ""))
    return result


def _unit(metadata: dict[str, str], name: str, choices: dict[str, float]) -> float:
    try:
        return choices[metadata[name]]
    except KeyError as exc:
        raise ValueError(f"{name} must be one of {', '.join(choices)}") from exc


@dataclass(frozen=True)
class ApertureField:
    """Validated SI-valued field, with shape (z, phi, Cartesian component).

    Construct through :func:`read_aperture_csv`, or supply SI values explicitly.
    phi is canonical and ascending in [0, 2*pi); z is strictly ascending.
    """

    frequency_hz: float
    radius_m: float
    phi_rad: np.ndarray
    z_m: np.ndarray
    e_global_v_per_m: np.ndarray
    phase_reference: str
    phasor_convention: str
    declared_source: str
    seam_removed: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "frequency_hz", _number(self.frequency_hz, "frequency_hz", positive=True))
        object.__setattr__(self, "radius_m", _number(self.radius_m, "radius_m", positive=True))
        phi = np.array(self.phi_rad, dtype=float, copy=True)
        z = np.array(self.z_m, dtype=float, copy=True)
        e = np.array(self.e_global_v_per_m, dtype=complex, copy=True)
        if phi.ndim != 1 or len(phi) < 3 or z.ndim != 1 or len(z) < 2:
            raise ValueError("a complete phi grid (>=3) and z grid (>=2) are required")
        if not np.isfinite(phi).all() or not np.isfinite(z).all() or not np.isfinite(e).all():
            raise ValueError("coordinates and complex fields must all be finite")
        if e.shape != (len(z), len(phi), 3):
            raise ValueError("e_global_v_per_m shape must be (len(z_m), len(phi_rad), 3)")
        if not (np.diff(phi) > 0).all() or not (np.diff(z) > 0).all():
            raise ValueError("phi and z coordinates must be strictly ascending")
        if phi[0] < 0 or phi[-1] >= 2 * math.pi:
            raise ValueError("phi_rad must be canonical in [0, 2*pi)")
        gaps = np.diff(np.r_[phi, phi[0] + 2 * math.pi])
        if not np.allclose(gaps, 2 * math.pi / len(phi), rtol=1e-8, atol=1e-10):
            raise ValueError("phi must be a complete uniformly spaced periodic grid; missing angles are not interpolated")
        if not isinstance(self.phase_reference, str) or not self.phase_reference.strip():
            raise ValueError("an explicit common phase_reference is required")
        if self.phasor_convention not in _CONVENTIONS:
            raise ValueError("an explicit exp(+jwt) or exp(-jwt) phasor_convention is required")
        if not isinstance(self.declared_source, str) or not self.declared_source.strip():
            raise ValueError("declared_source must identify the origin, e.g. SYNTHETIC")
        for name, value in (("phi_rad", phi), ("z_m", z), ("e_global_v_per_m", e)):
            value.setflags(write=False)
            object.__setattr__(self, name, value)

    def cylindrical_components(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return (E_r, E_phi, E_z) in local cylindrical bases, in V/m."""
        ex, ey, ez = np.moveaxis(self.e_global_v_per_m, -1, 0)
        cos_phi, sin_phi = np.cos(self.phi_rad), np.sin(self.phi_rad)
        return cos_phi * ex + sin_phi * ey, -sin_phi * ex + cos_phi * ey, ez


def read_aperture_csv(path: str | Path) -> ApertureField:
    """Read an explicitly labelled, single-frequency common-reference CSV.

    Extra columns are refused so incompatible conventions are not silently
    ignored. Radius/units/frame/reference are file metadata; every row must
    repeat frequency and phase_reference to expose mixed exports.
    """
    metadata: dict[str, str] = {}
    table: list[str] = []
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        if line.lstrip().startswith("#"):
            if table:
                raise ValueError("metadata must precede the CSV header")
            key, sep, value = line.lstrip()[1:].partition("=")
            key, value = key.strip(), value.strip()
            if not sep or key not in _METADATA or not value:
                raise ValueError(f"unknown or empty metadata: {key}")
            if key in metadata:
                raise ValueError(f"duplicate metadata: {key}")
            metadata[key] = value
        else:
            table.append(line)
    missing = set(_METADATA) - metadata.keys()
    if missing:
        raise ValueError("missing explicit metadata: " + ", ".join(sorted(missing)))
    if metadata["coordinate_system"] != "Global" or metadata["cylinder_axis"] != "global_z":
        raise ValueError("only a cylinder centred on the Global z axis is supported")
    frequency_scale = _unit(metadata, "frequency_unit", _FREQUENCY_UNITS)
    length_scale = _unit(metadata, "length_unit", _LENGTH_UNITS)
    angle_scale = _unit(metadata, "angle_unit", _ANGLE_UNITS)
    field_scale = _unit(metadata, "field_unit", _FIELD_UNITS)
    frequency = _number(metadata["frequency"], "frequency", positive=True) * frequency_scale
    radius = _number(metadata["radius"], "radius", positive=True) * length_scale
    reader = csv.DictReader(io.StringIO("\n".join(table)))
    if reader.fieldnames is None or len(reader.fieldnames) != len(set(reader.fieldnames)) or set(reader.fieldnames) != set(_COLUMNS):
        raise ValueError("CSV columns must be exactly: " + ",".join(_COLUMNS))
    entries: dict[tuple[float, float], tuple[float, np.ndarray]] = {}
    closed_seams: set[tuple[float, float]] = set()
    seam_removed = False
    for row_number, row in enumerate(reader, 2):
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"incomplete CSV row {row_number}")
        if row["phase_reference"].strip() != metadata["phase_reference"]:
            raise ValueError(f"mixed phase references at CSV row {row_number}")
        row_frequency = _number(row["frequency"], "row frequency", positive=True) * frequency_scale
        if not math.isclose(row_frequency, frequency, rel_tol=1e-12, abs_tol=0.0):
            raise ValueError(f"mixed frequencies at CSV row {row_number}")
        raw_phi = _number(row["phi"], "phi") * angle_scale
        if raw_phi < -1e-10 or raw_phi > 2 * math.pi + 1e-10:
            raise ValueError("CSV phi must lie in [0, 360 deg] or [0, 2*pi rad]")
        phi = 0.0 if abs(raw_phi) < 1e-10 or abs(raw_phi - 2 * math.pi) < 1e-10 else raw_phi
        z = _number(row["z"], "z") * length_scale
        e = np.array([complex(_number(row[f"e{axis}_re"], f"e{axis}_re"),
                              _number(row[f"e{axis}_im"], f"e{axis}_im"))
                      for axis in "xyz"], dtype=complex) * field_scale
        key = (z, phi)
        if key in entries:
            old_phi, old_e = entries[key]
            if key in closed_seams or not math.isclose(abs(raw_phi - old_phi), 2 * math.pi, abs_tol=1e-10):
                raise ValueError("duplicate phi/z sample")
            if not np.allclose(old_e, e, rtol=1e-9, atol=0.0):
                raise ValueError("complex field mismatch at periodic phi seam")
            seam_removed = True
            closed_seams.add(key)
        else:
            entries[key] = (raw_phi, e)
    phi_values = sorted({key[1] for key in entries})
    z_values = sorted({key[0] for key in entries})
    for name, actual in (("phi_count", len(phi_values)), ("z_count", len(z_values))):
        expected = _number(metadata[name], name, positive=True)
        if not expected.is_integer() or expected != actual:
            raise ValueError(f"{name} does not match the declared complete sampling grid")
    if not entries or len(entries) != len(phi_values) * len(z_values):
        raise ValueError("missing phi/z grid cells; no interpolation or zero filling is performed")
    fields = np.array([[entries[(z, phi)][1] for phi in phi_values] for z in z_values])
    return ApertureField(frequency, radius, np.array(phi_values), np.array(z_values), fields,
                         metadata["phase_reference"], metadata["phasor_convention"],
                         metadata["source"], seam_removed)


def _z_weights(z: np.ndarray) -> np.ndarray:
    weights = np.empty(len(z))
    weights[0], weights[-1] = (z[1] - z[0]) / 2, (z[-1] - z[-2]) / 2
    weights[1:-1] = (z[2:] - z[:-2]) / 2
    return weights


def _coherence(component: np.ndarray, z: np.ndarray, weights: np.ndarray) -> dict[str, Any]:
    amplitude_sum = np.abs(component).sum(axis=1)
    sum_complex = component.sum(axis=1)
    energy_sum = (np.abs(component) ** 2).sum(axis=1)
    rows = []
    for index, (zz, amp, complex_sum, energy) in enumerate(zip(z, amplitude_sum, sum_complex, energy_sum)):
        r = min(1.0, float(abs(complex_sum) / amp)) if amp > 0 else None
        # At a zero resultant the mean phase is undefined and dispersion is
        # unbounded; JSON uses null, accompanied by an explicit condition.
        resolvable = r is not None and r > 1e-12
        rows.append({
            "z_m": float(zz), "nonzero_phi_samples": int(np.count_nonzero(np.abs(component[index]) > 0)),
            "first_order_coherence_magnitude": r,
            "coherent_energy_fraction": min(1.0, float(abs(complex_sum) ** 2 / (component.shape[1] * energy))) if energy > 0 else None,
            "circular_mean_phase_deg": float(np.angle(complex_sum, deg=True)) if resolvable else None,
            "amplitude_weighted_circular_std_deg": math.degrees(math.sqrt(max(0.0, -2 * math.log(r)))) if resolvable else None,
            "phase_condition": "defined" if resolvable else ("zero_field" if amp == 0 else "zero_resultant_mean_undefined_dispersion_unbounded"),
        })
    amp_total = float(np.dot(weights, amplitude_sum))
    energy_total = float(np.dot(weights, energy_sum))
    return {
        "definition": "within each z: |sum_phi E|/sum_phi |E|; phase statistics are amplitude-weighted, zeros have no phase",
        "within_z_first_order_coherence_magnitude": min(1.0, float(np.dot(weights, np.abs(sum_complex)) / amp_total)) if amp_total > 0 else None,
        "within_z_coherent_energy_fraction": min(1.0, float(np.dot(weights, np.abs(sum_complex) ** 2) / (component.shape[1] * energy_total))) if energy_total > 0 else None,
        "per_z": rows,
    }


def diagnose_aperture(field: ApertureField, *, sectors: int = 3) -> dict[str, Any]:
    """Compute dimensionless coherence/symmetry and SI squared-field integrals.

    Sector symmetry compares the full tangential complex field to a rotation
    through k*2*pi/sectors, in the *local* (e_phi,e_z) basis. The denominator
    is the norm of the original field. Both raw and phase-aligned residuals
    are reported, so a rotating phase mode cannot masquerade as in-phase
    symmetry. An incompatible phi grid returns a condition, not interpolation.
    """
    if isinstance(sectors, bool) or not isinstance(sectors, (int, np.integer)) or sectors < 2:
        raise ValueError("sectors must be an integer >=2")
    er, ep, ez = field.cylindrical_components()
    nphi = len(field.phi_rad)
    wz = _z_weights(field.z_m)
    area_weights = wz[:, None] * field.radius_m * 2 * math.pi / nphi
    powers = {name: float(np.sum(area_weights * np.abs(component) ** 2))
              for name, component in (("e_r", er), ("e_phi", ep), ("e_z", ez))}
    if (not all(math.isfinite(value) for value in powers.values())
            or any(value == 0 and np.any(component != 0) for value, component in
                   zip(powers.values(), (er, ep, ez)))):
        raise ValueError("field/coordinate dynamic range exceeds finite float64 diagnostics")
    tangential = powers["e_phi"] + powers["e_z"]
    vector = np.stack((ep, ez), axis=-1)
    comparisons = []
    comparable = nphi % sectors == 0
    if comparable and tangential > 0:
        for k in range(1, sectors):
            shifted = np.roll(vector, -k * (nphi // sectors), axis=1)
            inner = np.sum(area_weights[..., None] * np.conj(vector) * shifted)
            angle = float(np.angle(inner)) if abs(inner) > 1e-12 * tangential else None
            def relative_norm(delta: np.ndarray) -> float:
                return math.sqrt(max(0.0, float(np.sum(area_weights[..., None] * np.abs(delta) ** 2)) / tangential))
            comparisons.append({
                "rotation_deg": 360.0 * k / sectors,
                "relative_complex_l2": relative_norm(shifted - vector),
                "relative_amplitude_l2": relative_norm(np.abs(shifted) - np.abs(vector)),
                "phase_offset_deg": math.degrees(angle) if angle is not None else None,
                "phase_aligned_relative_l2": relative_norm(shifted * np.exp(-1j * angle) - vector) if angle is not None else None,
                "phase_alignment_condition": "defined" if angle is not None else "orthogonal_or_zero_complex_overlap",
            })
    result = {
        "schema_version": 1,
        "kind": "CYLINDRICAL_APERTURE_FIELD_DIAGNOSTIC",
        "source": "INPUT_COMPLEX_FIELD",
        "input": {"declared_source": field.declared_source, "frequency_hz": field.frequency_hz,
                  "radius_m": field.radius_m, "z_range_m": [float(field.z_m[0]), float(field.z_m[-1])],
                  "coordinate_system": "Global", "cylinder_axis": "global_z",
                  "phase_reference": field.phase_reference, "phasor_convention": field.phasor_convention,
                  "phi_count": nphi, "z_count": len(field.z_m), "seam_removed": field.seam_removed},
        "conditions": {"complete_periodic_uniform_phi_grid": True,
                       "quadrature": "periodic phi rectangle; nonuniform z trapezoid; dS=radius*dphi*dz",
                       "phase_reference_consistency": "declared_and_checked_across_rows_not_independently_verified",
                       "zero_tangential_field": tangential == 0,
                       "sector_grid_compatible": comparable},
        "squared_field_integrals_v2": {**powers, "tangential": tangential, "total_electric": tangential + powers["e_r"]},
        "ez_fraction_of_tangential_squared_field": powers["e_z"] / tangential if tangential > 0 else None,
        "azimuthal_coherence": {"e_z": _coherence(ez, field.z_m, wz), "e_phi": _coherence(ep, field.z_m, wz)},
        "sector_symmetry": {"sectors": int(sectors), "basis": "local cylindrical (e_phi,e_z)",
                            "normalization": "L2(reference tangential field); same full-circle area weights",
                            "condition": "incompatible_phi_count_no_interpolation" if not comparable else ("zero_tangential_field" if tangential == 0 else "defined"),
                            "rotations": comparisons},
        "limitations": [
            "Electric-field diagnostics only: squared-field integrals have units V^2, not radiated power W or efficiency.",
            "No magnetic field/equivalent-current propagation: no far-field beam, gain, S11 or aperture performance is inferred.",
            "Input provenance and common excitation phase must be validated by the exporter; this module does not verify HFSS or measurements.",
            "A component's phase is undefined at zero amplitude; near-zero numerical phase/coherence needs a measurement noise assessment.",
            "Grid quadrature is not a spatial-convergence certificate; repeat with finer exported near-field sampling when available.",
        ],
    }
    # Fail explicitly rather than emitting NaN/Infinity when a finite but
    # extreme input exceeds float64's squared-field dynamic range.
    try:
        json.dumps(result, allow_nan=False)
    except (ValueError, OverflowError) as exc:
        raise ValueError("field/coordinate dynamic range exceeds finite float64 diagnostics") from exc
    return result


def example_csv() -> str:
    """Synthetic uniform axial field; not an exported HFSS result."""
    lines = ["# frequency=28", "# frequency_unit=GHz", "# radius=80", "# length_unit=mm",
             "# angle_unit=deg", "# field_unit=V/m", "# coordinate_system=Global",
             "# cylinder_axis=global_z", "# phasor_convention=exp(+jwt)",
             "# phase_reference=synthetic_common_excitation", "# source=SYNTHETIC",
             "# phi_count=6", "# z_count=2",
             ",".join(_COLUMNS)]
    for z in (-1.25, 1.25):
        for phi in range(0, 360, 60):
            lines.append(f"{phi},{z},28,synthetic_common_excitation,0,0,0,0,1,0")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", nargs="?", help="common-reference complex E CSV")
    parser.add_argument("--sectors", type=int, default=3, help="rotational sectors (default: 3)")
    parser.add_argument("--output", type=Path, help="write JSON to this explicit path; default stdout")
    parser.add_argument("--example", action="store_true", help="print a synthetic CSV template to stdout")
    args = parser.parse_args(argv)
    if args.example:
        if args.input or args.output:
            parser.error("--example is used without input or --output; redirect stdout to save CSV")
        print(example_csv(), end="")
        return 0
    if not args.input:
        parser.error("input CSV is required (or use --example)")
    if args.output and args.output.resolve() == Path(args.input).resolve():
        parser.error("output must be different from the input CSV")
    try:
        result = diagnose_aperture(read_aperture_csv(args.input), sectors=args.sectors)
        encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.write_text(encoded, encoding="utf-8")
        else:
            print(encoded, end="")
    except (OSError, ValueError, csv.Error) as exc:
        print(f"aperture: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
