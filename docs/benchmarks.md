# Benchmark evidence (synthetic acceptance before real HFSS)

## 2026-11-15 四张原生远场报告（未发布）

真实运行完成后保存并打开 AEDT 的 3D、E、H 和 E/H 叠加报告，软件显示原生 3D PNG 与实测切面。
按当前工程约定：E 为 φ=0°、θ=−180°…180°；H 为 θ=90°、φ=0°…360°。
频率读取本工程已保存的远场解，多频点同步切换，打开按钮跟随当前频率。
普通重开 .aedt 在本机不会恢复报告窗口；工程旁的“查看方向图.cmd”及软件按钮负责自动打开四图。
完整 765 项 Python 测试及 Node UI 行为测试通过。最终部署代码在已有解副本上成功保存四报告、导出四图，
读取 6643 点，并通过保存关闭后连续两次重开验证。此前 164 个 CSV 点对照误差 < 5e-14 dB。
未新增求解，旧安装包未重建。说明：`docs/handover/farfield_views.md`；证据：`docs/evidence/farfield_views.json`。

## 2026-11-15 交接评审修复（未发布）

修复 Touchstone 频率单位与连续带宽、未知 CPU 采样误判、连接异常后继续建模，
并调整失败解释。新增 15 项回归测试，完整 **728 项通过**；证据：
`docs/evidence/handover_review_tests.json`，详细评审：`docs/handover/review_2026-11-15.md`。
另以离线故障注入复现“第二次重试未启动却耗尽预算”，仍待恢复生命周期改造，
证据：`docs/evidence/handover_retry_probe.json`。
本轮未运行真机求解，未打发布 tag 或重建安装包；不能据此宣布长期仿真问题已解决。
辐射效率维持禁用，但“无可用量”的绝对结论需复核：安装版 PyAEDT 默认只返回首个非空
数量类别，旧证据未记录完整类别/solution/context。后续补充完整枚举再判断。


| Date | Artifact | Budget | Seeds |
|---|---|---|---|
| 2026-11-10 | `docs/evidence/ablation_synthetic_budget40.json` | 40 | 0–4 |
| 2026-11-11 | `docs/evidence/surface_ablation_budget24.json` | 24 | 0–2 |

## S2 on the surface fitted to the measured lens candidates

`scripts/run_surface_benchmark.py 24` fits an exact thin-plate RBF to the 14 trusted
HFSS measurements of the reference family and runs the four strategies on that fitted
landscape. Predictions outside the measured box are clipped to ±50% of the measured
range, because the interpolant is unconstrained there.

The surface uses the **five** parameters that actually vary across the measurements
(`tip_offset`, `disc_factor`, `ring_outer`, `ring_height`, `ring_shift`). An earlier
version omitted `disc_factor`, which collapsed the disc-thickness candidate onto the
ring-2.2 point in feature space and corrupted every number below; that is fixed and
the surface now reproduces the measured values exactly (ring 2.2 → 6.44 dBi,
ring 2.0 → 6.45 dBi, disc 1.1 → 4.92 dBi / 5.67 dB).

| strategy | feasible (mean of 3 seeds) | evaluations (mean) | hypervolume (mean) |
|---|---|---|---|
| random | 5.00 | 24.0 | 268.07 |
| lhs | 8.33 | 24.0 | 268.07 |
| **nsga2** | **15.33** | 23.0 | 268.07 |
| surrogate | 6.67 | 24.0 | 264.96 |

Reading: on this spiky landscape (feasible only along ring 2.20–2.40 mm at tip 1.0)
**NSGA-II finds three times more feasible candidates than random** and beats LHS and
the surrogate; all four reach essentially the same best hyper-volume, i.e. the
algorithms differ in *how often they land in the feasible band*, not in the best
point they eventually find. Earlier tables in this file were produced before the
`disc_factor` fix and are superseded; sparse-export runs were also discarded — see
`docs/evidence/export_audit.json`.

## S2/S3 ablation on the analytic problem

