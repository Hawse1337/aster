"""Parameterised revolve profiles.

The reference bowls are `OperationType='Polyline'` closed profiles revolved around Z;
treating them with boolean operations destroys them. This module rebuilds the profile
from design parameters instead, so a parameter change is a point change - the object is
never destroyed and the same design can be re-parameterised any number of times.

All coordinates are millimetres, given as ``[x, z]`` pairs in the XZ plane (x is the
radius from the Z axis).
"""
from __future__ import annotations

from typing import Iterable, Sequence

Point = Sequence[float]

# The lower bowl as stored in the f24g05 design (nine points, closed). The project holds
# five designs and the first occurrence in the file has a 22 mm rim, so the extractor picks
# the occurrence whose revolved volume matches the reference body (24 mm rim).
BOWL_TEMPLATE: tuple[tuple[float, float], ...] = (
    (0.0, -1.0),      # axis, at the tip plane
    (6.2, -1.0),      # tip plane outer edge
    (9.5, -4.8),      # cone slope
    (24.0, -4.8),     # rim inner
    (24.0, -5.8),     # rim outer
    (9.044, -5.8),    # back face
    (5.744, -2.0),    # inner slope
    (0.0, -2.0),      # axis, back
    (0.0, -1.0),      # close
)


def _clean(points: Iterable[Sequence[float]]) -> list[list[float]]:
    cleaned: list[list[float]] = []
    for point in points:
        if len(point) != 2:
            raise ValueError("profile points must be [x, z] pairs")
        x, z = float(point[0]), float(point[1])
        if x < 0:
            raise ValueError("profile radius must be >= 0 (got %r)" % x)
        cleaned.append([x, z])
    if len(cleaned) < 3:
        raise ValueError("a closed profile needs at least three points")
    if [round(v, 6) for v in cleaned[0]] != [round(v, 6) for v in cleaned[-1]]:
        cleaned.append(list(cleaned[0]))
    return cleaned


def bowl_profile(tip_offset_mm: float = 1.0, disc_offset_mm: float = 1.0,
                 template: Sequence[Sequence[float]] | None = None) -> list[list[float]]:
    """Lower bowl profile with the tip plane anchored to the disc mid-plane.

    ``tip_offset_mm`` is the distance from the disc mid-plane to the bowl tip, and
    ``disc_offset_mm`` is the distance from the coax mouth to the disc mid-plane; both
    follow the measured reference design (tip 1.0 mm, disc 1.0 mm above the mouth).
    """
    if tip_offset_mm <= 0:
        raise ValueError("tip_offset_mm must be positive")
    if disc_offset_mm < 0:
        raise ValueError("disc_offset_mm must be >= 0")
    base = list(template or BOWL_TEMPLATE)
    # the template's tip plane is its highest z; place it at -tip_offset so that the
    # default tip offset reproduces the reference design exactly
    shift = -tip_offset_mm - max(point[1] for point in base)
    return _clean([[point[0], point[1] + shift] for point in base])


def with_ground_ring(profile: Sequence[Sequence[float]], inner_radius_mm: float,
                     outer_radius_mm: float, height_mm: float,
                     shift_mm: float = 0.0) -> list[list[float]]:
    """Insert a PEC annulus into the (open) tip edge of the bowl profile.

    The ring is welded into the same closed profile, so the bowl and the ring become one
    conductor and no boolean union is ever needed. The ring straddles the tip edge: half
    of it above the tip plane, half below, matching the measured reference design.
    """
    if not 0 < inner_radius_mm < outer_radius_mm:
        raise ValueError("ring radii must satisfy 0 < inner < outer")
    if height_mm <= 0:
        raise ValueError("ring height must be positive")
    points = _clean(profile)
    # tip edge = the second point of the bowl profile (radius along the tip plane)
    tip_index = 1
    tip_x, tip_z = points[tip_index]
    if outer_radius_mm <= tip_x + 1e-9:
        raise ValueError("ring outer radius must exceed the tip edge radius (%.3f mm)"
                         % tip_x)
    z_top = tip_z + height_mm / 2.0 + shift_mm
    z_bottom = tip_z - height_mm / 2.0 + shift_mm
    ring = [
        [inner_radius_mm, z_bottom],
        [outer_radius_mm, z_bottom],
        [outer_radius_mm, z_top],
        [inner_radius_mm, z_top],
        [inner_radius_mm, z_bottom],
    ]
    # splice the ring in right after the tip edge and return to it, keeping the outline
    # closed and non-self-intersecting
    merged = points[:tip_index] + [[tip_x, z_bottom]] + ring[1:] + [[tip_x, z_top]] \
        + points[tip_index + 1:]
    return _clean(merged)

