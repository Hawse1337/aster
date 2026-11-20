# 决策记录：辐射效率——旧结论被推翻

- 状态：**已推翻旧结论（可枚举，接入验证待做）**
- 日期：2026-11-15（初版 2026-11-15，Astra 评审后重开）
- 影响：`lens28_var` / `lens28_ref` 的 `radiation_efficiency` 目标与约束

## 结论（当前）

**旧结论"本设计不可得"是错的。** 只读、按类别完整枚举后（`docs/evidence/quantities_full.json`），
`solved` 工程的 **Antenna Parameters** 类别直接给出：

```
PeakDirectivity, PeakGain, PeakRealizedGain, PeakSystemGain,
RadiatedPower, AcceptedPower, IncidentPower,
RadiationEfficiency, TotalEfficiency, SystemEfficiency, MaxU
```

**Far Fields** 也给出 42 个量（含 `DirTotal`、`DirPhi`… 与 `Pacc`），**Fields / S-parameters**
含 `Pacc`、`Pref`、`Pinc`。因此 η 至少有三条现成路径：

1. `Antenna Parameters / RadiationEfficiency` 直接取；
2. `RadiatedPower / AcceptedPower` 相除；
3. 远场 `GainTotal` 与 `DirTotal` 相减（η = 10^((G−D)/10)，代码已存在并有解析测试）。

## 旧证据（保留，作为历史）

旧结论依据的三份记录仍然保留，供对照：docs/evidence/efficiency_investigation.json
（直接性报告被拒、全向网格毒化会话）、docs/evidence/power_probe.json（PyAEDT 未取到功率量）、
docs/evidence/quantities_probe.json（扁平列表，缺类别/context）。它们的共同问题是**没有按类别枚举**。

## 为什么之前会判错

旧探测 `docs/evidence/quantities_probe.json` 只存了三个**扁平列表**；而 PyAEDT 1.9.0 的
`available_report_quantities()` 在**不指定类别**时会循环类别并**遇首个非空即返回**。旧调用没有保存
类别/solution/context，所以"只有 Gain、没有 Directivity"其实是"只看到了第一个非空类别"。
Astra 的评审正是指出了这一点（见 `docs/handover/review_2026-11-15.md` 第 10 节）。

## 现在的处理

- 只读探针 `scripts/probe_quantities_full.py`：在**独立进程**里附着 AEDT、打开工程副本、
  按 `(report category, quantity category, solution)` 全量询问，逐条记录答案与异常；
  失败即弃用该进程（避免一次失败毒化会话）；
- 界面不再声称"不可用"，改为 **"接入验证中"**（`pending_metrics`）；
  **但指标继续禁用**，直到取数与互校完成——不能因为"能枚举"就宣称"能用"；
- 真正把这些量接成目标/约束仍需要一次受控真机验证（导出 → 与远场/功率互相核对），
  在此之前**不把 η 写进证据或优化目标**。

## 取数第一次受控尝试（v1.43.6）

2026-11-15 附着非图形 AEDT、在工程副本上分别调用 `get_solution_data` 取
`RadiationEfficiency` / `RadiatedPower` / `AcceptedPower` / `GainTotal` / `DirTotal`：
**五次都返回对象、无异常，但数据序列为空**（`docs/evidence/efficiency_values.json`）。
因此"可枚举"进一步确认，"可取数"**尚未成立**。下一步是按 `report_type`/`context`/`variations`
组合做结构与调用诊断，再用原生报告导出交叉验证；在拿到数值并三路互校之前，指标继续禁用。

## 取数诊断（v1.43.9）：get_solution_data 在这个已解工程上全部返回 False

附着非图形 AEDT、打开工程副本后，逐一尝试七种调用（Antenna Parameters 的效率/功率、Far Fields 的
Gain 与 Dir，含 Phi=0deg 与 Phi=All、Modal Solution Data 的 Pacc）：**全部返回 False**
（不是空数组，而是拒绝返回），docs/evidence/efficiency_query_diagnostic.json。
工程里的 setup/sweep 只有 P4 / P4 : LastAdaptive / P4 : ANT_Band。

这与 Astra 的提醒一致：**S 参数 sweep 可能没有保存远场数据**，有可枚举的量不等于这个解能取数。
因此当前状态仍是：**量存在（枚举证据）→ 该已解工程不可取数 → 需要一次带保存场/辐射设置的受控运行**。
在那之前 η 继续标 pending_metrics 并禁用。

## 真机验证（v1.44.8，阶段 B）

一次带 `include_directivity` 的受控真机运行（`phase_b4`，21 分钟求解）在**实时管线**里给出了
`directivity_horiz_db = 6.747 dBi`、`beam_theta = 90.0°`，说明方向性目标已真正接入。
同一份产物的离线重放（`docs/evidence/phase_b_validation.json`）给出：

| 量 | 值 | 来源 |
|---|---|---|
| 峰值方向性 | 6.803 dBi | 球面增益网格积分（guard `pattern`） |
| θ=90° 平面峰值方向性 | 6.747 dBi | 同上 + 独立梯形积分，差 <0.1 dB |
| 峰值增益（网格全局） | 6.860 dBi | 同一网格 |
| 辐射效率 η | 1.013（截断 1.0） | `η = ∮G dΩ / 4π` |
| 主瓣 θ | 90.0° | θ 切面峰值 |

**效率结论（更新）**：金属（PEC）透镜没有欧姆/介质损耗，η≈1.0 与物理一致；这是**单一来源**
（球面网格积分）的数值，独立来源仍缺——本环境下 Antenna Parameters 直取路径受 PyAEDT 1.9.0
版本探测限制（附着 GUI AEDT 报 `specified_version: None`）而不可用，属于**工具限制而非物理不可得**。
因此 η 仍保持"待验证、不进优化"，重开条件改为：**在可驱动的非图形会话里取得
`RadiationEfficiency` 与 `Prad/Pacc` 任一独立数值**。

## 仍待验证

1. `Antenna Parameters` 的 `RadiationEfficiency` 是否在当前 setup/solution 下返回有限值；
2. 三条路径彼此是否自洽（直接值 vs Prad/Pacc vs G−D）；
3. 导出这些量是否会像 `CreateReport` 那样使会话失效（探针只枚举、未取数）。

## 重新开启的条件（与关闭条件）

- 完成一次受控真机取数并互相核对 → 把 η 接入目标/约束，关闭此记录；
- 若取数失败但不毒化会话 → 记录失败证据，保留"接入验证中"；
- 若取数毒化会话 → 回到"当前集成不可得"，但**必须附本次完整枚举证据**，不得再只凭扁平列表下结论。
