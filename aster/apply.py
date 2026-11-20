"""Apply candidate parameters to the AEDT model through the guard bridge (S1).

This is the glue between ``OperationModel`` (declarative operations) and the
guarded broker (``run_python_code``). It never solves anything and never forces
a project: the snippet attaches to an existing project path.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .parametric.aedt import render
from .parametric.model import OperationModel


@dataclass
class ApplyResult:
    ok: bool
    validated: bool
    objects: list[str]
    operations: list[dict[str, Any]]
    stdout: str = ""
    stderr: str = ""
    error: str | None = None


class ModelApplier:
    """Applies one parameter set to a project and reports the geometry state."""

    def __init__(
        self,
        model: OperationModel,
        bridge: Any,
        *,
        project_path: str,
        design_name: str,
        pid: int | None = None,
        port: int | None = None,
        incremental: bool = True,
    ):
        if pid is None and port is None:
            raise ValueError("ModelApplier needs an explicit AEDT pid or port")
        self.model = model
        self.bridge = bridge
        self.project_path = project_path
        self.design_name = design_name
        self.pid = pid
        self.port = port
        self._applied_once = False
        # incremental updates are the fast path; some parameter transitions can
        # still reference an object a previous update removed, so callers that need
        # a guaranteed clean state can force a full re-apply
        self.incremental = incremental

    def apply(self, params: dict[str, Any]) -> ApplyResult:
        # callers legitimately pass a partial dict (the GUI sends only the fields the user
        # edited), so the model's defaults fill the rest before anything indexes a key
        merged = dict(self.model.space.default())
        merged.update(dict(params))
        params = merged
        try:
            self.model.space.validate(dict(params))
        except Exception as exc:  # noqa: BLE001 - a bad parameter is a failed apply, not a crash
            return ApplyResult(ok=False, validated=False, objects=[], operations=[],
                               error=str(exc)[:300])
        geometry = self.model.validate_geometry(dict(params))
        em = self.model.validate_em_setup(dict(params))
        if not (geometry.valid and em.valid):
            return ApplyResult(
                ok=False,
                validated=False,
                objects=[],
                operations=[],
                error="; ".join(geometry.messages + em.messages) or "invalid candidate",
            )
        if not self._applied_once or not self.incremental:
            operations = self.model.build(dict(params))
            self._applied_once = True
        else:
            operations = self.model.update(dict(params))
        if not operations:
            return ApplyResult(ok=True, validated=True, objects=[], operations=[])
        code = render(operations, project_path=self.project_path,
                      design_name=self.design_name, port=self.port)
        response = self.bridge.execute_code(code, pid=self.pid, port=self.port)
        payload = response.get("result") or {}
        stdout = str(response.get("stdout") or "")
        stderr = str(response.get("stderr") or "")
        if not isinstance(payload, dict):
            return ApplyResult(False, False, [], operations, stdout, stderr,
                               error=f"unexpected execute_code payload: {payload!r}"[:300])
        operations_log = payload.get("operations") or []
        validated = bool(payload.get("validated", False))
        return ApplyResult(
            ok=True,
            validated=validated,
            objects=[str(name) for name in (payload.get("objects") or [])],
            operations=operations_log if isinstance(operations_log, list) else operations,
            stdout=stdout,
            stderr=stderr,
        )

