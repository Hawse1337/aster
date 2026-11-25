# Changelog

## 1.55.0 — 2026-11-26

- **Astra audit fixes (2026-11-23).** Repaired the standard workflow and campaign plumbing:
  plan bounds are compiled into an effective case; screening seeds initialize NSGA-II/III;
  real evaluation budgets are enforced; stage state is locked and bound to the plan; gate files
  are never mistaken for dicts; completion and electromagnetic acceptance are separated; and
  full-model evidence can be replayed from guard/worker ledgers with recipe and builder identity.
  Added `workflow_space.py`, `workflow_evidence.py`, and workflow regression tests.
  Offline suite: 909 passed, 2 skipped, 100 subtests.
- **Optional `GuardedSolveExecutor`.** `aster.solve_adapter` adds a generic build → guarded
  solve → export adapter for projects whose approved builder only builds/saves. It submits through
  the guard bridge, waits for a terminal run, copies S11/far-field artifacts, and records the guard
  campaign/run in `Evaluation.origin`; `CampaignRecord`/manifest rows now retain that origin so a
  selected full-model run can be replayed without losing its guard campaign path. See
  `docs/optimization_workflow.md`.
- **Formal evidence attach and long-solve polling fallback.** `workflow_evidence.attach_run` writes
  the real-run ledger/archive shape instead of requiring project-side hand-written
  `real_runs.jsonl`; `GuardedSolveConfig.run_params` carries `symmetry`/recipe identity into the
  guarded submit; and `GuardedSolveExecutor` treats present artifacts as completion when a long
  solve wedges the AEDT gRPC poll.
- **Solve-capable executor injection.** `WorkflowRunner` accepts an `executor_factory` and
  `run_approved_campaign(..., executor=...)` accepts a custom executor, so a project can use an
  approved builder plus `submit_guarded_run`/native export without forking the workflow. Also
  preserves nested `result.saved`, adds screening `front_size`/`evaluations`, and fixes optional
  `report_name` plumbing.
- Synced the latest algorithm working tree: conservative band/pattern acceptance,
  aperture field diagnostics, measured coarse/fine validation, paired multifidelity
  correction and the 10 GHz lens case update. Packaged GUI is intentionally excluded.
- **Standard optimisation workflow.** `aster.opt_workflow` adds a machine-readable
  `OptimizationPlan`, a baseline -> screening -> search -> acceptance -> delivery state machine and
  a `WorkflowRunner`. Screening uses LHS/DOE through the guard-approved builder; search wraps
  `optimizer.nsga2`/`nsga3`; each stage records artifacts in
  `optimization_workflow.json` and cannot be skipped. Acceptance/delivery are explicit evidence
  gates, not invented metrics. See `docs/optimization_workflow.md`.
- **Guard-approved optimiser adapter.** `aster.approved_builder` evaluates candidates through
  `run_approved_builder` (no arbitrary code), reads `result.s1p` / optional
  `farfield_metrics.json`, writes `optimization_manifest.json`, and applies a completion gate
  (`optimization_gate.py`). `InProcessBridge`/`StdioBridge` forward campaign/candidate provenance
  to the guard. `CampaignRunner` now accepts an injected executor for this path.

## 1.54.0 — 2026-11-20

**2026-11-21 — same-version asset rebuild.** The v1.54.0 wheel/sdist were rebuilt from main
after the bridge fix (aster now enables the guard's arbitrary-code opt-in only for its own broker
call and restores the environment; see `user_doc/safety.md`). Version/tag unchanged.

- Security integration: real AEDT runs are documented as guard-only (ansys-aedt-mcp v0.3.2+),
  with managed-session refusal, arbitrary-code default-off, run-token scope and audit.
- New docs page `user_doc/safety.md` (boundary, client fail-closed rule, residual risk), linked
  from the repository README and the docs site.
- README/docs landing refreshed with the three delivery forms and the runtime-safety section;
  release references updated to v1.54.0.
- No library API change in this release; offline suite unchanged.

## 1.53.0 — 2026-11-20

- Three-layer packaging: Python library/CLI, agent skill, docs site + GitHub releases.
- Skill engineering: canonical `skills/aster/` (SKILL.md + references + scripts), mirrored into the
  package so `aster skill install` works from a wheel.
- CLI: `aster doctor` (AEDT/guard/PyAEDT environment check) and `aster skill install|path|show`.
- Documentation site (MkDocs Material) deployed to GitHub Pages.
- Offline suite extended with skill-bundle consistency tests.

按阶段标签整理；详细说明见 `docs/status.md` 与 `docs/final_report.md`。

## 1.52.0 — 2026-11-20

- 项目由 `antopt` 正式更名为 **aster**（天线设计工作台）。
- 仓库：https://github.com/Hawse1337/aster （旧 `antopt` URL 自动跳转）。
- Python 导入、CLI（`python -m aster`、`aster`）、环境变量前缀（`ASTER_*`）与文档同步改名；
  发布资产为 `aster-1.52.0`。
- `1.51.0` 保留为改名前的历史版本。

## 1.51.0 — 2026-11-20

- 首次公开发布（当时名为 `antopt`）：设计侧库、离线测试、受控 AEDT 运行集成与 agent skill。

## 1.50.0 — 2026-11-18

