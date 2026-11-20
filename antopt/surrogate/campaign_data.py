"""Build a surrogate dataset from campaign summaries and recommend candidates.

Measured runs are the only source: a record is used when it is ``COMPLETED``
and carries numeric metrics. Recommendations are tagged ``predicted`` by the
caller; they must be verified with a real run before they are believed.
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence

import numpy as np

from ..analysis import measured_rows
from ..core import CaseSpec, sample_population
from .dataset import Dataset, Sample
from .models import GaussianProcessModel, cross_validate, model_is_usable
from .selection import select_candidates


def dataset_from_records(records: Iterable[dict[str, Any]], *,
                         target: str = "peak_gain") -> Dataset:
    dataset = Dataset()
    for row in measured_rows(records):
        if target not in row.metrics:
            continue
        dataset.add(Sample(
            candidate_id=row.candidate_id,
            params={name: float(value) for name, value in row.params.items()
                    if isinstance(value, (int, float))},
            targets={target: float(row.metrics[target])},
            fidelity="high",
            source="measured",
            run_id=row.run_id,
        ))
    return dataset


def fit_measured_model(dataset: Dataset, case: CaseSpec, *,
                       target: str = "peak_gain") -> dict[str, Any]:
    """Fit a GPR on measured data and report whether it is trustworthy."""
    param_names = [spec.name for spec in case.family.param_space]
    x, y = dataset.matrix(target, param_names)
    model = GaussianProcessModel()
    report = cross_validate(model, x, y, folds=min(4, len(y)))
    model.fit(x, y)
    usable = model_is_usable(report, float(y.max() - y.min()), max_relative_rmse=0.35)
    return {"model": model, "report": report, "usable": usable,
            "param_names": param_names, "samples": len(y),
            "target_range": [float(y.min()), float(y.max())]}


def recommend_next(case: CaseSpec, dataset: Dataset, *, target: str = "peak_gain",
                   count: int = 1, pool: int = 512, seed: int = 0,
                   strategy: str = "ei") -> list[dict[str, Any]]:
    """Pick candidates by acquisition on the measured surrogate."""
    fitted = fit_measured_model(dataset, case, target=target)
    model = fitted["model"]
    param_names = fitted["param_names"]
    space = case.family.param_space
    pool_params = sample_population(space, pool, seed=seed)
    pool_x = np.array([[float(params[name]) for name in param_names] for params in pool_params])
    mean, std = model.predict(pool_x)
    best = float(np.max(dataset.matrix(target, param_names)[1]))
    picks = select_candidates(mean, std, best=best, strategy=strategy, count=count)
    already = {tuple(sorted(sample.params.items())) for sample in dataset.samples()}
    chosen: list[dict[str, Any]] = []
    for pick in picks:
        params = dict(pool_params[pick.index])
        key = tuple(sorted(params.items()))
        if key in already:
            continue
        chosen.append({
            "params": params,
            "predicted": float(pick.mean),
            "uncertainty": float(pick.std),
            "acquisition": float(pick.score),
            "source": "predicted",
            "model_cv_rmse": fitted["report"].rmse,
            "model_usable": fitted["usable"],
        })
    return chosen


def grid_prediction(case: CaseSpec, dataset: Dataset, *, target: str = "peak_gain",
                    per_axis: int = 12) -> dict[str, Any]:
    """Predict the best reachable value on a coarse grid of the parameter space."""
    fitted = fit_measured_model(dataset, case, target=target)
    model = fitted["model"]
    space = case.family.param_space
    axes = []
    index_of = {spec.name: index for index, spec in enumerate(space)}
    for spec in space:
        if spec.kind == "choice":
            axes.append(np.array([spec.normalize(choice) for choice in spec.choices]))
        else:
            axes.append(np.linspace(0.0, 1.0, per_axis))
    grid = np.array(np.meshgrid(*axes)).reshape(len(axes), -1).T
    physical_grid = np.array([[float(space.from_vector(row)[name]) for name in fitted["param_names"]]
                             for row in grid])
    mean, std = model.predict(physical_grid)
    best = int(np.argmax(mean))
    params = space.from_vector(grid[best])
    return {
        "predicted_max": float(mean[best]),
        "uncertainty": float(std[best]),
        "params": {name: value for name, value in params.items()},
        "median": float(np.median(mean)),
        "sample_count": fitted["samples"],
        "cv_rmse": fitted["report"].rmse,
        "usable": fitted["usable"],
    }