def load_profiles(path: str | Path) -> dict[str, list[list[float]]]:
    """Load the extracted reference profiles (`scripts/extract_profiles.py`)."""
    import json
    from pathlib import Path as _Path

    payload = json.loads(_Path(path).read_text(encoding="utf-8"))
    return {name: [[float(x), float(z)] for x, z in body["points"]]
            for name, body in payload["bodies"].items()}


def shift_profile(points: Sequence[Sequence[float]], dz_mm: float) -> list[list[float]]:
    """Move a profile along Z (bowl tip offset, disc offset)."""
    return _clean([[float(point[0]), float(point[1]) + float(dz_mm)] for point in points])


def scale_disc(points: Sequence[Sequence[float]], radial_factor: float = 1.0,
               thickness_factor: float = 1.0,
               mid_z_mm: float | None = None) -> list[list[float]]:
    """Scale the disc profile in radius and thickness about its own mid-plane.

    Scaling about the mid-plane (not the global origin) keeps the plate centred on the
    coax mouth, which is what the measured `disc_factor` does.
    """
    if radial_factor <= 0 or thickness_factor <= 0:
        raise ValueError("disc scale factors must be positive")
    cleaned = _clean(points)
    zs = [point[1] for point in cleaned]
    mid = (min(zs) + max(zs)) / 2.0 if mid_z_mm is None else float(mid_z_mm)
    return _clean([[point[0] * radial_factor,
                    mid + (point[1] - mid) * thickness_factor] for point in cleaned])


def coax_with_flange(tube: Sequence[Sequence[float]], outer_radius_mm: float,
                     height_mm: float, mouth_z_mm: float = -1.0,
                     shift_mm: float = 0.0) -> list[list[float]]:
    """Step the coax outer wall out to the ground-ring radius at the mouth.

    The ring is PEC of the same body, so a step in the outer wall is geometrically the
    same as welding a ring onto the wall - without any boolean operation. One closed
    profile in, one solid out.
    """
    if height_mm <= 0:
        raise ValueError("ring height must be positive")
    cleaned = _clean(tube)
    wall_radius = max(point[0] for point in cleaned)
    if outer_radius_mm <= wall_radius:
        raise ValueError("ring outer radius must exceed the coax wall radius (%.3f mm)"
                         % wall_radius)
    z_top = mouth_z_mm + height_mm / 2.0 + shift_mm
    z_bottom = mouth_z_mm - height_mm / 2.0 + shift_mm
    stepped: list[list[float]] = []
    for point in cleaned:
        x, z = point
        if abs(x - wall_radius) < 1e-9 and z > z_bottom:
            # replace the straight outer wall with the stepped wall
            if not stepped or abs(stepped[-1][1] - z_bottom) > 1e-9:
                stepped.append([wall_radius, z_bottom])
            stepped.extend([[outer_radius_mm, z_bottom],
                            [outer_radius_mm, z_top],
                            [wall_radius, z_top]])
        stepped.append([x, z])
    return _clean(stepped)