- feat(astra): five structural levers measured for the theta=90 flat-donut pattern; verdict and evidence
- docs(evidence): A1c failed export; uniform height gradient saturates at a 125 deg cone
- docs(evidence): record A1b as a failed/partial attempt and its 28 GHz snapshot
- feat(astra): pattern target reference, donut metrics and first height-gradient measurement

## 1.49.0 — 2026-11-18

- feat(algorithm): recommendation, learning-contract and response-preview upgrade with completed verification

## 1.48.0 — 2026-11-18

- feat(astra): 24-32 GHz AstraBB reproduction campaign closes at 7.0 GHz with a 5.09 dBi gain variant
- docs(evidence): a strong pitch gradient splits the stopband and costs bandwidth
- docs(evidence): pitch 1.15 moves the gap to 30 GHz and lifts peak gain to 5.09 dBi
- docs(evidence): pitch 1.10 leaves only 31 GHz above -10 dB - 32 GHz now passes
- docs(evidence): the disc pitch moves the mismatch - denser rings put 32 GHz at -14 dB and break 30/31
- docs(evidence): candidate 26 disc gives the best gain but only half the bandwidth
- docs(evidence): the outer-exit scale does not fix 32 GHz
- docs(evidence): radial 1.30 matches 24-31 GHz deepest and exposes the 32 GHz conflict
- docs(evidence): radial 1.25 beats 1.20 - the 7 GHz point is the local optimum so far

## 1.47.2 — 2026-11-17

- chore(yadof): sync the integration notes and version references to upstream 0.8.3
- docs(evidence): shrinking the feed to radial 1.25 at bore 8 mm gives 7.0 GHz contiguous
- docs(evidence): bore 8 mm beats both 6.2 and 10 mm - 4 GHz band, -17.75 dB at 28 GHz, 4.17 dBi gain
- docs(evidence): opening the disc bore recovers 7 dB at 28 GHz and grows the band to 4 GHz
- docs(evidence): record the co-design attempts and why automation has not produced their numbers yet
- docs(evidence): the feed alone covers 24-32 GHz; the disc is what breaks it
- docs(evidence): one page that states the 24-32 GHz phase honestly

## 1.47.1 — 2026-11-17

- docs(report): point the artefact table at the rebuilt 1.47.1 distribution
- docs(evidence): the AstraBB feed responds to its scale parameters - Flare1.65 takes 28 GHz from -5.6 to -11.2 dB

## 1.47.0 — 2026-11-17

- docs(report): point the artefact table at the rebuilt 1.47.0 distribution
- release: 1.47.0 - the 24-32 GHz phase closes with the target family identified, not reproduced

## 1.46.6 — 2026-11-17

- docs(evidence): the second available integrated candidate is the same story - 3.0 GHz and 2.7 dBi
- docs(evidence): the user's integrated AstraBB template measures 3 GHz / 2.6 dBi, not the broadband target

## 1.46.5 — 2026-11-16

- docs(evidence): measure the user's real AstraBB template at 24-32 GHz and record what it does not prove

## 1.46.4 — 2026-11-16

- feat(structure): a three-level collar, and proof that the narrow band is not a meshing artefact

## 1.46.3 — 2026-11-16

- feat(wideband): the target band is 24-32 GHz now, so the case measures eight gigahertz

## 1.46.2 — 2026-11-16

- fix(structure): slope the dual-height collar, and the first point reaches 7.20 dBi directivity

## 1.46.1 — 2026-11-16

- feat(structure): two-step ground-ring collar so directivity and bandwidth can share one design

## 1.46.0 — 2026-11-16

- docs(report): point the artefact table at the rebuilt 1.46.0 distribution
- release: 1.46.0 - the seed search settled the structure question

## 1.45.4 — 2026-11-16

- feat(seed): refinement plan between the bandwidth seed and the directivity edge

## 1.45.3 — 2026-11-16

- test(seed): five measured seed designs say the bandwidth seed is real and sidelobes need a new structure

## 1.45.2 — 2026-11-16

- fix(measurement): give a slow design a real window to finish its sweep export
- feat(report): the DoE/seed summary now carries sidelobe level

## 1.45.1 — 2026-11-16

- feat(seed): the bandwidth seed is already in the measured data, and sll joins the objectives

## 1.45.0 — 2026-11-16

- release: 1.45.0 - first DoE measured, the AEDT message flood gone, artefacts rebuilt

## 1.44.13 — 2026-11-16

- docs(known-issues): cite the guard tags without pretending they are design releases
- fix(aedt-noise): stop the 'Solution is not a property name' flood at both sources

## 1.44.12 — 2026-11-16

- docs(handover): where each design improvement lives, and the five traps met on the way

## 1.44.11 — 2026-11-16

- feat(sweep): discrete verification points next to an interpolating sweep

## 1.44.10 — 2026-11-16

- feat(doe): turn a finished batch into decisions, not a pile of rows

## 1.44.9 — 2026-11-16

- test(sweep): A/B says the fine discrete grid buys nothing and the interpolating sweep is equivalent

## 1.44.8 — 2026-11-15

- test(phase-b): directivity measured on a real solve, efficiency has one numeric source

## 1.44.7 — 2026-11-15

- feat(robustness): plan tolerance variants and score the worst case honestly

## 1.44.6 — 2026-11-15