Measured with `scripts/run_ablation_synthetic.py 40` (`aster.benchmark.ablation.run_ablation`),
reference point `(0.0, -30.0)` for the 2-D hypervolume.

| strategy | feasible (mean of 5 seeds) | evaluations (mean) | hypervolume (mean) | first feasible at |
|---|---|---|---|---|
| random | 17.2 | 40.0 | 367.69 | 2.0 |
| lhs | 15.0 | 40.0 | 350.85 | 2.0 |
| nsga2 | 14.8 | 20.4 | 334.40 | 1.6 |
| surrogate (before fix) | 4.2 | 40.0 | 332.58 | — |
| **surrogate (v0.25.0)** | **16.6** | 40.0 | 333.09 | 1.8 |

`first_feasible_at` (v0.25.2) is the 1-based evaluation index of the first
feasible candidate; the ablation summary reports its mean and how many seeds
never found one. On this problem every strategy reaches a feasible design within
the first two evaluations (feasible fraction ≈ 40%), so the KPI "fewer
evaluations to the first feasible design" cannot be discriminated here.

### What the first run exposed

1. **The loop re-measured the same parameter vectors.** With no exclusion of
   already-measured points, the model RMSE stayed frozen at 0.836 across every
   iteration while the budget drained; the feasible count never moved past the
   LHS seed (3 of 40).
2. **The surrogate learned only the objective.** EI drove the search into
   high-gain but infeasible regions, so the assisted strategy was 4× worse than
   random sampling on feasibility.

### Fix (v0.25.0)

* the acquisition pool is filtered against every measured parameter vector and
  resampled when exhausted;
* the loop learns the normalised constraint violation as a second target: while
  no feasible region is predicted it proposes the lowest predicted violation
  (feasibility first), and once one is predicted it runs EI inside it.

Observed effect on seed 0: feasible 3 → 17 over the same 40 evaluations, with
model RMSE decreasing 0.836 → 0.335 over the run (evidence that the dataset now
grows and the model learns).

### Open KPI gap

The system-level KPI "first feasible candidate needs ≥30% fewer HFSS runs than
random sampling" is **not yet demonstrated**: on this problem the feasible
fraction is high (~40%), so the first feasible appears within the first handful
of evaluations for every strategy. It needs a problem with a small feasible
fraction (or real HFSS data) to be measured meaningfully; `StrategyResult` still
has to expose `first_feasible_at` for that comparison.

## S3 real-data study: first feasible design per strategy (running)

Design: the measured family holds 15 trusted points. Six **new** candidates are measured
so each strategy gets its own sequence, and the study reports how many HFSS runs it
needed before its first feasible design.

| strategy | candidates | how chosen |
|---|---|---|
| random | 3 | uniform inside the plausible box (tip 0.95–1.15, disc 0.90–1.05, ring 2.00–2.60, height 0.50–0.70, shift ±0.20), seed 20261112, duplicates removed |
| surrogate | 3 | best *unmeasured* ring diameters on the fitted surface (2.18, 2.32, 2.38 mm) |

Every candidate goes through the same guarded path; passes follow the measured rule
(8 for a ring/tip tweak, 12 when disc/height/shift changes).

Artifacts: `docs/evidence/s3_study_plan.json` (candidates + predictions),
`docs/evidence/s3_study_results.json` (appended after each measurement),
`docs/evidence/s3_study_summary.json` (produced by `scripts/s3_analyze.py`).

Status: running. The plan was simplified to ring-only candidates at eight passes,
because a 12-pass structural candidate took far too long; one surrogate point
(ring 2.18 mm) is already measured at 3.0 GHz / 6.328 dBi / 1.368 dB
(run 20261111T220413596033). The table is generated by scripts/s3_analyze.py
into docs/evidence/s3_study_table.md and pasted here once the remaining five finish.

## S3 real-data study: first feasible design per strategy (completed 2026-11-12)

Six new ring-only candidates were measured, three per strategy, so each strategy has its
own sequence. Tip, disc, ring height and shift stayed at their defaults; only the ring
diameter was chosen by the strategy. All runs used eight adaptive passes and the guarded
path.

