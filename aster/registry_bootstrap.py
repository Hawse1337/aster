"""Register the built-in design cases without importing the web GUI server."""
from __future__ import annotations


def ensure_registry() -> None:
    """Make sure the built-in cases are registered (idempotent)."""
    from .parametric.registry import _REGISTRY  # local import: internal table
    from .cases import lens28_ref, lens28_var, lens2g4_10g
    from .parametric.registry import register_case

    if "lens28_ref" not in _REGISTRY:
        register_case(lens28_ref.register())
    if "lens28_var" not in _REGISTRY:
        register_case(lens28_var.register())
    if "lens2g4_10g" not in _REGISTRY:
        register_case(lens2g4_10g.register())