- feat(doe): a stratified baseline planner that knows what was already measured

## 1.44.5 — 2026-11-15

- feat(sweep): make the measurement sweep a choice instead of a hard-coded 1 GHz line

## 1.44.4 — 2026-11-15

- feat(directivity): the theta=90 plane becomes a measured objective and constraint

## 1.44.2 — 2026-11-15

- fix(launch): no console window, no eager AEDT, and a browser that says when it fails

## 1.44.1 — 2026-11-15

- fix(packaging): the installed application could not start, and the manual shortcut was wrong

## 1.44.0 — 2026-11-15

- release: 1.44.0 - Astra's findings closed out, yadof conventions adopted, artefacts rebuilt

## 1.43.9 — 2026-11-15

- test(efficiency): diagnostic says the solved project answers False for every query

## 1.43.8 — 2026-11-15

- feat(progress): separate heartbeat, resources, budgets and the verdict that must not kill

## 1.43.7 — 2026-11-15

- test(faults): an offline fault-injection set for the invariants we claim

## 1.43.6 — 2026-11-15

- test(efficiency): first controlled read-out - enumerable yes, retrievable not yet

## 1.43.5 — 2026-11-15

- fix(efficiency): the 'not obtainable' verdict was wrong - a category-wise probe finds it

## 1.43.4 — 2026-11-15

- feat(distributed): a coordinator with leased tasks, and agents that do the work locally

## 1.43.3 — 2026-11-15

- feat(gui): show normalised hypervolume in run status and in the recommendation's explanation

## 1.43.2 — 2026-11-15

- fix(lifecycle): isolate every attempt, kill only owned pids, and never retry an unconfirmed hang

## 1.43.1 — 2026-11-15

- feat(metrics): normalised hypervolume progress, adopting yadof's convention

## 1.43.0 — 2026-11-15

- Merge Astra's review: correctness fixes, far-field views and a documented review
- docs(handover): a brief for an external review

## 1.42.0 — 2026-11-15

- feat(ui): a Chinese/English switch for the page

## 1.41.1 — 2026-11-15

- feat(aedt): an opt-in non-graphical launch (-ng)

## 1.41.0 — 2026-11-15

- feat(worker): split the control plane from the execution plane

## 1.40.1 — 2026-11-15

- feat(acquisition): the hypervolume reference point now reaches every BoTorch path

## 1.40.0 — 2026-11-15

- feat(recommend): candidate-level failure risk as a hard constraint

## 1.39.1 — 2026-11-15

- chore: version 1.39.1
- docs: a checkable register of every problem found so far

## 1.39.0 — 2026-11-15

- test(evidence): record the real-machine cost check and finish the regression
- docs: manuals cover the new panels, and the report closes out 1.39.0
- build: 1.39.0 artefacts, rebuilt and verified
- feat: settle radiation efficiency and pin the page/API contract

## 1.38.1 — 2026-11-15

- build: a reproducible portable zip, and proof the queue shows a running job
- chore(release): 1.38.1 and a changelog that covers an untagged release
- chore(release): one version, checked against the tags, plus a generated changelog

## 1.38.0 — 2026-11-15

- feat(recommend): estimate the solve cost and respect a budget

## 1.37.0 — 2026-11-15

- fix(queue): persist a job when it starts and on every stage change
- chore: session log

## 1.36.0 — 2026-11-14

- test(evidence): measure the envelope-constrained proposal and record what it exposed

## 1.35.0 — 2026-11-14

- feat(recommend): fit only the dimensions that vary, with refined feasible edges

## 1.34.0 — 2026-11-14

- fix(recommend): search only where the campaign has measured

## 1.33.0 — 2026-11-14

- feat(stability): a solve budget, because every clever signal can go blind

## 1.32.0 — 2026-11-14

- feat(recommend): never propose a geometry the model rejects

## 1.31.0 — 2026-11-14

- fix(stability): stop killing healthy solves, and stop trusting a leftover AEDT

## 1.30.0 — 2026-11-14

- feat(gui): show run status and the version, and write the English manual

## 1.29.0 — 2026-11-14

- feat(preferences): narrow the search and say which objective matters

## 1.28.0 — 2026-11-14

- feat(gui): announce the metrics a design cannot produce

## 1.27.1 — 2026-11-14

- docs(status): record the housekeeping step

## 1.27.0 — 2026-11-14

- feat(housekeeping): dry-run retention, a real export, and a CLI

## 1.26.0 — 2026-11-14

- feat(stability): detect a stalled solve from the guard status, proven on a real failure

## 1.25.1 — 2026-11-14

- feat(observability): record how long each stage takes and how often runs succeed

## 1.25.0 — 2026-11-14

- feat(gui): recommend the next batch from what the campaign has measured

## 1.24.2 — 2026-11-14

- docs(benchmarks): the twenty-evaluation comparison, with paired statistics

## 1.24.1 — 2026-11-14

- docs(benchmarks): the five-arm table, and repair the mangled code spans

## 1.24.0 — 2026-11-14

- feat(surrogate): add qLogNParEGO, and follow the BoTorch tutorial on normalisation and sequential batches

## 1.23.3 — 2026-11-14

- docs: list the two distribution artefacts and their sizes
- docs(manual): name both distributions the build actually produces

## 1.23.1 — 2026-11-14