| strategy | first feasible at | measured runs | result |
|---|---|---|---|
| surrogate | 1 | 3 | ring 2.18: feasible; ring 2.32: feasible; ring 2.38: feasible |
| random | 1 | 3 | ring 2.47: feasible; ring 3.42: infeasible; ring 4.15: infeasible |

Measured values (bandwidth / peak gain / ripple):

| strategy | ring (mm) | bandwidth | gain | ripple | feasible | run_id |
|---|---|---|---|---|---|---|
| surrogate | 2.18 | 3.0 | 6.328 | 1.368 | ✅ | `20261111T220413596033` |
| surrogate | 2.32 | 3.0 | 6.343 | 1.563 | ✅ | `20261111T231608080579` |
| surrogate | 2.38 | 3.0 | 6.123 | 1.895 | ✅ | `20261111T233335489520` |
| random | 2.47 | 3.0 | 6.403 | 1.508 | ✅ | `20261111T222115600122` |
| random | 3.42 | 2.0 | 6.475 | 1.676 | ❌ | `20261111T223822857593` |
| random | 4.15 | 2.0 | 6.227 | 1.352 | ❌ | `20261111T225630297929` |

### Honest reading

* **First-feasible count: tied at 1** — both strategies got a feasible design on their
  very first measurement, so this small sample does **not** show the surrogate reaching
  feasibility in fewer runs;
* **Feasibility rate: surrogate 3/3 vs random 1/3** — the surrogate never proposed an
  infeasible ring, while two of the three random draws (3.42 and 4.15 mm) landed outside
  the 2.20–2.40 mm band and measured 2.0 GHz;
* the sample is three per arm, so the rate difference is suggestive, not significant;
  what the data does show is that **the fitted surface keeps proposals inside the
  feasible band**, which is the behaviour the GUI's 推荐下一个 relies on.

The accumulated measured set is now **21 trusted points** (15 before the study plus the
six above), which is what the "≥20 points" requirement asked for.

## S2 surface ablation with the full trusted dataset (21 points, 2026-11-12)

`scripts/run_surface_benchmark.py 24` now reads the trusted dataset file instead of a
hard-coded list, so the comparison includes the six study measurements. Budget 24, seeds
0–2, reference point `(0.0, -30.0)`.

| strategy | feasible (mean of 3 seeds) | evaluations (mean) | hypervolume (mean) |
|---|---|---|---|
| nsga2 | **9.00** | 22.7 | **265.04** |
| surrogate | 4.33 | 24.0 | 167.36 |
| random | 3.33 | 24.0 | 262.48 |
| lhs | 3.00 | 24.0 | 251.53 |

Reading: with the wider dataset (ring diameters now include 3.42 and 4.15 mm, both
infeasible) **NSGA-II finds roughly three times more feasible candidates than random or
LHS and also has the best hyper-volume**; the surrogate's hyper-volume drops because its
proposals concentrate near the feasible band and the reference point penalises its narrower
spread. The earlier 14-point table is superseded by this one.

## S3 expanded study (five samples per arm, complete)

The original study measured three candidates per strategy; the arms are extended to five so the
feasibility rates are not decided by three draws.

| strategy | measurements | feasible | rate | first feasible at | rings (mm) |
|---|---|---|---|---|---|
| surrogate | 5 | 4 | **80%** | 1 | 2.18, 2.32, 2.38, 2.12, 2.28 |
| random | 5 | 1 | **20%** | 1 | 2.47, 3.42, 4.15, 4.47, 3.84 |

The four new measurements (all guarded, eight passes, ring-only):

| strategy | ring (mm) | bandwidth (GHz) | gain (dBi) | feasible |
|---|---|---|---|---|
| surrogate | 2.12 | 2.0 | 6.427 | no |
| surrogate | 2.28 | 3.0 | 6.408 | yes |
| random | 4.47 | 1.0 | 6.001 | no |
| random | 3.84 | 2.0 | 6.302 | no |

