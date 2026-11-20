# 电磁设计改进记录（2026-11-15/16）

本文件记录"由快到慢"那一轮改进的落地位置、证据与未验证边界，供后来者接手。
配套：`docs/status.md` 第 33–38 步、`docs/final_report.md` §10.6–10.8、`docs/decisions/efficiency.md`。

## 1. 方向性指标（θ=90°，目标 ≥7 dBi）

| 层 | 位置 |
|---|---|
| 指标计算 | `aedt_guard/pattern.py`：`directivity_horizontal_from_grid()`（同一积分给出 η、峰值方向性、水平面峰值）；`directivity_from_grid()` 保留 |
| 评估输出 | `aedt_guard/evaluator.py`：`directivity_horiz_db`（跨频最差）；`pattern.read_sphere_grid` 兼容**扁平表**（Freq, Phi, Theta, 一行一对）与矩阵布局 |
| 案例声明 | `antopt/cases/lens28_var.py`：`evaluation.include_directivity=True`、硬约束 θ=90° 方向性 ≥7 dBi、主瓣 θ∈[85°,95°]、目标 `dir_obj` |
| 页面 | `antopt/gui/static/index.html`：运行状态显示「θ=90° 方向性」「主瓣 θ」 |
| 证据 | `docs/evidence/phase_b_validation.json`、`docs/evidence/efficiency_query_diagnostic.json`、`docs/evidence/quantities_full.json` |

真机结论（默认设计）：θ=90° 方向性 **6.747 dBi**、主瓣 **90.0°**、峰值 6.803 dBi、η≈1.01（PEC 透镜），
默认设计**未达** 7 dBi / 3 GHz 目标，约束系统正确判为不可行。

## 2. 辐射效率（仍待独立来源）

- 旧结论"不可得"已被推翻（按类别枚举 93 个 Antenna Parameters 量）；
- guard 现可由球面网格积分给出 η（`radiation_efficiency`），但这是**单一来源**；
- Antenna Parameters 直取受 PyAEDT 1.9.0 附着限制（GUI AEDT 报版本格式错误）不可用；
- 指标保留 `pending_metrics`（接入验证中），**不进入优化**。重开条件见决策记录。

## 3. 扫频与带宽测量

- `--sweep-step`、`--sweep-type`（Discrete/Interpolating）、`--save-fields`、`--verify-frequencies`
  贯通 GUI 与 worker；默认配方不变（1 GHz 离散），保证既有证据可比；
- A/B 结论（`docs/evidence/sweep_ab.json`）：0.5 GHz **离散** 13 点在测量窗口内未完成导出，
  被 `E_SWEEP_EXPORT_INCOMPLETE` 拒绝（`docs/evidence/sweep_fine_discrete.json`）；**插值扫频**
  29 分钟完成、13 频点、带宽一致、共同频点差 ≤0.018 dB → 需要细看时用插值 + 关键频点复核。

## 4. DoE 铺底

- `antopt/doe.py`：拉丁超立方、按 `config_hash` 去重、跳过已测、FAILED 可重排、计划可续跑；
- `scripts/run_doe.py`：入队（幂等）；`scripts/analyze_doe.py` + `antopt/doe_report.py`：完成/失败、
  可行点、分布、最优、参数粗略效应；
- 首轮 10 点 DoE 正在真实 worker 上执行（campaign `<workspace>\phase_doe`），
  结果将写入 `docs/evidence/doe_results.json`。

## 5. 鲁棒性与多机

- `antopt/robustness.py`：公差扰动生成（边界裁剪、去重）+ 最差情况/合格率评分（11 项测试）；
- 多机代码与离线测试完整，**真实多机未验证**（本机单授权环境）。

## 6. 踩过的坑（值得记住）

1. AEDT 球面网格导出是**扁平表**，读取器必须表头驱动（曾静默丢方向性指标）；
2. worker 直接跑队列时**不会注册案例**（GUI 掩盖），已修；
3. 求解前的解数据探测曾刷 AEDT 消息窗口（PyAEDT 报表管线噪声），已改为"无法确定即 None"；
4. 失败的任务不能当成"已测"，DoE 去重只看非 FAILED；
5. 验证脚本必须用**中性 cwd**，否则源码树会掩盖安装版缺陷。

## 7. 首轮 DoE 结果（v1.45.0）

`docs/evidence/doe_results.json`：10 点计划、8 完成、**0 可行**；方向性 0.04–6.88 dBi，带宽全 0；
失败 2 点分别是测量窗口不足（慢设计）与 AEDT gRPC 会话故障（会话被多次重启后的不稳定）。
参数粗效应：`disc_radial` ↑ +3.2 dBi、`disc_factor` ↓ 2.9 dBi、`ring_height` ↓ 1.7 dBi。