- （无提交记录）

## 1.23.2 — 2026-11-14

- docs+chore: rebuild the installer from the final build and record the software phase

## 1.23.0 — 2026-11-14

- feat(packaging): the packaged application runs a real solve

## 1.22.0 — 2026-11-14

- feat(packaging): an installer, verified by installing and uninstalling it
- chore: keep build outputs out of the repository

## 1.21.0 — 2026-11-14

- feat(packaging): a Windows build that the smoke test actually starts

## 1.20.1 — 2026-11-14

- docs(status): record phase D and the corrected acquisition comparison

## 1.20.0 — 2026-11-14

- feat(surrogate): one acquisition dispatcher, and the sign bug it exposed

## 1.19.0 — 2026-11-14

- feat(queue): persist jobs and batches, and resume what a restart left unfinished

## 1.18.1 — 2026-11-14

- docs(benchmarks): random still wins at twenty evaluations - the honest result

## 1.18.0 — 2026-11-14

- feat(stability): detect a stalled solve and explain every failure in the user's terms

## 1.17.1 — 2026-11-14

- feat(config): one configuration layer instead of paths baked into scripts

## 1.16.0 — 2026-11-14

- （无提交记录）

## 1.17.0 — 2026-11-14

- feat(surrogate): BoTorch qLogEHVI/qLogNEHVI batch acquisition, with the toolchain handled

## 1.15.0 — 2026-11-13

- docs: final report for the goal

## 1.14.0 — 2026-11-13

- test(cases): the efficiency kind works as a constraint, and the 8-point table lands in status

## 1.13.9 — 2026-11-13

- test(yadof): 2.40 - gain matches to 3.4e-5, the band flips between arms

## 1.13.8 — 2026-11-13

- test(yadof): ring 2.45 agrees across arms to 5.2e-5 dBi - seven full matches

## 1.13.7 — 2026-11-13

- test(batch): ring 2.45 completes on a retry - eight rings now

## 1.13.6 — 2026-11-13

- test(batch): ring 2.45 does not produce a band even with a ten-minute window

## 1.13.5 — 2026-11-13

- test(yadof): ring 2.40 - gain matches to 2.1e-5, bandwidth does not

## 1.13.4 — 2026-11-13

- test(batch): ring 2.40 measured on a second attempt, after a solve stall

## 1.13.3 — 2026-11-13

- test(yadof): ring 2.35 agrees across arms to 1.1e-5 dBi - six points now

## 1.13.2 — 2026-11-13

- test(yadof): ring 2.30 agrees across arms to 1.6e-5 dBi

## 1.13.1 — 2026-11-13

- test(batch): rings 2.30 and 2.35 measured on the batch arm

## 1.13.0 — 2026-11-13

- test(yadof): ring 2.25 agrees across arms to 1.5e-4 dBi

## 1.12.3 — 2026-11-13

- test(yadof): ring 2.15 now agrees across arms to 1.8e-5 dBi

## 1.12.2 — 2026-11-13

- test(interop): pin the once-per-session sweep configuration, and drop the dead helper

## 1.12.1 — 2026-11-13

- fix(interop): do not rebuild the sweep per candidate in the yadof session

## 1.12.0 — 2026-11-13

- fix(cases): give the sweep ten minutes to finish before the export is called short

## 1.11.2 — 2026-11-13

- docs: say what happened to the raw campaign artifacts

## 1.11.1 — 2026-11-13

- test(yadof): 2.15 fails on the yadof arm at a partial sweep, and that is recorded

## 1.11.0 — 2026-11-13

- test(yadof): two rings now measured on both arms

## 1.10.3 — 2026-11-13

- docs(status): the 2.15 row is a success, and the limit is the intermittent export

## 1.10.2 — 2026-11-13

- docs(conclusions): two more lessons - probability versus determinism, and where policy belongs

## 1.10.1 — 2026-11-13

- docs(benchmarks): ring 2.15 completes on a rerun - the failure was transient

## 1.10.0 — 2026-11-13

- docs(benchmarks): ring 2.15 completes on a rerun - the failure was transient

## 1.9.2 — 2026-11-13

- docs: map every GPT requirement to its final state

## 1.9.1 — 2026-11-13

- docs: final state - three rings measured, three limits named

## 1.9.0 — 2026-11-13

- docs(benchmarks): three rings measured end to end, one reproduced twice

## 1.8.4 — 2026-11-13

- （无提交记录）

## 1.8.5 — 2026-11-13

- fix(cases): put the touchstone retry in the case spec, not only in the publishers

## 1.8.3 — 2026-11-13

- feat(cases): ask for the touchstone retry in every spec publisher

## 1.8.2 — 2026-11-13

- docs: settle the radiation-efficiency item as a data-source limit

## 1.8.1 — 2026-11-13

- fix(cases): refuse the efficiency objective again, now with both reasons

## 1.8.0 — 2026-11-13

- feat(cases): efficiency asks the guard for a sphere grid instead of a missing quantity

## 1.7.0 — 2026-11-13

- feat(physics): directivity and efficiency from a full-sphere gain pattern

## 1.6.1 — 2026-11-13

- fix(cases): refuse the efficiency objective with the reason, and record the quantity list

## 1.6.0 — 2026-11-13