The extension changed the conclusion twice over: the surrogate's 3/3 became 4/5 (80%), and the
random arm's 1/3 became 1/5 (20%). Both arms still reach a feasible design on their first draw,
so the "first feasible at" metric does not separate them; the feasibility rate does, and it now
rests on five samples per arm rather than three.

Worth noting: every random draw outside the 2.20-2.45 mm band (3.42, 3.84, 4.15, 4.47 mm)
measured at most 2.0 GHz, while ring 2.47 happened to work - the feasible band is narrow and not
centred where a uniform sampler would look.

## NSGA-III 与 NSGA-II 的合成消融对比（2026-11-12）

同一个合成问题、同一预算（40 次评估）、5 个种子，新增的 NSGA-III 与既有策略同表对比
（docs/evidence/ablation_synthetic_budget40.json）：

| strategy | feasible_mean | evals_mean | hv_mean |
|---|---|---|---|
| random | 17.20 | 40.0 | 367.692 |
| lhs | 15.00 | 40.0 | 350.850 |
| nsga2 | 14.80 | 20.4 | 334.399 |
| **nsga3** | **13.40** | **19.6** | **346.692** |
| surrogate | 16.60 | 40.0 | 333.093 |

读法：NSGA-III 的**超体积高于 NSGA-II**（346.7 vs 334.4）且评估更少（19.6 vs 20.4），
说明参考方向机制确实把解摊得更开；但**可行解平均数量更低**（13.4 vs 14.8），
所以不能简单说三代全面优于二代——在可行域窄的问题上，二代对可行解的偏置更强。

备注：两代实现共用同一问题适配器、可行性优先约束与评估缓存，差异只来自拥挤距离
（NSGA-II）与参考方向（NSGA-III）。pymoo 的 niching 会随机打破并列，
同种子两次运行的评估数可能差 1–2 次，因此结论用多随机种子而不是单次复现。

## GUI 批量路径与独立脚本的一致性（2026-11-13，P3 收口）

同一参数（环 2.20 mm、8 passes、24–30 GHz 离散扫频）经两条路径实测：

| 路径 | 带宽 | 增益 | 波纹 | run |
|---|---|---|---|---|
| GUI 批量（RealRunManager，单会话） | 3.0 GHz | **5.88119 dBi** | **1.63072 dB** | atch-20261112T181456 |
| 独立脚本 
un_var_case.py | 3.0 GHz | **5.88123 dBi** | **1.63075 dB** | 20261112T145151110233 |

**四位小数吻合**，说明 GUI 路径（同会话多候选）与脚本路径测量的是同一件事。
同批第二个候选（环 2.25 mm）为 3.0 GHz / 6.0718 dBi / 1.7573 dB。

yadof 臂的 8 候选对拍**未交付**：其会话的求解设置调用被吞掉，候选以 E_NOT_CONVERGED 结束；
batch 臂在长脚本中遇到 broker 超时（同一代码在专门实验中已成功）。证据见
docs/evidence/p2_comparison.json 与 docs/evidence/p3_gui_batch.json。

## yadof 路径 vs GUI 批量 vs 独立脚本（2026-11-13）

同一参数（环 2.20 mm）经三条路径实测：

| 路径 | 带宽 | 增益 | run |
|---|---|---|---|
| yadof 会话（生成的工作区） | 3.0 GHz | **5.881228 dBi** | 20261112T212409485591 |
| 独立脚本 
un_var_case.py | 3.0 GHz | 5.881229 dBi | 20261112T145151110233 |
| GUI 批量 RealRunManager | 3.0 GHz | 5.881195 dBi | 
eal-20261112T181456-00 |

**三条路径相差 < 4e-5 dBi**。环 2.25 的 yadof 尝试以
E_SWEEP_EXPORT_INCOMPLETE（扫频只导出 1 行）失败，而同一参数的 GUI 批量路径成功
（3.0 GHz / 6.0718 dBi）——**yadof 路径可用但尚不稳定**。

时间量级：yadof 单候选约 1512 s；GUI 批量两个候选约 3720 s（不同批次，仅作参考）。
证据：docs/evidence/p2_comparison.json、docs/evidence/yadof_single.json。

