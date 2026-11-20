"""BoTorch qLogNEHVI batch acquisition (optional dependency).

The rest of the package needs no torch: this module is the only place that imports it, and it
does so lazily so a checkout without torch keeps working. ``available()`` answers whether the
stack can be used; callers fall back to :func:`aster.surrogate.batch.kriging_believer` when it
cannot.

qLogNEHVI is the noisy expected-hypervolume improvement of a batch of ``q`` points, which is the
acquisition the multi-objective literature uses for batch design. It needs a fitted GP per
objective, a reference point and the set of observed designs (the baseline).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sysconfig
from pathlib import Path
from typing import Any, Sequence

import numpy as np

__all__ = ["available", "qlognehvi_batch", "hypervolume", "describe", "ensure_toolchain",
           "METHOD"]

METHOD = "qlognehvi"

_TOOLCHAIN: dict[str, str] | None = None
_TOOLCHAIN_CHECKED = False

VSWHERE = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe")


def _vcvars_candidates() -> list[Path]:
    """Every vcvars64.bat this machine has, newest Visual Studio first."""
    found: list[Path] = []
    if VSWHERE.is_file():
        try:
            out = subprocess.run([str(VSWHERE), "-latest", "-products", "*",
                                  "-property", "installationPath"],
                                 capture_output=True, text=True, timeout=60)
            for line in (out.stdout or "").splitlines():
                line = line.strip()
                if line:
                    found.append(Path(line) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat")
        except Exception:  # noqa: BLE001 - fall through to the fixed locations
            pass
    found.append(Path(r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC"
                      r"\Auxiliary\Build\vcvars64.bat"))
    found.append(Path(r"C:\Program Files\Microsoft Visual Studio\2022\Community\VC"
                      r"\Auxiliary\Build\vcvars64.bat"))
    return [path for path in found if path.is_file()]


def ensure_toolchain() -> dict[str, str] | None:
    """Make ``cl`` and ``ninja`` reachable so BoTorch can build its fused kernels.

    BoTorch loads a small C++ kernel for the log-space hypervolume improvements and falls back to
    pure Python (about three times slower) when it cannot build it. Two things are needed on
    Windows and neither is inherited when the interpreter is started by absolute path: the MSVC
    environment from ``vcvars64.bat`` and the venv's ``Scripts`` directory (which holds
    ``ninja.exe``). This activates both in-process and returns the variables it injected.
    """
    global _TOOLCHAIN, _TOOLCHAIN_CHECKED
    if _TOOLCHAIN_CHECKED:
        return _TOOLCHAIN
    _TOOLCHAIN_CHECKED = True
    if shutil.which("cl") and shutil.which("ninja"):
        _TOOLCHAIN = {}
        return _TOOLCHAIN

    injected: dict[str, str] = {}
    scripts = sysconfig.get_path("scripts")
    if scripts and shutil.which("ninja") is None:
        injected["PATH"] = scripts + os.pathsep + os.environ.get("PATH", "")

    if not shutil.which("cl"):
        for candidate in _vcvars_candidates():
            try:
                out = subprocess.run(
                    'cmd /c ""%s" >nul 2>&1 && set"' % candidate,
                    capture_output=True, text=True, timeout=180, shell=True,
                    encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001 - a missing toolchain is not an error
                continue
            for line in (out.stdout or "").splitlines():
                key, sep, value = line.partition("=")
                if sep and key and not key.startswith("="):
                    injected.setdefault(key, value)
            if injected.get("Path") or injected.get("PATH"):
                break

    if injected:
        for key, value in injected.items():
            if key.upper() == "PATH":
                os.environ["PATH"] = value
            elif key.upper() == "INCLUDE" and "INCLUDE" in os.environ:
                os.environ["INCLUDE"] = value + ";" + os.environ["INCLUDE"]
            else:
                os.environ[key] = value
        _TOOLCHAIN = injected
    else:
        _TOOLCHAIN = None
    _adopt_built_extensions()
    return _TOOLCHAIN


def _adopt_built_extensions() -> list[str]:
    """Put already-built torch extensions on ``sys.path``.

    BoTorch first tries ``import logei_fused_ext`` and only compiles when that fails. A build that
    succeeded earlier (under a shell that had the compiler environment) is sitting in torch's
    extension cache, and importing it from there skips the rebuild entirely - which matters here
    because a rebuild from inside this interpreter trips over the OEM code page while reading the
    compiler's Chinese output.
    """
    cache = Path(os.environ.get("LOCALAPPDATA", "")) / "torch_extensions"
    added: list[str] = []
    if not cache.is_dir():
        return added
    for directory in cache.rglob("*"):
        if directory.is_dir() and any(directory.glob("*.pyd")):
            path = str(directory)
            if path not in sys_path():
                add_sys_path(path)
                added.append(path)
    return added


def sys_path() -> list[str]:
    import sys  # noqa: PLC0415

    return sys.path


def add_sys_path(path: str) -> None:
    import sys  # noqa: PLC0415

    sys.path.insert(0, path)


def available() -> bool:
    """True when torch and BoTorch can both be imported."""
    try:
        import botorch  # noqa: F401, PLC0415
        import torch  # noqa: F401, PLC0415
    except Exception:  # noqa: BLE001 - any import failure means "not available"
        return False
    return True


def _unit_box(bounds: Sequence[tuple[float, float]]) -> tuple[np.ndarray, np.ndarray]:
    """Lower and upper corner of the search box."""
    low = np.array([pair[0] for pair in bounds], dtype=float)
    high = np.array([pair[1] for pair in bounds], dtype=float)
    return low, high


def _normalise(x: np.ndarray, low: np.ndarray, high: np.ndarray) -> np.ndarray:
    span = np.where(np.abs(high - low) < 1e-12, 1.0, high - low)
    return (np.asarray(x, dtype=float) - low) / span


def _denormalise(x: np.ndarray, low: np.ndarray, high: np.ndarray) -> np.ndarray:
    return low + np.asarray(x, dtype=float) * (high - low)


def _reference_point(y: np.ndarray, margin: float = 0.05) -> np.ndarray:
    """Slightly worse than the observed worst value in every direction."""
    worst = np.max(y, axis=0)
    span = np.maximum(np.ptp(y, axis=0), 1e-6)
    return worst + margin * span


def _hypervolume_2d(values: np.ndarray, ref: np.ndarray) -> float:
    """Exact 2-D hypervolume for minimisation, by sweeping the sorted points."""
    order = np.argsort(values[:, 0])
    area = 0.0
    previous_y = float(ref[1])
    for index in order:
        first, second = float(values[index, 0]), float(values[index, 1])
        if second >= previous_y:
            continue  # dominated or level with the step: contributes no area
        area += (float(ref[0]) - first) * (previous_y - second)
        previous_y = second
    return max(0.0, area)


def hypervolume(y: np.ndarray, reference: np.ndarray | None = None) -> float:
    """Hypervolume of a minimisation set against ``reference`` (exact for two objectives).

    BoTorch's ``Hypervolume`` works in maximisation form, and negating both arguments to reuse it
    is the kind of sign convention that is easy to get silently wrong - it returned zero for a
    hand-checkable example. So the two-objective case is computed here directly and only larger
    dimensions defer to BoTorch.
    """
    values = np.asarray(y, dtype=float)
    if values.ndim != 2:
        raise ValueError("y must be 2-D")
    ref = _reference_point(values) if reference is None else np.asarray(reference, dtype=float)
    # points outside the reference box add no volume; a batch often contains some, and refusing
    # the whole set for them made the comparison harness unusable
    values = values[np.all(values <= ref, axis=1)]
    if len(values) == 0:
        return 0.0
    if values.shape[1] == 2:
        return _hypervolume_2d(values, ref)
    if not available():
        raise RuntimeError("hypervolume above two objectives needs torch/botorch")
    import torch  # noqa: PLC0415
    from botorch.utils.multi_objective.hypervolume import Hypervolume  # noqa: PLC0415

    return float(Hypervolume(-torch.as_tensor(ref, dtype=torch.double)).compute(
        -torch.as_tensor(values, dtype=torch.double)))


def _fit_gp(train_x, targets, *, noiseless=False, maxiter=60, timeout_s=15.0, diagnostics=None):
    """Condition on data and fit hyperparameters under a cooperative SciPy time budget."""
    import time
    import warnings
    import torch
    from botorch.fit import fit_gpytorch_mll
    from botorch.models import SingleTaskGP
    from botorch.models.model_list_gp_regression import ModelListGP
    from botorch.models.transforms.outcome import Standardize
    from gpytorch.mlls import ExactMarginalLogLikelihood, SumMarginalLogLikelihood

    if maxiter < 1 or timeout_s <= 0:
        raise ValueError("GP fitting budget must be positive")
    models = [SingleTaskGP(train_x, targets[:, i:i + 1],
                           train_Yvar=torch.full_like(targets[:, i:i + 1], 1e-8) if noiseless else None,
                           outcome_transform=Standardize(m=1)) for i in range(targets.shape[1])]
    model = models[0] if len(models) == 1 else ModelListGP(*models)
    mll = (ExactMarginalLogLikelihood(model.likelihood, model) if len(models) == 1
           else SumMarginalLogLikelihood(model.likelihood, model))
    started = time.monotonic()
    try:
        with warnings.catch_warnings(record=True) as caught:
            fit_gpytorch_mll(mll, max_attempts=1,
                            optimizer_kwargs={"options": {"maxiter": int(maxiter)},
                                              "timeout_sec": float(timeout_s) / len(models)})
        if not all(torch.all(torch.isfinite(parameter)) for parameter in model.parameters()):
            raise RuntimeError("fitted GP has nonfinite parameters")
    except Exception as exc:
        if diagnostics is not None:
            diagnostics["fit"] = {"status": "failed", "reason": str(exc)[:240]}
        raise RuntimeError("GP hyperparameter fitting failed: " + str(exc)) from exc
    model.eval()
    if diagnostics is not None:
        diagnostics["fit"] = {"status": "fitted", "models": len(models),
                              "maxiter": int(maxiter), "budget_seconds": float(timeout_s),
                              "seconds": time.monotonic() - started,
                              "warnings": [str(item.message)[:200] for item in caught]}
    return model


def _validate_inputs(x_obs, y_obs, bounds, q, constraint_obs, pending_X):
    x, y = np.asarray(x_obs, float), np.asarray(y_obs, float)
    if x.ndim != 2 or y.ndim != 2 or len(x) != len(y) or len(x) < 2 or not x.shape[1] or not y.shape[1]:
        raise ValueError("requires X[N,D], y[N,M] with at least two observations")
    if len(bounds) != x.shape[1] or q < 1:
        raise ValueError("bounds dimension or batch size is invalid")
    low, high = _unit_box(bounds)
    if not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)) or not np.all(np.isfinite([low, high])) or np.any(high <= low):
        raise ValueError("observations and nondegenerate bounds must be finite")
    g = np.empty((len(x), 0)) if constraint_obs is None else np.asarray(constraint_obs, float)
    if g.ndim == 1:
        g = g[:, None]
    if g.ndim != 2 or len(g) != len(x) or not np.all(np.isfinite(g)):
        raise ValueError("constraint_obs must be finite g[N,K] with g<=0 feasible")
    pending = np.empty((0, x.shape[1])) if pending_X is None else np.asarray(pending_X, float)
    if pending.ndim != 2 or pending.shape[1] != x.shape[1] or not np.all(np.isfinite(pending)):
        raise ValueError("pending_X must be finite X[P,D] in physical units")
    return x, y, low, high, g, pending


def _validate_reference(reference_point, objective_count):
    """A fixed target may exclude observed points from its hypervolume box."""
    if reference_point is None:
        return None
    reference = np.asarray(reference_point, float)
    if reference.shape != (objective_count,) or not np.all(np.isfinite(reference)):
        raise ValueError("reference_point must be finite with one value per objective")
    return reference


def _botorch_batch(x_obs, y_obs, bounds, *, q, seed, num_restarts, raw_samples,
                   noiseless=False, reference_point=None, weights=None,
                   constraint_obs=None, pending_X=None, fit_maxiter=60,
                   fit_timeout_s=15.0, diagnostics=None, parego=False):
    if not available():
        raise RuntimeError("torch/botorch are not installed; use diverse_ei")
    import torch
    from botorch.optim import optimize_acqf
    from botorch.sampling.normal import SobolQMCNormalSampler
    from botorch.acquisition.objective import GenericMCObjective
    from botorch.acquisition.multi_objective.objective import IdentityMCMultiOutputObjective

    info = diagnostics if diagnostics is not None else {}
    x, y, low, high, g, pending = _validate_inputs(x_obs, y_obs, bounds, q, constraint_obs, pending_X)
    supplied_reference = _validate_reference(reference_point, y.shape[1])
    torch.manual_seed(int(seed))
    ensure_toolchain()
    tensor = lambda value: torch.as_tensor(value, dtype=torch.double)
    train_x = tensor(_normalise(x, low, high))
    pending_t = tensor(_normalise(pending, low, high)) if len(pending) else None
    feasible = np.all(g <= 0, axis=1)
    scale = np.maximum(np.max(np.abs(g), axis=0), 1e-8) if g.shape[1] else np.empty(0)
    info.update({"constraint_count": g.shape[1], "feasible_observations": int(feasible.sum()),
                 "pending_count": len(pending), "noise": "fixed_numerical_floor" if noiseless else "learned_homoscedastic"})
    sampler = SobolQMCNormalSampler(sample_shape=torch.Size([128]), seed=int(seed))
    if not feasible.any():
        # Maximise negative expected minimum positive violation. No infeasible
        # objective observation is admitted to a hypervolume baseline.
        from botorch.acquisition.monte_carlo import qSimpleRegret
        model = _fit_gp(train_x, tensor(g / scale), noiseless=noiseless,
                        maxiter=fit_maxiter, timeout_s=fit_timeout_s, diagnostics=info)
        objective = GenericMCObjective(lambda samples, X=None: -samples.clamp_min(0).sum(dim=-1))
        acquisition = qSimpleRegret(model, sampler=sampler, objective=objective, X_pending=pending_t)
        info["stage"] = "feasibility_first"
    else:
        train_y = tensor(np.column_stack([-y, g / scale]))
        model = _fit_gp(train_x, train_y, noiseless=noiseless,
                        maxiter=fit_maxiter, timeout_s=fit_timeout_s, diagnostics=info)
        n_obj = y.shape[1]
        objective = IdentityMCMultiOutputObjective(outcomes=list(range(n_obj)))
        constraints = [lambda samples, i=i: samples[..., n_obj + i] for i in range(g.shape[1])] or None
        common = {"model": model, "sampler": sampler, "objective": objective,
                  "constraints": constraints, "X_pending": pending_t}
        if parego:
            from botorch.acquisition.multi_objective.parego import qLogNParEGO
            factor = None if weights is None else tensor(weights)
            if factor is not None:
                if factor.shape != (n_obj,) or not torch.isfinite(factor).all() or (factor < 0).any() or factor.sum() <= 0:
                    raise ValueError("weights must match objectives and be nonnegative")
                factor = factor / factor.sum()
            acquisition = qLogNParEGO(X_baseline=train_x[feasible],
                                      scalarization_weights=factor, **common)
            info["preference"] = "user_chebyshev_weights" if factor is not None else "random_chebyshev"
        else:
            from botorch.acquisition.multi_objective.logei import (
                qLogExpectedHypervolumeImprovement, qLogNoisyExpectedHypervolumeImprovement)
            if weights is not None and not np.allclose(weights, np.asarray(weights)[0]):
                raise ValueError("hypervolume acquisition does not implement preference weights; use qlognparego")
            reference_values = _reference_point(y[feasible]) if supplied_reference is None else supplied_reference
            info["reference_point_minimised"] = reference_values.tolist()
            info["reference_dominated_observations"] = int(np.all(y[feasible] < reference_values, axis=1).sum())
            reference = tensor(-reference_values)
            if noiseless:
                from botorch.utils.multi_objective.box_decompositions.non_dominated import FastNondominatedPartitioning
                partitioning = FastNondominatedPartitioning(ref_point=reference, Y=train_y[feasible, :n_obj])
                acquisition = qLogExpectedHypervolumeImprovement(
                    ref_point=reference.tolist(), partitioning=partitioning, **common)
            else:
                acquisition = qLogNoisyExpectedHypervolumeImprovement(
                    ref_point=reference.tolist(), X_baseline=train_x[feasible],
                    prune_baseline=True, **common)
            info["preference"] = "unweighted_pareto_hypervolume"
        info["stage"] = "objective"
    box = tensor(np.vstack([np.zeros(len(bounds)), np.ones(len(bounds))]))
    candidates, _ = optimize_acqf(acq_function=acquisition, bounds=box, q=int(q),
                                  num_restarts=int(num_restarts), raw_samples=int(raw_samples),
                                  options={"batch_limit": 5, "maxiter": 100},
                                  sequential=True)
    return _denormalise(candidates.detach().cpu().numpy(), low, high)


def qlognehvi_batch(x_obs, y_obs, bounds, q=2, seed=0, num_restarts=4, raw_samples=64,
                    noiseless=False, reference_point=None, *, constraint_obs=None,
                    pending_X=None, fit_maxiter=60, fit_timeout_s=15.0, diagnostics=None):
    """Constrained qLog(E/NE)HVI; inputs and pending designs share physical units."""
    return _botorch_batch(x_obs, y_obs, bounds, q=q, seed=seed,
                          num_restarts=num_restarts, raw_samples=raw_samples,
                          noiseless=noiseless, reference_point=reference_point,
                          constraint_obs=constraint_obs, pending_X=pending_X,
                          fit_maxiter=fit_maxiter, fit_timeout_s=fit_timeout_s,
                          diagnostics=diagnostics)


def qlognparego_batch(x_obs, y_obs, bounds, q=2, seed=0, num_restarts=4, raw_samples=64,
                       *, weights=None, constraint_obs=None, pending_X=None,
                       fit_maxiter=60, fit_timeout_s=15.0, diagnostics=None):
    """Constrained Chebyshev scalarisation; supplied weights are genuine preferences."""
    return _botorch_batch(x_obs, y_obs, bounds, q=q, seed=seed,
                          num_restarts=num_restarts, raw_samples=raw_samples,
                          parego=True, weights=weights, constraint_obs=constraint_obs,
                          pending_X=pending_X, fit_maxiter=fit_maxiter,
                          fit_timeout_s=fit_timeout_s, diagnostics=diagnostics)


def describe() -> dict[str, Any]:
    """Small report for docs and the comparison script."""
    info: dict[str, Any] = {"method": METHOD, "available": available()}
    if info["available"]:
        import botorch  # noqa: PLC0415
        import torch  # noqa: PLC0415

        info["torch"] = torch.__version__
        info["botorch"] = botorch.__version__
    return info