- test(evidence): the efficiency metric is blocked by AEDT, and here is the proof

## 1.5.0 — 2026-11-13

- fix(interop): let the yadof session survive a poisoned worker session

## 1.4.0 — 2026-11-13

- feat(cases): the efficiency objective now asks the guard for the directivity column

## 1.3.1 — 2026-11-13

- docs: handover - verified paths, open items and where the evidence lives

## 1.3.0 — 2026-11-13

- fix(interop): rebuild the band sweep when the candidate changes

## 1.2.1 — 2026-11-13

- docs(benchmarks): three paths, one number - and where the yadof path is still flaky

## 1.2.0 — 2026-11-13

- fix(watchdog): sample system-wide busy time, and verify the yadof session

## 1.1.1 — 2026-11-13

- feat(watchdog): detect a stalled solver instead of waiting out the timeout

## 1.1.0 — 2026-11-13

- docs: close out the goal with an evidence-linked summary and four lessons

## 1.0.2 — 2026-11-13

- test(evidence): the yadof arm reaches the solver and then stalls

## 1.0.1 — 2026-11-13

- fix(interop): name the far-field setup and surface the setup error in the yadof session

## 1.0.0 — 2026-11-13

- refactor(executor): one builder for the setup and band sweep, used by every path

## 0.99.2 — 2026-11-13

- fix(evidence): regenerate the P2 comparison record as valid JSON

## 0.99.1 — 2026-11-13

- docs(benchmarks): the GUI batch path and the standalone runner agree to four decimals

## 0.99.0 — 2026-11-13

- feat(gui): P3 is closed - two candidates in one session, both COMPLETED

## 0.98.2 — 2026-11-13

- fix(gui): do not reconfigure the setup from the worker thread

## 0.98.1 — 2026-11-13

- fix(gui): configure the project copy in the caller thread

## 0.98.0 — 2026-11-13

- fix(gui): one connected bridge for the whole manager

## 0.97.2 — 2026-11-13

- test(evidence): record the P3 block with the bridge/session finding

## 0.97.1 — 2026-11-13

- fix(gui): connect the bridge before touching the copy, and record the remaining block

## 0.97.0 — 2026-11-13

- fix(gui): retry the sweep creation and keep the clean payload clean

## 0.96.1 — 2026-11-13

- docs(status): record the P3 blocker per stage and what remains to trace

## 0.96.0 — 2026-11-13

- test(evidence): P3 is blocked inside the guard submit path, not in the design system

## 0.95.0 — 2026-11-13

- fix(apply): accept partial parameter dicts and refuse bad values without raising

## 0.94.0 — 2026-11-13

- fix(interop,gui): apply the solver settings, not just the sweep

## 0.93.0 — 2026-11-12

- feat(surrogate): kriging-believer batch acquisition (the q in qNEHVI, no BoTorch)

## 0.92.0 — 2026-11-12

- feat(interop): the yadof session publishes the spec and the band sweep

## 0.91.0 — 2026-11-12

- test(evidence): record why the real radiation-efficiency path is blocked

## 0.90.0 — 2026-11-12

- feat(interop): the yadof-versus-batch comparison harness

## 0.89.0 — 2026-11-12

- feat(gui): export the measured Pareto front from the page

## 0.88.0 — 2026-11-12

- fix(gui): create the discrete band sweep once per project copy

## 0.87.0 — 2026-11-12

- feat(lwa): three-parameter rib taper h_n = h0 + a1*u + a2*u^2

## 0.86.0 — 2026-11-12

- feat(interop): generate real yadof workspace files, verified against yadof 0.8.1

## 0.85.0 — 2026-11-12

- feat(interop): generate the yadof job workflow that runs through the guard

## 0.84.0 — 2026-11-12

- feat(cases): opt-in radiation-efficiency objective for the variable family
- docs: close the Pareto export and radiation-efficiency rows in the gap analysis

## 0.83.0 — 2026-11-12

- （无提交记录）

## 0.83.1 — 2026-11-12

- feat(module-a): Pareto front export and the radiation-efficiency metric

## 0.82.0 — 2026-11-12

- feat(optimizer): NSGA-III, plus the yadof interop bridge and gap analysis

## 0.81.2 — 2026-11-12

- docs(status): mark the GUI variable-mode solve as the one open gap

## 0.81.1 — 2026-11-12

- revert(gui): drop the sweep re-creation that broke every GUI run

## 0.81.0 — 2026-11-12

- fix(gui): recreate the discrete sweep before every real run

## 0.80.0 — 2026-11-12

- fix(gui): the launcher really accepts --case and --reuse-project now

## 0.79.1 — 2026-11-12

- docs(s3): finish the five-per-arm table with the last two results

## 0.79.0 — 2026-11-12

- docs(s3): the five-per-arm study is complete

## 0.78.0 — 2026-11-12

- docs(s3): three of the four expanded measurements are in

## 0.77.1 — 2026-11-12

- docs(status): record the incremental timings next to the S1 result

## 0.77.0 — 2026-11-12

- docs(s3): expanded study table, surrogate rate drops to 80%

## 0.76.0 — 2026-11-12

- test(gui): cover the variable case on both paths

## 0.75.0 — 2026-11-12

- feat(s3): merge the study results into a per-strategy comparison

## 0.74.0 — 2026-11-12