def with_ring_collar(points: Sequence[Sequence[float]], inner_radius_mm: float,
                     outer_radius_mm: float, height_mm: float,
                     shift_mm: float = 0.0, tip_z_mm: float | None = None,
                     mid_radius_mm: float | None = None,
                     outer_height_mm: float | None = None,
                     middle_height_mm: float | None = None) -> list[list[float]]:
    """Raise a PEC collar on the bowl tip face around the coax.

    The measured ground ring straddles the bowl tip face: half of it is buried in the bowl
    and half stands proud. Only the proud half changes the geometry, so a collar of
    `height/2` on the tip-face segment is geometrically identical - and it keeps the
    bowl a single closed profile, so no boolean union is needed.

    Structure A adds a second step: when ``mid_radius_mm`` lies strictly between the inner and
    outer radius and ``outer_height_mm`` differs from ``height_mm``, the collar is raised to
    ``height_mm`` from the inner radius to the split radius and to ``outer_height_mm`` beyond
    it. The measured data showed directivity wants a low ring while bandwidth wants a taller
    one, so one radial geometry can now carry both heights.
    """
    if not 0 < inner_radius_mm < outer_radius_mm:
        raise ValueError("ring radii must satisfy 0 < inner < outer")
    if height_mm <= 0:
        raise ValueError("ring height must be positive")
    split = mid_radius_mm is not None and float(mid_radius_mm) < outer_radius_mm - 1e-9
    if split:
        mid = float(mid_radius_mm)
        outer_height = float(height_mm if outer_height_mm is None else outer_height_mm)
        if not inner_radius_mm + 1e-9 < mid < outer_radius_mm - 1e-9:
            raise ValueError("the split radius must lie strictly between the ring radii")
        if outer_height <= 0:
            raise ValueError("outer ring height must be positive")
        if abs(outer_height - height_mm) < 1e-12:
            split = False  # equal heights are one collar, not two
    cleaned = _clean(points)
    tip_z = max(point[1] for point in cleaned) if tip_z_mm is None else float(tip_z_mm)
    top = tip_z + height_mm / 2.0 + shift_mm
    if split:
        top_outer = tip_z + float(outer_height_mm) / 2.0 + shift_mm
        # A vertical step of a few hundredths of a millimetre made the solver spend its entire
        # 2700 s budget on the thin feature. The same heights are carried by sloped tops: flat to
        # the split radius, then straight segments through every level out to the outer height.
        middle = None
        if middle_height_mm is not None and float(middle_height_mm) > 0:
            middle = [mid, tip_z + float(middle_height_mm) / 2.0 + shift_mm]
        if middle is not None and abs(middle[1] - top) > 1e-15:
            collar = [[inner_radius_mm, tip_z], [inner_radius_mm, top],
                      [mid, middle[1]], [outer_radius_mm, top_outer],
                      [outer_radius_mm, tip_z]]
        else:
            collar = [[inner_radius_mm, tip_z], [inner_radius_mm, top],
                      [mid, top], [outer_radius_mm, top_outer],
                      [outer_radius_mm, tip_z]]
    else:
        collar = [[inner_radius_mm, tip_z], [inner_radius_mm, top],
                  [outer_radius_mm, top], [outer_radius_mm, tip_z]]
    result: list[list[float]] = []
    inserted = False
    for index, point in enumerate(cleaned):
        x, z = point
        result.append([x, z])
        if inserted:
            continue
        nxt = cleaned[index + 1] if index + 1 < len(cleaned) else None
        if nxt is None:
            continue
        nx, nz = nxt
        # the tip face is the horizontal segment at the tip plane that crosses the ring
        if (abs(z - tip_z) < 1e-9 and abs(nz - tip_z) < 1e-9
                and min(x, nx) <= inner_radius_mm and max(x, nx) >= outer_radius_mm):
            result.extend(collar)
            inserted = True
    if not inserted:
        raise ValueError("the profile has no tip-face segment covering the ring radii")
    return _clean(result)

def mirror_profile(points: Sequence[Sequence[float]]) -> list[list[float]]:
    """Mirror a profile about the disc mid-plane (z -> -z), keeping the radii."""
    return _clean([[float(point[0]), -float(point[1])] for point in points])
