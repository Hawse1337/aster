# API map

Small, task-first index. The package is `aster`; everything imports without AEDT unless noted.

## Spaces and goals

| need | API |
|---|---|
| one numeric variable | `aster.core.ParamSpec(name, unit, low, high, default)` |
| joint space | `aster.core.ParamSpace([...])` |
| a metric goal | `aster.core.Objective(id, kind, direction)` |
| feasibility rule | `aster.core.Constraint(...)` |
| case definition | `aster.core.CaseSpec(...)` |

Supported metric kinds: `peak_gain`, `realized_gain_h`, `directivity_db`, `directivity_horiz_db`,
`s11`, `s11_bandwidth`, `effective_bandwidth`, `ripple`, `sll`, `beam_theta`,
`radiation_efficiency`.

## Sampling and comparison

| need | API |
|---|---|
| Latin hypercube | `aster.core.latin_hypercube(space, count, seed=...)` |
| population sampling | `aster.core.sample_population(...)` |
| one evaluated candidate | `aster.optimize.Evaluation(candidate_id, family_id, params, metrics, feasible)` |
| dominance | `aster.optimize.dominates`, `fast_non_dominated_sort` |
| archive | `aster.optimize.pareto_archive(population, objectives)` |

## Campaigns and measured runs

| need | API |
|---|---|
| campaign recipe | `aster.campaign.Campaign` / `aster.campaigns` |
| measured run manager | `aster.runtime.runner.RealRunManager` |
| run/job archive | `aster.runtime.store.JobStore` |
| CLI campaign worker | `python -m aster.worker --help` |
| AEDT process helpers | `aster.aedt_host.running_aedt`, `start_aedt`, `aedt_reachable` |

## Acceptance

| need | API |
|---|---|
| S11 band from Touchstone | `aster.band_acceptance.assess_touchstone_band` |
| ring pattern metrics | `aster.pattern_acceptance.assess_ring_pattern` |
| combined run verdict | `aster.acceptance` |
| read a `.s1p` | `aster.touchstone.read_touchstone` |
| far-field views | `aster.farfield.view`, `aster.farfield.artifact_dir` |

## Surrogates

| need | API |
|---|---|
| sample set | `aster.surrogate.dataset` |
| GP model | `aster.surrogate.models` |
| multi-fidelity | `aster.surrogate.multifidelity.MultiFidelityModel` |
| acquisition | `aster.surrogate.selection` (optional `acquisition` extra) |

## Configuration

`aster.config.discover()` merges environment variables (`ASTER_*`), a config file and defaults;
`aster config show` is not a command, but `aster doctor` prints the resolved state and the missing
items.