- feat(gui): offer the variable-driven case in the design console

## 0.73.0 — 2026-11-12

- docs: record the variable-driven rebuild in conclusions and README

## 0.72.0 — 2026-11-12

- feat(gui): batch panel, actionable error hints and case selection for real runs

## 0.71.0 — 2026-11-12

- feat(gui): batch fine-scan runs in one solver session

## 0.70.0 — 2026-11-12

- feat(model): incremental rebuilds meet the sub-two-second target

## 0.69.0 — 2026-11-12

- perf(model): one subtract per bowl instead of eighteen

## 0.68.0 — 2026-11-12

- feat(s1): thirty parameter updates in one project - the exit condition is met

## 0.67.0 — 2026-11-12

- feat(cases): measured hole arrays reproduce the reference band exactly

## 0.66.2 — 2026-11-12

- test(holes): the small holes cannot explain the 44 mm3 gap

## 0.66.1 — 2026-11-12

- test(calibration): collar and hole arrays lift the band to 2.0 GHz

## 0.66.0 — 2026-11-12

- feat(cases): ring collar on the bowl and the reference hole arrays

## 0.65.0 — 2026-11-12

- test(bisect): rebuilding the bowls alone destroys the band, the coax alone gets halfway

## 0.64.1 — 2026-11-12

- test(calibration): corrected profiles lift the gain to the reference, bandwidth still 0

## 0.64.0 — 2026-11-12

- fix(parametric): pick the profile occurrence that matches the reference body

## 0.63.0 — 2026-11-12

- test(diag): the profile rebuild produced shells, not solids

## 0.62.0 — 2026-11-12

- feat(cases): variable-driven lens28_var case and its first calibration

## 0.61.0 — 2026-11-12

- feat(parametric): extract the reference profiles and the parameter transforms

## 0.60.0 — 2026-11-12

- feat(parametric): revolve_profile op and parameterised bowl profile

## 0.59.1 — 2026-11-12

- docs(status): add the parsed bowl profile to the S1 section

## 0.59.0 — 2026-11-12

- docs(status): the S1 root cause is the modelling approach, not the tooling
- docs(status): pin the final design-system version (v0.58.2)

## 0.58.2 — 2026-11-12

- docs(readme): final progress summary with the S1 gap called out

## 0.58.0 — 2026-11-12

- （无提交记录）

## 0.58.1 — 2026-11-12

- docs: final S1-S7 and GUI status with the missing S1 exit condition

## 0.57.0 — 2026-11-12

- feat(benchmark): the surface ablation reads the trusted dataset directly

## 0.56.0 — 2026-11-12

- docs(s3): the six-candidate real-data study is complete

## 0.55.1 — 2026-11-12

- fix(model): stop emitting the cone backup/restore on the solve path

## 0.55.0 — 2026-11-12

- feat(gui): suggest the next unmeasured candidate from the fitted surface

## 0.54.0 — 2026-11-12

- feat(model): back up and restore the lower cone around the ring union

## 0.53.1 — 2026-11-12

- docs(s1): the incremental mode still fails because the first build unites

## 0.53.0 — 2026-11-12

- feat(s1): verifier gets an incremental mode that keeps the ring fixed

## 0.52.0 — 2026-11-12

- feat(gui): CSV export of the ledger, plus the conclusions and README examples

## 0.51.0 — 2026-11-12

- docs: generated operator catalog and the honest plugin-scope statement

## 0.50.0 — 2026-11-12

- feat(model): incremental updates skip the destructive ring union

## 0.49.0 — 2026-11-12

- feat(s3): plan and start the real-data first-feasible study

## 0.48.0 — 2026-11-12

- feat(s7): LWA runs through the guard and reports KPIs plus the measured beam angle

## 0.47.1 — 2026-11-12

- feat(lwa): add an S11-only submission path (blocked by the sphere preflight)

## 0.47.0 — 2026-11-12

- test(lwa): bisect shows the model solves without the far-field sphere

## 0.46.1 — 2026-11-12

- docs(s7): the guard was right - the LWA setup genuinely has no solution

## 0.46.0 — 2026-11-12

- feat(s7): submit the leaky-wave antenna through the guard

## 0.45.0 — 2026-11-12

- feat(s4): diagnosis-driven change verified with one real solve

## 0.44.1 — 2026-11-12

- test(model): pin the step-ring limitation after trying subtract

## 0.44.0 — 2026-11-12

- fix(gui): start every real run from a pristine project copy

## 0.43.1 — 2026-11-12

- feat(gui): one-click in-process launcher and README for the whole workflow

## 0.43.0 — 2026-11-12

- feat(gui): guard-ledger history, baseline and multi-candidate comparison

## 0.42.1 — 2026-11-12

- feat(gui): real mode verified end to end against live AEDT

## 0.42.0 — 2026-11-12

- feat(gui): real runs through the guard, S11 curves and a usage README

## 0.41.0 — 2026-11-12

- feat(gui): local design console plus a surface-feature bug fix

## 0.40.3 — 2026-11-12

- docs: record the final design-system version in the status table

## 0.40.2 — 2026-11-12

- docs: final S1-S7 status

## 0.40.1 — 2026-11-12

- docs(live): LWA has radiation and solves, but the solution keeps no radiated fields

## 0.40.0 — 2026-11-12

