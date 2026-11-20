"""Render modelling operations into a guarded AEDT snippet (S1).

The snippet runs inside the guard's broker via ``run_python_code``: it never
starts a solve, it only edits the geometry of an *existing* project. All
operations are validated here, before any code is sent.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable

KNOWN_OPERATIONS = {
    "delete_if_present",
    "create_cylinder",
    "create_box",
    "create_tapered_units",
    "subtract",
    "set_wedge",
    "shift_cones",
    "shift_objects",
    "scale_objects",
    "scale_z_about_centre",
    "scale_xy_about_axis",
    "add_step_ring",
    "backup_object",
    "revolve_profile",
    "restore_object",
    "add_transition_cone",
    "add_inner_sleeve",
    "gap_bowls",
    "validate_simple",
}

CONE_OBJECTS = ["cone_up", "cone_dn"]


class OperationError(ValueError):
    """Raised when an operation is unknown or malformed."""


def validate_operations(operations: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    checked: list[dict[str, Any]] = []
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict) or "op" not in operation:
            raise OperationError(f"operation[{index}] must be an object with 'op'")
        name = operation["op"]
        if name not in KNOWN_OPERATIONS:
            raise OperationError(f"operation[{index}]: unknown op {name!r}")
        arguments = operation.get("args") or {}
        if not isinstance(arguments, dict):
            raise OperationError(f"operation[{index}].args must be an object")
        if name == "delete_if_present":
            names = arguments.get("names")
            if not isinstance(names, list) or not names:
                raise OperationError("delete_if_present needs a non-empty 'names' list")
        elif name == "create_box":
            sizes = arguments.get("sizes")
            origin = arguments.get("origin")
            if not isinstance(origin, list) or len(origin) != 3:
                raise OperationError("create_box origin must be [x, y, z]")
            if not isinstance(sizes, list) or len(sizes) != 3:
                raise OperationError("create_box sizes must be [dx, dy, dz]")
            if any(float(v) <= 0 for v in sizes):
                raise OperationError("create_box sizes must be positive")
            if not isinstance(arguments.get("name"), str) or not arguments["name"]:
                raise OperationError("create_box needs a name")
        elif name == "create_tapered_units":
            count = arguments.get("count")
            if not isinstance(count, int) or count < 1:
                raise OperationError("create_tapered_units count must be a positive int")
            for key in ("pitch", "h0", "width", "depth"):
                value = arguments.get(key)
                if not isinstance(value, (int, float)) or float(value) <= 0:
                    raise OperationError("create_tapered_units %s must be positive" % key)
            mode = arguments.get("mode", "relative")
            if mode not in ("relative", "additive"):
                raise OperationError("create_tapered_units mode must be relative or additive")
            for key in ("a1", "a2"):
                if key in arguments and not isinstance(arguments[key], (int, float)):
                    raise OperationError("create_tapered_units %s must be numeric" % key)
        elif name == "create_cylinder":
            for key in ("name", "origin", "radius", "height"):
                if key not in arguments:
                    raise OperationError(f"create_cylinder needs '{key}'")
            if float(arguments["radius"]) <= 0 or float(arguments["height"]) <= 0:
                raise OperationError("create_cylinder radius/height must be positive")
            if len(arguments["origin"]) != 3:
                raise OperationError("create_cylinder origin must be [x, y, z]")
        elif name == "subtract":
            if "target" not in arguments:
                raise OperationError("subtract needs 'target'")
            tools = arguments.get("tools") or arguments.get("tool")
            if not tools:
                raise OperationError("subtract needs 'tool' or 'tools'")
            if isinstance(tools, str):
                tools = [tools]
            if not isinstance(tools, list) or not all(isinstance(item, str) and item
                                                      for item in tools):
                raise OperationError("subtract tools must be object names")
            arguments = dict(arguments, tools=tools)
        elif name == "set_wedge":
            if not isinstance(arguments.get("present"), bool):
                raise OperationError("set_wedge needs a boolean 'present'")
        elif name == "shift_cones":
            if not isinstance(arguments.get("dz", 0), (int, float)):
                raise OperationError("shift_cones needs numeric 'dz'")
        elif name == "shift_objects":
            targets = arguments.get("targets")
            delta = arguments.get("delta")
            if not isinstance(targets, list) or not targets:
                raise OperationError("shift_objects needs a non-empty 'targets' list")
            if not isinstance(delta, list) or len(delta) != 3:
                raise OperationError("shift_objects delta must be [dx, dy, dz]")
        elif name == "scale_objects":
            targets = arguments.get("targets")
            factor = arguments.get("factor")
            if not isinstance(targets, list) or not targets:
                raise OperationError("scale_objects needs a non-empty 'targets' list")
            if not isinstance(factor, (int, float)) or float(factor) <= 0:
                raise OperationError("scale_objects factor must be positive")
        elif name == "scale_z_about_centre":
            target_name = arguments.get("name")
            factor = arguments.get("factor")
            if not isinstance(target_name, str) or not target_name:
                raise OperationError("scale_z_about_centre needs an object 'name'")
            if not isinstance(factor, (int, float)) or float(factor) <= 0:
                raise OperationError("scale_z_about_centre factor must be positive")
        elif name == "add_inner_sleeve":
            radius = arguments.get("radius")
            z0 = arguments.get("z0")
            length = arguments.get("length")
            if not isinstance(radius, (int, float)) or float(radius) < 0.45:
                raise OperationError(
                    "add_inner_sleeve radius must be >= 0.45 (the inner conductor is 0.4)")
            if not isinstance(length, (int, float)) or float(length) <= 0:
                raise OperationError("add_inner_sleeve length must be positive")
            if not isinstance(z0, (int, float)):
                raise OperationError("add_inner_sleeve z0 must be a number")
        elif name == "add_transition_cone":
            r_bottom = arguments.get("radius_bottom")
            r_top = arguments.get("radius_top")
            height = arguments.get("height")
            if not isinstance(r_bottom, (int, float)) or float(r_bottom) <= 0:
                raise OperationError("add_transition_cone radius_bottom must be positive")
            if not isinstance(r_top, (int, float)) or float(r_top) <= float(r_bottom):
                raise OperationError("add_transition_cone radius_top must exceed radius_bottom")
            if not isinstance(height, (int, float)) or float(height) <= 0:
                raise OperationError("add_transition_cone height must be positive")
        elif name == "add_step_ring":
            inner = arguments.get("inner_radius")
            outer = arguments.get("outer_radius")
            height = arguments.get("height")
            target = arguments.get("target", "cone_dn")
            if not isinstance(inner, (int, float)) or float(inner) <= 0:
                raise OperationError("add_step_ring inner_radius must be positive")
            if not isinstance(outer, (int, float)) or float(outer) <= float(inner):
                raise OperationError("add_step_ring outer_radius must exceed inner_radius")
            if not isinstance(height, (int, float)) or float(height) <= 0:
                raise OperationError("add_step_ring height must be positive")
            if not isinstance(target, str) or not target:
                raise OperationError("add_step_ring target must be an object name")
        elif name == "revolve_profile":
            points = arguments.get("points")
            if not isinstance(points, list) or len(points) < 3:
                raise OperationError("revolve_profile needs at least three points")
            for index, point in enumerate(points):
                if not isinstance(point, list) or len(point) != 2:
                    raise OperationError(
                        "revolve_profile points[%d] must be [x, z]" % index)
                if float(point[0]) < 0:
                    raise OperationError("revolve_profile radius must be >= 0")
            if not isinstance(arguments.get("name"), str) or not arguments["name"]:
                raise OperationError("revolve_profile needs a name")
            angle = arguments.get("angle", 360.0)
            if not 0 < float(angle) <= 360:
                raise OperationError("revolve_profile angle must be in (0, 360]")
        elif name in {"backup_object", "restore_object"}:
            target_name = arguments.get("name")
            if not isinstance(target_name, str) or not target_name:
                raise OperationError("%s needs an object 'name'" % name)
            suffix = arguments.get("suffix", "_bak")
            if not isinstance(suffix, str) or not suffix:
                raise OperationError("%s suffix must be a non-empty string" % name)
            arguments = {"name": target_name, "suffix": suffix}
        elif name == "scale_xy_about_axis":
            target_name = arguments.get("name")
            factor = arguments.get("factor")
            if not isinstance(target_name, str) or not target_name:
                raise OperationError("scale_xy_about_axis needs an object 'name'")
            if not isinstance(factor, (int, float)) or float(factor) <= 0:
                raise OperationError("scale_xy_about_axis factor must be positive")
        elif name == "gap_bowls":
            gap = arguments.get("tip_offset_mm", arguments.get("gap_mm"))
            if not isinstance(gap, (int, float)) or float(gap) <= 0:
                raise OperationError("gap_bowls needs a positive 'tip_offset_mm'")
            disc_offset = arguments.get("disc_offset_mm", 1.0)
            if not isinstance(disc_offset, (int, float)) or float(disc_offset) < 0:
                raise OperationError("disc_offset_mm must be >= 0")
        elif name == "validate_simple":
            if int(arguments.get("attempts", 1)) < 1:
                raise OperationError("validate_simple attempts must be >= 1")
        checked.append({"op": name, "args": arguments})
    return checked


@dataclass
class SnippetRenderer:
    """Render operations as Python executed in the guard broker."""

    project_path: str
    design_name: str
    port: int | None = None
    pid: int | None = None
    lines: list[str] = field(default_factory=list)

    def _literals(self, value: Any) -> str:
        return json.dumps(value, ensure_ascii=False)

    def render(self, operations: Iterable[dict[str, Any]]) -> str:
        checked = validate_operations(operations)
        body: list[str] = []
        body.append("_aster_ops = []")
        for operation in checked:
            name = operation["op"]
            args = operation["args"]
            if name == "delete_if_present":
                body.append(
                    "for _name in %s:\n"
                    "    if _name in app.modeler.object_names:\n"
                    "        app.modeler.delete(_name)" % self._literals(args["names"])
                )
            elif name == "create_box":
                body.append(
                    "app.modeler.create_box(origin=%s, sizes=%s, name=%r, material=%r)"
                    % (self._literals([float(v) for v in args["origin"]]),
                       self._literals([float(v) for v in args["sizes"]]),
                       args["name"], args.get("material", "pec"))
                )
            elif name == "create_tapered_units":
                prefix = str(args.get("prefix", "lwa_unit"))
                count = int(args["count"])
                pitch = float(args["pitch"])
                h0 = float(args["h0"])
                a1 = float(args.get("a1", 0.0))
                a2 = float(args.get("a2", 0.0))
                width = float(args["width"])
                depth = float(args["depth"])
                mode = str(args.get("mode", "relative"))
                if mode == "additive":
                    # h_n = h0 + a1*u + a2*u^2 with u in [0, 1] and heights in mm
                    formula = "    _h = %r + %r * _t + %r * _t * _t\n" % (h0, a1, a2)
                else:
                    # h_n = h0 * (1 + a1*u + a2*u^2): the polynomial is a relative taper
                    formula = "    _h = %r * (1.0 + %r * _t + %r * _t * _t)\n" % (h0, a1, a2)
                body.append(
                    "_aster_units = []\n"
                    "for _i in range(%d):\n"
                    "    _t = 0.0 if %d <= 1 else _i / float(%d - 1)\n"
                    % (count, count, count)
                    + formula
                    + "    _name = '%s_' + str(_i)\n" % prefix
                    + "    app.modeler.create_box(origin=[0, %r, _i * %r],\n" % (
                        float(args.get("base_y", 0.0)), pitch)
                    + "        sizes=[%r, _h, %r], name=_name, material='pec')\n" % (
                        width, depth)
                    + "    _aster_units.append(_name)"
                )
            elif name == "create_cylinder":
                body.append(
                    "app.modeler.create_cylinder(orientation=%s, origin=%s, radius=%s, "
                    "height=%s, name=%s, material=%s)"
                    % (
                        self._literals(args.get("axis", "Z")),
                        self._literals([float(v) for v in args["origin"]]),
                        float(args["radius"]),
                        float(args["height"]),
                        self._literals(args["name"]),
                        self._literals(args.get("material", "pec")),
                    )
                )
            elif name == "subtract":
                body.append(
                    "app.modeler.subtract(%s, %s, keep_originals=%s)"
                    % (
                        self._literals(args["target"]),
                        self._literals(args.get("tools") or args["tool"]),
                        bool(args.get("keep_originals", False)),
                    )
                )
            elif name == "set_wedge":
                if args["present"]:
                    body.append(
                        "if 'wedge' in app.modeler.object_names:\n"
                        "    try:\n"
                        "        app.modeler['wedge'].display_wireframe = False\n"
                        "    except Exception:\n"
                        "        pass"
                    )
                else:
                    body.append(
                        "if 'wedge' in app.modeler.object_names:\n"
                        "    app.modeler.delete('wedge')"
                    )
            elif name == "shift_cones":
                body.append(
                    "app.modeler.move(%s, [0, 0, %s])" % (self._literals(CONE_OBJECTS), float(args.get("dz", 0)))
                )
            elif name == "shift_objects":
                body.append(
                    "app.modeler.move(%s, %s)"
                    % (self._literals(args["targets"]),
                       self._literals([float(v) for v in args["delta"]]))
                )
            elif name == "scale_objects":
                body.append(
                    "app.modeler.scale(%s, %s, %s, %s)"
                    % (self._literals(args["targets"]),
                       float(args["factor"]), float(args["factor"]), float(args["factor"]))
                )
            elif name == "scale_z_about_centre":
                # thickness-like parameter: scale one object along Z about its own
                # centre so its position is preserved (modeler.scale uses the
                # global origin, which would also move the object)
                factor = float(args["factor"])
                body.append(
                    "_box = app.modeler[%r].bounding_box\n"
                    "_mid = (_box[2] + _box[5]) / 2.0\n"
                    "app.modeler.scale([%r], 1.0, 1.0, %r)\n"
                    "_box = app.modeler[%r].bounding_box\n"
                    "app.modeler.move([%r], [0, 0, _mid - (_box[2] + _box[5]) / 2.0])"
                    % (args["name"], args["name"], factor, args["name"], args["name"])
                )
            elif name == "scale_xy_about_axis":
                # radial parameter: scale one object in X/Y about the Z axis. The
                # object must be centred on that axis (validated by the caller),
                # so scaling about the global origin keeps its centre in place.
                factor = float(args["factor"])
                body.append(
                    "app.modeler.scale([%r], %r, %r, 1.0)"
                    % (args["name"], factor, factor)
                )
            elif name == "add_inner_sleeve":
                # impedance step on the inner conductor: the dielectric is a tube
                # whose bore matches the 0.4 mm conductor, so the bore has to be
                # widened first or HFSS rejects the overlapping PEC
                radius = float(args["radius"])
                z0 = float(args["z0"])
                length = float(args["length"])
                bore = str(args.get("tag", "aster")) + "_bore"
                sleeve = str(args.get("tag", "aster")) + "_sleeve"
                body.append(
                    "app.modeler.create_cylinder(orientation='Z', origin=[0, 0, %r],\n"
                    "    radius=%r, height=%r, name=%r, material='vacuum')\n"
                    "if %r in app.modeler.object_names:\n"
                    "    app.modeler.subtract('coax_diel', %r, keep_originals=False)\n"
                    "app.modeler.create_cylinder(orientation='Z', origin=[0, 0, %r],\n"
                    "    radius=%r, height=%r, name=%r, material='pec')\n"
                    "if %r in app.modeler.object_names:\n"
                    "    app.modeler.unite(['coax_inner', %r])"
                    % (z0, radius, length, bore, bore, bore,
                       z0, radius, length, sleeve, sleeve, sleeve)
                )
            elif name == "add_transition_cone":
                # broadband coax-to-radial transition: a flared cone from the coax
                # outer radius to the bowl tip radius, united into one conductor
                r_bottom = float(args["radius_bottom"])
                r_top = float(args["radius_top"])
                height = float(args["height"])
                shift = float(args.get("shift", 0.0))
                target_name = args.get("target", "cone_dn")
                tag = str(args.get("tag", "aster"))
                cone_name = tag + "_cone"
                body.append(
                    "_coax_end = app.modeler['coax_outer'].bounding_box[5]\n"
                    "_cone_z = _coax_end + %r\n"
                    "app.modeler.create_cone(orientation='Z', origin=[0, 0, _cone_z],\n"
                    "    radius_bottom=%r, radius_top=%r, height=%r, name=%r,\n"
                    "    material='pec')\n"
                    "if %r in app.modeler.object_names:\n"
                    "    app.modeler.unite(['coax_outer', %r, %r])"
                    % (shift, r_bottom, r_top, height, cone_name, cone_name, cone_name,
                       target_name)
                )
            elif name == "revolve_profile":
                # the bowl and its ring are one closed profile revolved around Z: the
                # object is created once and never united, so parameter changes are
                # point changes and repeated applies stay idempotent
                profile_points = [[float(point[0]), float(point[1])]
                                  for point in args["points"]]
                profile_name = str(args["name"])
                angle = float(args.get("angle", 360.0))
                material = str(args.get("material", "pec"))
                literal = ", ".join("[%r, %r]" % (point[0], point[1])
                                    for point in profile_points)
                body.append(
                    "_profile = [%s]\n" % literal
                    + "if %r in app.modeler.object_names:\n" % profile_name
                    + "    app.modeler.delete(%r)\n" % profile_name
                    + "_poly = app.modeler.create_polyline(\n"
                    + "    [[_p[0], 0.0, _p[1]] for _p in _profile],\n"
                    + "    cover_surface=False, close_surface=False,\n"
                    + "    name=%r, material=%r)\n" % (profile_name, material)
                    # HFSS builds the solid the same way the reference project does:
                    # cover the closed profile into a sheet, then sweep it around Z
                    + "app.modeler.cover_lines(_poly)\n"
                    + "app.modeler.sweep_around_axis(_poly, axis='Z',\n"
                    + "    sweep_angle=%r, number_of_segments=0)" % angle
                )
            elif name == "backup_object":
                # keep a spare copy of an object that a later operation destroys
                target_name = str(args["name"])
                suffix = str(args.get("suffix", "_bak"))
                body.append(
                    "_bak_name = %r + %r\n"
                    "if _bak_name not in app.modeler.object_names and %r in app.modeler.object_names:\n"
                    "    try:\n"
                    "        _copied = app.modeler.copy(assignment=[%r])\n"
                    "        if _copied:\n"
                    "            app.modeler[_copied[0]].name = _bak_name\n"
                    "    except Exception:\n"
                    "        pass"
                    % (target_name, suffix, target_name, target_name)
                )
            elif name == "restore_object":
                # put the destroyed object back so the next operation can address it
                target_name = str(args["name"])
                suffix = str(args.get("suffix", "_bak"))
                body.append(
                    "_bak_name = %r + %r\n"
                    "if %r not in app.modeler.object_names and _bak_name in app.modeler.object_names:\n"
                    "    try:\n"
                    "        _copied = app.modeler.copy(assignment=[_bak_name])\n"
                    "        if _copied:\n"
                    "            app.modeler[_copied[0]].name = %r\n"
                    "    except Exception:\n"
                    "        pass"
                    % (target_name, suffix, target_name, target_name)
                )
            elif name == "add_step_ring":
                # ground bridge between the coax mouth and the lower bowl: a PEC
                # annulus centred on the coax, united into the target so the two
                # parts form one conductor (edge contact alone is not a
                # connection in HFSS)
                inner = float(args["inner_radius"])
                outer = float(args["outer_radius"])
                height = float(args["height"])
                target_name = args.get("target", "cone_dn")
                shift = float(args.get("shift", 0.0))
                tag = str(args.get("tag", "aster"))
                out_name = tag + "_ring_out"
                in_name = tag + "_ring_in"
                z_expression = "_coax_end - %r + %r" % (height / 2.0, shift)
                body.append(
                    "_coax_end = app.modeler['coax_outer'].bounding_box[5]\n"
                    "_ring_z = %s\n"
                    "app.modeler.create_cylinder(orientation='Z', origin=[0, 0, _ring_z],\n"
                    "    radius=%r, height=%r, name=%r, material='pec')\n"
                    "app.modeler.create_cylinder(orientation='Z', origin=[0, 0, _ring_z],\n"
                    "    radius=%r, height=%r, name=%r, material='pec')\n"
                    "app.modeler.subtract(%r, %r, keep_originals=False)\n"
                    # NOTE (2026-11-12): uniting absorbs cone_dn, so a design can only
                    # be parameterised once; the GUI copies a pristine project per run
                    # and the in-place rework (recreate cone_dn before the unite) is
                    # tracked in docs/live_runs.md. Subtracting instead of uniting was
                    # tried and is not viable: on the second apply the notch already
                    # exists and AEDT rejects the empty Subtract.
                    "if %r in app.modeler.object_names:\n"
                    "    app.modeler.unite(['coax_outer', %r, %r])"
                    % (z_expression, outer, height, out_name, inner, height, in_name,
                       out_name, in_name, out_name, out_name, target_name)
                )
            elif name == "gap_bowls":
                # Anchored layout measured from the reference design (f24g05):
                #   disc mid-plane = coax mouth + disc_offset_mm (default 1.0)
                #   each bowl tip plane = disc mid-plane -/+ tip_offset_mm
                # so the disc straddles the coax mouth and both bowls sit
                # symmetrically around it, exactly like the working design.
                tip = float(args.get("tip_offset_mm", args.get("gap_mm", 1.0)))
                disc_offset = float(args.get("disc_offset_mm", 1.0))
                body.append(
                    "_coax_end = app.modeler['coax_outer'].bounding_box[5]\n"
                    "_disc_box = app.modeler['disc'].bounding_box\n"
                    "_disc_mid_target = _coax_end + %r\n"
                    "app.modeler.move('disc', [0, 0, _disc_mid_target - "
                    "(_disc_box[2] + _disc_box[5]) / 2.0])\n"
                    "_dn_box = app.modeler['cone_dn'].bounding_box\n"
                    "app.modeler.move('cone_dn', [0, 0, (_disc_mid_target - %r) "
                    "- _dn_box[5]])\n"
                    "_up_box = app.modeler['cone_up'].bounding_box\n"
                    "app.modeler.move('cone_up', [0, 0, (_disc_mid_target + %r) "
                    "- _up_box[2]])" % (disc_offset, tip, tip)
                )
            elif name == "validate_simple":
                body.append(
                    "import time as _aster_time\n"
                    "_aster_ok = False\n"
                    "for _attempt in range(%d):\n"
                    "    _aster_ok = bool(app.validate_simple())\n"
                    "    if _aster_ok:\n"
                    "        break\n"
                    "    _aster_time.sleep(%s)\n"
                    "_aster_ops.append({'validated': _aster_ok})"
                    % (int(args.get("attempts", 1)), float(args.get("delay_s", 2.0)))
                )
            body.append("_aster_ops.append(%s)" % self._literals({"op": name}))

        header = [
            "if '_aster_app' not in globals():",
            "    from ansys.aedt.core import Hfss as _AsterHfss",
            "    _aster_app = _AsterHfss(",
            "        project=%s, design=%s, new_desktop=False, close_on_exit=False,"
            " non_graphical=False%s" % (
                self._literals(self.project_path),
                self._literals(self.design_name),
                (", port=%d" % int(self.port)) if self.port else "",
            ),
            "    )",
            "app = _aster_app",
        ]
        footer = [
            "result = {'operations': _aster_ops,",
            "          'validated': all(op.get('validated', True) for op in _aster_ops),",
            "          'objects': sorted(app.modeler.object_names)}",
        ]
        self.lines = header + body + footer
        return "\n".join(self.lines) + "\n"


def render(operations: Iterable[dict[str, Any]], *, project_path: str, design_name: str,
           port: int | None = None) -> str:
    return SnippetRenderer(project_path=project_path, design_name=design_name,
                           port=port).render(operations)


def render_variable_declaration(
    variables: dict[str, str], *, project_path: str, design_name: str,
    port: int | None = None,
) -> str:
    """Declare HFSS design variables once (values are expressions with units)."""
    lines = [
        "if '_aster_app' not in globals():",
        "    from ansys.aedt.core import Hfss as _AsterHfss",
        "    _aster_app = _AsterHfss(project=%s, design=%s, new_desktop=False,"
        " close_on_exit=False, non_graphical=False%s)" % (
            json.dumps(project_path), json.dumps(design_name),
            (", port=%d" % int(port)) if port else "",
        ),
        "app = _aster_app",
    ]
    for name, expression in variables.items():
        lines.append("app[%s] = %s" % (json.dumps(name), json.dumps(expression)))
    lines.append("result = {'variables': {name: app[name] for name in %s}}"
                 % json.dumps(sorted(variables)))
    return "\n".join(lines) + "\n"


def render_variable_update(
    values: dict[str, str], *, project_path: str, design_name: str,
    port: int | None = None,
) -> str:
    """Update existing HFSS variables; geometry rebuilds without new objects."""
    lines = [
        "if '_aster_app' not in globals():",
        "    from ansys.aedt.core import Hfss as _AsterHfss",
        "    _aster_app = _AsterHfss(project=%s, design=%s, new_desktop=False,"
        " close_on_exit=False, non_graphical=False%s)" % (
            json.dumps(project_path), json.dumps(design_name),
            (", port=%d" % int(port)) if port else "",
        ),
        "app = _aster_app",
    ]
    for name, expression in values.items():
        lines.append("app[%s] = %s" % (json.dumps(name), json.dumps(expression)))
    lines.append("_aster_ok = bool(app.validate_simple())")
    lines.append("result = {'updated': %s, 'validated': _aster_ok,"
                 " 'variables': {name: app[name] for name in %s}}"
                 % (json.dumps(sorted(values)), json.dumps(sorted(values))))
    return "\n".join(lines) + "\n"