## 多候选端到端测量（2026-11-13 收尾）

| 环径 | 路径 | 带宽 | 增益 | 波纹 | run |
|---|---|---|---|---|---|
| 2.05 | GUI 批量 | 2.0 GHz | 6.1915 dBi | 1.6307 dB |
| 2.30 | GUI 批量 | 3.0 GHz | 6.0546 dBi | 1.6230 dB |
| 2.35 | GUI 批量 | 3.0 GHz | 6.1087 dBi | 1.4981 dB |
| 2.40 | GUI 批量 | 2.0 GHz | 5.8803 dBi | 1.7291 dB |
| 2.45 | GUI 批量 | 3.0 GHz | 6.1678 dBi | 1.7103 dB | 前两次失败（扫频），重跑通过 | 20261113T060207321272 |
| 2.20 | GUI 批量 / 脚本 / yadof | 3.0 GHz | 5.8812 dBi | 1.6307 dB | 三条路径一致 |
| 2.25 | GUI 批量（两次） | 3.0 GHz | 6.0718 dBi | 1.7569 dB | 两次相差 <4e-5 |
| 2.15 | GUI 批量 | 3.0 GHz | 6.2598 dBi | 1.6529 dB | `20261113T072719-00`（首次失败为**偶发**，换 campaign 即成功） |

两条结论：

1. **可复现**：同一参数（2.25）两次独立批量运行，增益与波纹四位小数一致；
2. **2.15 的失败是偶发的**：换新 campaign 重跑同一环径即成功（3.0 GHz / 6.2598 dBi / 1.6529 dB）。
   说明"扫频只导出 1 行"是**间歇性导出竞态**，重试策略能救回大部分但不是全部——
   残余概率已记录，不再归因于"该几何解不出来"。

## 两臂同点对拍（yadof vs GUI 批量）

| 环径 | yadof 臂 | GUI 批量臂 | 差异 |
|---|---|---|---|
| 2.05 | 2.0 GHz / 6.19140 dBi | 2.0 GHz / 6.19154 dBi | 1.4e-4 dBi |
| 2.15 | 3.0 GHz / 6.25977 dBi | 3.0 GHz / 6.25979 dBi | 1.8e-5 dBi |
| 2.25 | 3.0 GHz / 6.07163 dBi | 3.0 GHz / 6.07182 dBi | 1.5e-4 dBi |
| 2.30 | 3.0 GHz / 6.05460 dBi | 3.0 GHz / 6.05461 dBi | 1.6e-5 dBi |
| 2.35 | 3.0 GHz / 6.10872 dBi | 3.0 GHz / 6.10871 dBi | 1.1e-5 dBi |
| 2.40 | 3.0 GHz / 5.88029 dBi | 2.0 GHz / 5.88026 dBi | 增益 3.4e-5；带宽 3.0 vs 2.0 |
| 2.45 | 3.0 GHz / 6.16775 dBi | 3.0 GHz / 6.16780 dBi | 5.2e-5 dBi |
| 2.20 | 3.0 GHz / 5.881228 dBi | 3.0 GHz / 5.881195 dBi | 3.3e-5 dBi |

八个环径两臂都测了：**七个完全一致**（增益差 ≤1.5e-4 dBi）；2.40 的增益一致到 3.4e-5，但带宽在 2.0/3.0 GHz 之间翻——该环径正好压在 −10 dB 边缘，26 GHz 附近一个扫频点决定结果，属于指标本身的不连续点。其中 2.05 那次 yadof 会话记录到
InsertFrequencySweep 的 setup_error 却仍然跑对——重连重试把一次本应失败的运行救了回来；
2.15 早先的失败（只导出 2 行）根因是**每个候选重建扫频**，去掉重建后即得到完整带宽。

**证据**：docs/evidence/batch_arm.json、docs/evidence/p2_comparison.json。

## 批量采集后端对拍（2026-11-14，五臂，已修复符号错误）