- feat(lwa): the leaky-wave model validates and solves in a real project

## 0.39.1 — 2026-11-12

- docs(live): single-domain LWA model builds all parts but validation still fails

## 0.39.0 — 2026-11-12

- docs: S1-S7 completion status

## 0.38.4 — 2026-11-12

- docs(live): LWA PEC walls, ports and setup succeed; overlapping vacuum bodies block validation

## 0.38.3 — 2026-11-12

- docs(live): LWA wave ports and setup now succeed; PEC walls are the gap

## 0.38.2 — 2026-11-12

- feat: auto pass budget in the candidate runner, corrected surface ablation

## 0.38.1 — 2026-11-11

- docs: trusted measurement conclusions table

## 0.38.0 — 2026-11-11

- feat(executor): pass budget follows the size of the geometry change

## 0.37.8 — 2026-11-11

- docs(live): thicker disc confirmed bad with a full 12-pass measurement

## 0.37.7 — 2026-11-11

- docs(discipline): export completeness and graded convergence rules

## 0.37.6 — 2026-11-11

- docs(live): thick-disc re-measurement needs 12 passes after the ring is added

## 0.37.5 — 2026-11-11

- docs(live): audit every measured run for sparse touchstone exports

## 0.37.4 — 2026-11-11

- docs(live): correct the 2.35 mm conclusion - it was a sparse export, not physics

## 0.37.3 — 2026-11-11

- docs(live): prediction vs measurement on the measured surface

## 0.37.2 — 2026-11-11

- feat(benchmark): exact RBF surface and the first real-shape ablation

## 0.37.1 — 2026-11-11

- fix(test): reference PARAMS must cover the unified parameter space

## 0.37.0 — 2026-11-11

- feat(benchmark): run search strategies on a surface fitted to measured data

## 0.36.2 — 2026-11-11

- docs(live): LWA solve blocked on PyAEDT 2026 R1 API differences

## 0.36.1 — 2026-11-11

- docs(live): the LWA geometry now builds in a real project

## 0.36.0 — 2026-11-11

- feat(parametric): make the LWA plugin actually buildable (S7)

## 0.35.1 — 2026-11-11

- docs(live): 0.05 mm fine scan confirms the 3 GHz ceiling

## 0.35.0 — 2026-11-11

- feat(controller): deterministic fine scan for spiky landscapes

## 0.34.2 — 2026-11-11

- docs(live): ring refinement and the first failed surrogate proposal

## 0.34.1 — 2026-11-11

- docs(live): ring 2.2 with tip 1.0 is the bandwidth optimum and now feasible

## 0.34.0 — 2026-11-11

- feat(case): goal becomes bandwidth-maximisation at gain >= 6 dBi

## 0.33.2 — 2026-11-11

- docs(live): feed steps destroy the passband regardless of size

## 0.33.1 — 2026-11-11

- feat(case): feed step parameters join the searchable space

## 0.33.0 — 2026-11-11

- feat(parametric): add_inner_sleeve operator with the dielectric-bore rule

## 0.32.4 — 2026-11-11

- docs(live): three-section gradient feed still gives no bandwidth

## 0.32.3 — 2026-11-11

- docs(live): feed-transition experiments and the dielectric-bore rule

## 0.32.2 — 2026-11-11

- docs(live): the topology tops out at ~3 GHz; full band needs a new mechanism

## 0.32.1 — 2026-11-11

- docs(live): the 2.2/0.6/0 ring is a sharp optimum; stepped rings fail

## 0.32.0 — 2026-11-11

- feat(parametric): conical coax-to-radial transition operator

## 0.31.2 — 2026-11-11

- docs(live): full-band requirement and the two-ring null result
- fix(case): register the new ring parameters in the model space

## 0.31.1 — 2026-11-11

- feat(case): optional second matching ring

## 0.31.0 — 2026-11-11

- feat(parametric): ring shift and multi-ring tags

## 0.30.1 — 2026-11-10

- docs(live): ring size scan triples the measured bandwidth

## 0.30.0 — 2026-11-10

- feat(case): ring geometry becomes part of the searchable family

## 0.29.1 — 2026-11-10

- docs(live): ground ring doubles the measured bandwidth (1.0 -> 2.0 GHz)

## 0.29.0 — 2026-11-10

- feat(parametric): add_step_ring structural operator
- docs(live): note the handed-project control run failing on a broker exit

## 0.28.1 — 2026-11-10

- docs(live): shrinking the disc aperture destroys match and gain

## 0.28.0 — 2026-11-10

- feat(case): radial disc scale as the structural bandwidth knob

## 0.27.2 — 2026-11-10

- docs(live): tip 0.9 band curve after the v1.15.11 export hardening

## 0.27.1 — 2026-11-10

- docs(live): first surrogate-proposal-then-measure loop on the reference family

## 0.27.0 — 2026-11-10

- feat(surrogate): fit on the measured reference family and propose a probe

## 0.26.1 — 2026-11-10

- docs(live): thicker-disc candidate and the 8-pass convergence requirement

## 0.26.0 — 2026-11-10

- feat(case): reference lens family with tip_offset and disc thickness

## 0.25.3 — 2026-11-10

