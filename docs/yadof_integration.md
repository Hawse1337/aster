# yadof v0.8.1 与我们的遗传算法系统：对比与协作方案

对象：<https://github.com/YS-Pan/yadof/releases/tag/v0.8.1>（2026-11-08 发布，
源码提交 `a56cfdab1051158b27539824e8bce0895740abf9`）。

## 一、这个发布到底是什么

从 release 说明与仓库 README（`README.md`，`main` 分支）可确认的事实：

| 项 | 内容 |
|---|---|
| 定位 | 仿真驱动的设计研究框架：本地或 HTCondor 上跑评估，保留原始输出，内置代理模型与可编辑的搜索策略 |
| 算法 | 单目标用 **GA**，多目标用 **NSGA-III**；可加 GPSAF 代理辅助、条件 INR（隐式神经表示）、幅值/形状 GP |
| 工作区契约 | `job_template/parameters_constraints.py`（参数/范围/可行性）、`job_template/workflow.py`（怎么跑仿真、怎么收集原始输出）、`submit/calc_cost.py`（原始输出→归一化目标）、`submit/optimization.py`（搜索、评估、训练的顺序）、`config.py`（执行模式/并发/超时） |
| 执行模式 | `local`（每个候选起子进程）、`fast`（复用常驻 worker）、`distributed`（HTCondor 池） |
| 命令行 | `python -m yadof init / check / smoke-test / run / view / docs / version` |
| v0.8.1 变化 | 工具拆成 `yadof_tools` 与 `user_tools`；`user_tools/aedt_parameters_gui.pyw` 提供完整 AEDT 参数界面，**只导出 `OptimizationSetup1` 里勾选 Include 的变量**，替换参数文件前先备份原文件；CLI 与导入路径保持兼容 |
| 交付物 | Windows x64 MSI（内置 CPython 3.11.15 私有运行时）、wheel、tar.gz、admin zip、验证报告 JSON、SHA256SUMS |
| 适配器 | HFSS、ngspice、Project Chrono |
| 基准 | 36 组 × 每组 5000 次真实评估，比较 real-only / PCA+SVD / 幅值 GP / 检索式响应 |

一句话：**yadof 是"仿真评估 + 优化循环"的通用骨架，我们这套是"天线领域 + HFSS 受控执行 + 领域算子"的专用系统。**

## 二、逐项对比

| 维度 | yadof v0.8.1 | 我们（aster） | 结论 |
|---|---|---|---|
| 多目标算法 | NSGA-III（第三代），可配 GPSAF 代理 | NSGA-II（旧）+ **本次新增 NSGA-III** | 持平（我们刚补上） |
| 单目标 | GA | 随机/LHS/代理辅助 + 物理引导局部搜索 | 我们更细 |
| 约束处理 | 参数文件里写可行性规则 | `g(x)≤0` 归一化 + 可行性优先 + 缺数据惩罚 | 我们更严格（可迁移给 yadof） |
| 代理模型 | 幅值/形状 GP、PCA+SVD、检索式响应、INR | GPR/RBF + EI/UCB + 多保真 | 各有侧重；我们缺 BoTorch |
| 执行后端 | local / fast / distributed（HTCondor） | 单机 AEDT + 守护进程（guard）+ 30 次同工程改参 | **yadof 的分布式是明显优势** |
| 原始数据保留 | `recorded_data/`，可用 `calc_cost.py` 重新打分 | JSONL 历史 + touchstone + 证据文件 | 都保留；yadof 的"换目标不重跑"更系统 |
| AEDT 集成 | 参数导出 GUI（Include 勾选）+ HFSS 适配器 | 全参数化重建（`revolve_profile`）、孔阵列、端口 | 我们的几何控制更深，他们更通用 |
| 可复现性 | 随机种子、快照、断点续跑 | 种子、tag、`docs/evidence/` | 我们证据更细，他们续跑更成熟 |
| 交付形态 | MSI/wheel/校验和 | Python 包 + 脚本 + GUI | 他们更像产品 |

## 三、协作方式（从"最省事"到"最深"）

### 方式 1：参数互导（已实现，零风险）

`aster/interop/yadof.py` 提供三件东西：

1. `parameters_constraints_source(space, constraints)` → 直接生成 yadof 工作区要的
   `job_template/parameters_constraints.py`（参数名/单位/上下限/类型/物理意义/制造约束 +
   `feasibility()` + `bounds()`），**以我们的参数文件为单一真源**，避免两边各写一份；