同一预算（初始 8 点 + 4 轮 × q=2 = **8 次新评估**）、**ZDT1**（五维、双目标）、6 个种子，
全部经 `aster.surrogate.backends.select_batch` 调度：

| 后端 | 最终超体积（均值 ± 标准差） | 相对随机 |
|---|---|---|
| **qLogNEHVI** | **4.076 ± 0.703** | **1.86×** |
| qLogEHVI | 4.007 ± 0.699 | 1.83× |
| Kriging believer | 3.765 ± 1.171 | 1.72× |
| qLogNParEGO | 3.590 ± 0.824 | 1.64× |
| 随机采样 | 2.195 ± 0.737 | 1.00× |
| （初始 8 点） | 1.835 | — |

**结论（8 次评估）**：超体积类（qLogNEHVI / qLogEHVI）最好且几乎等价（配对差 0.07，
远小于它们与 qLogNParEGO 的 0.49）；qLogNParEGO 明显弱于超体积类——它每次随机选一个
标量化方向，8 次评估摊不平这个方差。believer 次之，随机最差但方差最小。

### 更大预算（20 次新评估，5 个种子）

| 后端 | 最终超体积 | 相对随机 | 与 qLogNEHVI 的配对比较 |
|---|---|---|---|
| **qLogNEHVI** | **4.706 ± 0.794** | **1.78×** | — |
| qLogEHVI | 4.646 ± 0.740 | 1.76× | 差 0.060，t = 0.71（无显著差异） |
| Kriging believer | 4.456 ± 0.824 | 1.69× | 差 0.250，t = 4.87（显著） |
| qLogNParEGO | 4.421 ± 0.692 | 1.67× | 差 0.285，t = 4.20（显著） |
| 随机采样 | 2.643 ± 0.505 | 1.00× | 差 2.063，t = 10.55（显著） |
| （初始 8 点） | 2.027 | — | — |

**两个预算下的结论一致**：

1. 超体积类（qLogNEHVI / qLogEHVI）稳定第一，且两者**统计上不可区分**——所以默认用
   qLogNEHVI（噪声鲁棒，且在有噪声时更稳），无噪声场景可切 qLogEHVI，差别不计；
2. qLogNParEGO 在两种预算下都落后约 0.3 超体积（t > 4），原因是它每次只沿一个随机标量化方向
   优化；它的优势是**每次迭代便宜**，在目标数很多（>4）或需要极快单步时才有意义；
3. Kriging believer（纯 numpy，无 torch）与 qLogNParEGO 相当，说明"没有 torch 也能用"这条退路
   质量可接受，但它比超体积类差 0.25（t = 4.9）；
4. 随机采样在两个预算下都明显最差（1.7–1.9 倍差距），这否掉了我早先"随机最好"的错误结论。

数据：`docs/evidence/acq_5arms_8evals.json` 与 `docs/evidence/acq_5arms_20evals.json`。

### 按 BoTorch 官方教程修正的三处