- docs(live): measured tip_offset sensitivity on the reference design
- fix(scripts): tip-offset scan uses the discrete sweep recipe
- fix(scripts): band measurement uses a discrete linear-step sweep
- docs(benchmark): first-feasible column and why the KPI is not discriminable here

## 0.25.2 — 2026-11-10

- feat(benchmark): report first_feasible_at per strategy and seed

## 0.25.1 — 2026-11-10

- docs(benchmark): record the S2/S3 ablation table and the open KPI gap

## 0.25.0 — 2026-11-10

- fix(benchmark): surrogate loop ignored feasibility and re-measured points

## 0.24.1 — 2026-11-10

- docs(live): reference baseline and mismatch attribution

## 0.24.0 — 2026-11-10

- feat(case): tip_offset replaces bowl_gap, measured from the reference design

## 0.23.2 — 2026-11-10

- fix(case): anchor the lower bowl on the coax mouth when setting the gap

## 0.23.1 — 2026-11-10

- feat(case): bandwidth is a hard requirement for the corrected bowl family

## 0.23.0 — 2026-11-10

- feat(case): user-corrected bowl family with bowl_gap as a parameter

## 0.22.0 — 2026-11-10

- feat(S1,S5): structural consistency checks and an environment guard

## 0.21.2 — 2026-11-10

- docs: the 11 dBi operator result does not reproduce; operator is experimental

## 0.21.1 — 2026-11-10

- docs: family-baseline experiment and geometry probes

## 0.21.0 — 2026-11-10

- feat(S5): family baselines with lineage and content hash

## 0.20.1 — 2026-11-10

- feat(S5): live structure-operator verification script and evidence

## 0.20.0 — 2026-11-10

- feat(S5): aperture structure operator for lens28 + scale/shift modelling ops

## 0.19.0 — 2026-11-10

- feat(S3): surrogate on measured campaign data + reachability answer

## 0.18.0 — 2026-11-10

- docs: feasible region, structural limit and topology trigger

## 0.17.3 — 2026-11-10

- fix(case): allow the full step_L range (the 3.0 mm rule was arbitrary)

## 0.17.2 — 2026-11-10

- docs: measured sensitivities from four HFSS points + relax step-length rule

## 0.17.1 — 2026-11-10

- fix: impossible parameters are recorded as MODEL_FAILED instead of crashing

## 0.17.0 — 2026-11-10

- docs: first feasible candidate on real HFSS (step_L=3.0, s11 -12.8 dB, peak 6.57 dBi)

## 0.16.2 — 2026-11-10

- feat: campaign summary script (measured table and slopes)

## 0.16.1 — 2026-11-10

- feat(S4): measured-data analysis (tables and first-order slopes)

## 0.16.0 — 2026-11-10

- fix(case): gate the handover target on peak gain, not the horizontal cut
- docs: live-run discipline (single AEDT, no heavy tasks during a solve, cost baseline)

## 0.15.1 — 2026-11-10

- feat: resume a crashed guarded run through the bridge

## 0.15.0 — 2026-11-10

- feat(S6): experiment driver that executes controller decisions

## 0.14.1 — 2026-11-10

- docs: first full-pipeline campaign point completed on real HFSS

## 0.14.0 — 2026-11-10

- feat(S3,S6): learning-curve comparison and multi-fidelity surrogate

## 0.13.0 — 2026-11-10

- feat(S2): NSGA-II campaign executor and real-budget script

## 0.12.0 — 2026-11-10

- feat(S4): physics-guided local search loop over measured campaigns

## 0.11.1 — 2026-11-10

- fix: a failed or unmeasured run must not look feasible to the controller

## 0.11.0 — 2026-11-10

- feat: submit far-field setup and extra setups with each campaign run

## 0.10.1 — 2026-11-10

- feat(S4): sensitivities from real campaign runs

## 0.10.0 — 2026-11-10

- feat(S1-S6): campaign runner linking parameters, guarded runs, archive, decisions

## 0.9.1 — 2026-11-10

- docs: running log of live HFSS runs

## 0.9.0 — 2026-11-10

- docs(S1): record the first completed guarded run (closed loop proven)

## 0.8.1 — 2026-11-10

- feat: stale-lock clearing through the bridge + bridge delegation tests

## 0.8.0 — 2026-11-10

- feat(S6): surrogate-assisted baseline and multi-seed ablation

## 0.7.0 — 2026-11-10

- feat(S7): second antenna plugin (periodic leaky-wave) + system loop test

## 0.6.0 — 2026-11-10

- feat(S5,S6): structure operators/families/triggers + rule-based strategy controller

## 0.5.0 — 2026-11-10

- feat(S4): sensitivity matrix, coupling, constrained local step, diagnosis

## 0.4.0 — 2026-11-10

- feat(S3): surrogate dataset, GPR/RBF models and acquisition functions

## 0.3.1 — 2026-11-10

- chore: register optimizer and benchmark packages

## 0.3.0 — 2026-11-10

- feat(S2): NSGA-II with feasibility-first constraint handling + benchmark harness

## 0.2.0 — 2026-11-10

- feat(S1): apply candidate parameters through the guarded broker

## 0.1.1 — 2026-11-10

- chore: explicit package __init__ files for aster.cases and aster.parametric

## 0.1.0 — 2026-11-10

- feat: aster design system foundation (S0) + parametric layer (S1 start)