2. `space_from_optimization_setup(rows)` → 把 AEDT 变量表（含 Include 勾选，即
   yadof 那个 GUI 的导出约定）读回成我们的 `ParamSpace`，**只导出勾选的变量**；
3. `to_recorded_data(entries)` / `write_recorded_data(...)` → 把我们的评估结果写成
   yadof `recorded_data/` 的 JSONL 行（参数 + 原始输出 + 可行性 + 成本），
   他们可以用 `calc_cost.py` 换目标重打分而不重跑仿真。

用法：

```python
from aster.cases import lens28_var
from aster.interop.yadof import parameters_constraints_source, write_recorded_data, to_recorded_data

space = lens28_var.build_model().space
open("my-study/job_template/parameters_constraints.py", "w", encoding="utf-8").write(
    parameters_constraints_source(space))
write_recorded_data("my-study/recorded_data/rows.jsonl", to_recorded_data(entries))
```

### 方式 2：让 yadof 当"分布式执行器"，我们当"几何与守卫"

yadof 的 `job_template/workflow.py` 只要求"给一组参数，跑一次仿真，交回原始输出"。
我们可以把**守护（guard）受控执行**包成这个 workflow：

- `workflow.py` 调 `aster.bridge` → 守护 worker → `OperationModel.update(params)` →
  求解 → 返回 touchstone 路径与远场 CSV；
- 由 yadof 的 HTCondor 模式把 N 个候选分发到多台机器（每台一个 AEDT）；
- 我们的 `SimulationGuard` 仍在每台机器上把关（收敛、扫频行数、远场球）。

这是**收益最大**的一条：把我们的"单机 30 次改参"扩成"集群 N×30"。
前置条件：每台 worker 上有 AEDT + guard + 我们的 case 包（yadof 的 README 明确说
worker 不需要装 yadof，只需要任务环境）。

### 方式 3：共享优化器，交换数据集

- 我们把 `docs/evidence/lens28_ref_measured.json`（21 个可信实测点）+ 本次 S3 扩样本
  的 10 个点导出成 yadof 的 recorded_data，让 yadof 的 NSGA-III 与 GP 在我们的
  真实数据上跑，比较"他们的搜索 vs 我们的代理搜索"；
- 反向：yadof 的 benchmark 报告（36 组 × 5000 次）里"幅值 GP 提升 Q 值"的证据，
  可以用来决定我们要不要引入幅值/形状 GP。

### 方式 4（可选）：AEDT 参数界面借力

yadof 的 `user_tools/aedt_parameters_gui.pyw` 已经做了"扫描 AEDT 设计变量、勾选后导出、
替换前备份"这套 UI。我们不必重写：**他们负责"选变量"，我们负责"这些变量怎么驱动几何"**
（`revolve_profile`、孔阵列、环领）。`space_from_optimization_setup` 就是这个接口的入口。

## 四、建议的落地顺序

| 步骤 | 内容 | 工作量 | 风险 |
|---|---|---|---|
| 1 | 已完成：参数互导 + recorded_data 导出（`aster/interop/yadof.py`，9 项测试） | — | 低 |
| 2 | 写 `yadof` 工作区样例：`job_template/workflow.py` 调守护执行一次真实 HFSS | 半天 | 中（要处理项目复制与端口） |
| 3 | 用 yadof `local` 模式跑 8 个候选，与我们 batch 队列对拍（同样 8 个点，比较耗时与结果一致性） | 1 天 | 中 |
| 4 | 上 HTCondor：2 台 worker × 每台 1 个 AEDT，跑 16 个候选 | 2–3 天 | 高（许可、路径、并发写工程） |
| 5 | 算法层对拍：我们的 NSGA-III vs yadof 的 NSGA-III+GPSAF，同一预算同一数据集 | 1 天 | 低 |

## 五、结论

- **算法代差已消除**：我们补上了 NSGA-III；yadof 的 NSGA-III 与我们同一代。
- **两边真正的差异不在算法，在执行规模与工程化**：他们有 HTCondor 分布式、MSI 交付、
  换目标重打分；我们有更深的几何参数化、更严的仿真守卫、更细的证据链。
- **最优协作姿势**：参数与数据用方式 1 打通（已做），执行用方式 2 把我们的受控执行
  挂到他们的分布式骨架上，算法用方式 3 在同一批真实数据上对拍。