读了 [BoTorch 多目标教程](https://botorch.org/docs/tutorials/multi_objective_bo) 之后改了实现：

1. **输入归一化**：教程在单位盒内拟合（`normalize(train_x, bounds)`）。我们原来直接拿原始量纲
   （环径 1.2–3.0 mm）拟合，长尺度会拖慢 GP 与 L-BFGS；现在统一归一化、优化后映射回原空间；
2. **`sequential=True`**：教程明确说 q>1 时应逐点贪心优化；原来没传，等于用了更慢的联合形式；
3. **`FastNondominatedPartitioning`**（qEHVI）+ `maxiter=200`：教程的配置。

教程同时确认了**符号约定**：它用 `BraninCurrin(negate=True)` 把最小化转成最大化，
所以"目标取负 + 参考点取负"就是正确做法，与上一轮修的一致。

### 更正：更早两次对拍的结论是错的

前两次比较得出"随机最好"，原因是我把符号搞反了：BoTorch 的超体积改进与 Kriging believer 的
EI 都是**最大化**形式，而本问题是最小化，两个代理臂因此在**朝最差角落爬**（ZDT1 的候选被钉在
x1 = 1.0）。修法是拟合前把目标取负、参考点一并取负。修正后结论反转：qLogNEHVI 约为随机采样的
**1.86 倍**超体积。

旧的 `docs/evidence/acquisition_comparison*.json`（12 种子 / 20 次评估两组）都是在修复前跑的，
保留只作"错误结论"的存档，不要引用其数值。

## 编译器与融合内核（同一台机器）

BoTorch 的 qEHVI 融合 C++ 内核需要 MSVC 环境（`vcvars64.bat`）与 `ninja`；按绝对路径启动解释器时
两者都不在 PATH 上，而且中文 locale 下读编译器输出会触发 `cp1` codec 报错。

- 本轮装了 **VS Build Tools 2022 + VCTools**；
- `aster/surrogate/botorch_acq.ensure_toolchain()` 自动注入 `vcvars` 环境、把 venv 的
  `ninja.exe` 放进 PATH，并把已构建的扩展目录加入 `sys.path`（避免重建时踩到那个 codec 问题）。

实测收益：**qLogNEHVI 1.19×、qLogEHVI 1.16×**（同一批次、多种子取最小值）——
不是官方宣称的 3×，因为小批量下时间主要花在 GP 拟合与采集优化上，而不是内核内层循环。

### 默认后端的设定

系统在 `auto` 下使用 **qLogNParEGO 之外的超体积后端**：有 torch 时选 **qLogNEHVI**，
没有 torch 时退回 **Kriging believer**。依据就是上面两轮对拍：

- qLogNEHVI 与 qLogEHVI 在两种预算下都统计上不可区分（配对 t = 0.71），但 qLogNEHVI 对噪声更稳，
  所以选它；
- qLogNParEGO 两种预算下都落后约 0.3 超体积（t > 4），只在"目标很多、单步必须极快"时才有意义；
- believer 比超体积类差约 0.25（t = 4.9），但它是没有 torch 时的可用退路；
- 随机采样 1.8 倍差距，是最低基线。

### 参考点对采集行为的影响（ZDT1，`docs/evidence/reference_point.json`）

同一批 12 个随机初始点、同一随机种子、q=3，仅改参考点（以实测最差值为基准，向外扩展 `k` 个跨度）：

| 参考点 | 平均 f1 | 平均到理想点距离 | 说明 |
|---|---|---|---|
| 紧（+0.02 跨度） | 0.270 | 0.856 | 集中在膝盖附近改善 |
| 默认（+0.05 跨度） | 0.272 | 0.857 | 与紧参考点几乎一致 |
| 宽（+4.0 跨度） | **0.287** | **0.981** | 明显向两端铺开 |

结论：**参考点确实改变覆盖方向，但需要足够宽才有可见差异**——0.6 个跨度实测与默认无差别，
4 个跨度才出现差异。这解释了为什么"参考点"这个旋钮在小幅调整时常显得没用：
它是相对量，不是绝对量。默认仍按数据自动推导（+5% 跨度）。

### 归一化超体积（借鉴 yadof 的约定，`aster/metrics.py`）

yadof 的成本视图用**固定归一化参考点 `(1, ..., 1)`** 画超体积带：上沿是截至当前所有代的累计
超体积，下沿是当前一代自身的超体积（见其 `user_doc/config_and_run.md` 的 "History and tools"）。
我们采用同一约定，让"进度停滞"在曲线上可见：

- 每条目标按**案例自己的目标值**归一化（带宽 3.0 GHz、增益 6.0 dBi 为理想值，0 为最差）；
  比目标更好时允许取负值（截断在 -1），**避免"达标的瞬间曲线饱和"**；
- 参考点固定为 `(1, 1)`，二维精确扫描求超体积；
- 逐次实测给出 `cumulative`（累计）与 `generation`（该次自身贡献）。

用现有实测数据重算（`docs/evidence/hypervolume_progress.json`，不新增求解）：

| 次序 | 累计归一化超体积 | 该次贡献 |
|---|---|---|
| `prior-205`（2.0 GHz / 6.19 dBi） | 0.688 | — |
| `real-…102409`（3.0 GHz / 6.11 dBi） | **1.043** | +0.355 |
| 之后的实测 | 1.043 | 0 |

**这里必须诚实**：第二条之后曲线完全平——不是因为算法收敛，而是这批实测里没有更好的点
（带宽已顶到 3 GHz，增益没有再升）。它同时也说明：**用"以实测极值做归一化"的朴素做法会被一个
-5.4 dBi 的坏点拉爆**（所有点瞬间饱和到 1.0），所以归一化必须来自任务目标而不是测量范围。

## 种子/细化批次（v1.46.0）

7 个真机设计的实测（完整表见 `docs/evidence/design_tradeoff.json`）：

| 设计 | 带宽 GHz | 增益 dBi | θ=90° 方向性 dBi | 纹波 dB | SLL dBc |
|---|---|---|---|---|---|
| ro2.30/h0.60/df0.90 | 3.0 | 6.37 | 6.80 | 1.43 | −2.95 |
| ro2.30/h0.50/df0.95 | 2.0 | 6.40 | 7.11 | 1.30 | −2.82 |
| ro2.30/h0.40/df1.00 | 2.0 | 6.29 | 7.09 | 1.58 | −2.00 |

结论：方向性与 3 GHz 带宽在现有几何互斥；旁瓣为栅瓣级。结构改版建议见
`docs/decisions/structure_change.md`。

## 24–32 GHz 阶段（v1.47.0）

| 对象 | 带宽 | 峰值增益 | θ=90° 方向性 | 主瓣/纹波 |
|---|---|---|---|---|
| 结构 A 最好点（24–30 内） | 3.0 | 6.43 | 7.09 | θ=90°/1.40 |
| R01_Lens30 集成模板 | 3.0 | 2.64 | 1.38 | θ=42°/6.62 |
| AstraBB_LensScreen | 3.0 | 2.71 | 1.13 | θ=42°/6.78 |

目标（截图参考天线）：24–35 GHz 匹配、带内峰值实现增益 5–8 dBi、θ=90° 面内 φ 纹波 ~2–3 dB。

## AstraBB 24–32 GHz 实测（2026-11-17/18）

| 配置 | 连续带宽 | 28 GHz | 32 GHz | 峰值增益 | 单次求解 |
|---|---|---|---|---|---|
| bore 8 + radial 1.30 + pitch 1.0 | 7.0 GHz (24–31) | −26.6 dB | −7.05 dB | 4.18 dBi | 1320 s |
| bore 8 + radial 1.30 + pitch 1.15 | 5.0 GHz (24–29) | −14.8 dB | −12.93 dB | 5.09 dBi | 1100 s |
| bore 8 + radial 1.30 + pitch 1.10 | 6.0 GHz (24–30) | −15.9 dB | −10.29 dB | 3.83 dBi | 1280 s |

每点均为 12 步自适应、MaxDeltaS 0.02、24–32 GHz 九点扫描；构建+测量总计约 30 分钟/变体。

## 甜甜圈方向图变体（2026-11-18，真实 AEDT）

| 变体 | 主瓣 θ | 峰值 dBi | horizon dBi | 带宽 GHz | SOLVING s |
|---|---|---|---|---|---|
| 基线 | 40° | 4.49 | 1.95 | 7.0 | 1320 |
| A1 环高渐变 0.8 | 126° | 6.48 | −10.97 | 2.0 | 1340 |
| B1 馈源偏移 −1.0 | 138° | 4.51 | 1.13 | 7.0 | 1410 |
| B2 馈源偏移 −0.5 | 138° | 4.29 | 1.46 | 7.0 | 1330 |
| C1 加厚盘体 | 140° | 5.71 | 0.04 | 2.0 | 1090 |
| D1 实心边缘环 | 34° | 4.72 | 1.41 | 7.0 | 1250 |
| E3 径向平行板 | 142° | 5.36 | −10.64 | 2.0 | 1350 |
