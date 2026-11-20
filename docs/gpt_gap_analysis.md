# GPT 意见落实情况（逐条核对）

来源：桌面上的六份文档（已抽取为 `docs/review/*.md`）：

| 文件 | 内容 |
|---|---|
| `gpt_improve1.md` | 任务书：模块 A–D + `OptimizationStrategyController` |
| `gpt_improve1_reply.md` | 我方对任务书的回复 |
| `gpt_improve2.md` | 参数化建模升级要求（13 条） |
| `gpt_improve2_reply.md` | 我方回复 |
| `gpt_solution.md` / `gpt_reply.md` | 早期方案与对话 |

核对口径：**以仓库里的代码与真实证据为准**，不以"曾经回答过"为准。

## 一、`gpt_improve1` 任务书

### 模块 A：带约束的多目标优化

| 要求 | 状态 | 证据 |
|---|---|---|
| NSGA-II + 可行性优先比较 | ✅ | `antopt/optimizer/nsga2.py`（pymoo 约束支配）、`tests/test_nsga2.py` |
| **升级到 NSGA-III** | ✅ 本次新增 | `antopt/optimizer/nsga3.py`、`tests/test_nsga3.py`、benchmark 策略 `nsga3` |
| Pareto Archive（参数/指标/可行/违反/支配/算法/run） | ✅ | `antopt/optimize.py::ParetoArchive` |
| 硬约束不能靠加权并入评分 | ✅ | 约束走 `g(x) ≤ 0`，缺数据记 `MISSING_PENALTY` |
| 综合有效带宽（多指标同时满足的连续区间） | ✅ | `antopt/core.py` 的 `effective_bandwidth`，案例里按采样点判定 |
| **Pareto 前沿导出（数据 + 过程记录）** | ✅ 已补（v0.83.0） | `antopt/export.py`（CSV/JSON + 过程记录）、`ParetoArchive.first_front()`、10 项测试 |
| 辐射效率作为目标 | 🟡 单元验证完成，真机受阻 | `antopt/physics/radiation.py` + 守护 evaluator 双侧单测；**开启 directivity 表达式会让真机求解卡死/扫频只导出 1 行**，已回退（guard v1.16.1），需 opt-in 开关后再验（`docs/evidence/efficiency_investigation.json`） |
| 副瓣/波束角约束 | ✅ | `sll`、`beam_angle` 在 contract/core，lens28 案例已用 |

### 模块 B：代理模型与贝叶斯优化

| 要求 | 状态 | 证据 |
|---|---|---|
| 统一代理接口、GPR/RBF、误差与不确定性 | ✅ | `antopt/surrogate/models.py` |
| 采集函数（EI/UCB）、可行性优先 | ✅ | `antopt/surrogate/selection.py`、`benchmark/ablation.py` |
| 多保真分层 | ✅ | `antopt/surrogate/multifidelity.py`（S3） |
| **BoTorch / qLogNEHVI 多目标贝叶斯** | ❌ | 未接入；当前是自研 EI/UCB |
| 预测值不得冒充实测 | ✅ | `status=PREDICTED` 与 `COMPLETED` 分离 |
| 同预算对比随机/GA | ✅ | `docs/benchmarks.md`、`docs/evidence/ablation_*.json` |

### 模块 C：物理灵敏度

| 要求 | 状态 | 证据 |
|---|---|---|
| 灵敏度矩阵、耦合判断 | ✅ | `antopt/physics/sensitivity.py` |
| 物理引导的局部搜索 | ✅ | `antopt/physics/local_opt.py`、`strategy_loop.py` |

### 模块 D：结构级搜索

| 要求 | 状态 | 证据 |
|---|---|---|
| 结构算子与结构族 | ✅ | `antopt/topology/operators.py`、`antopt/families.py` |
| 何时该换结构的触发规则 | ✅ | `antopt/topology/triggers.py` |
| **自动生成新结构（而非只选已有算子）** | 🟡 部分 | 有算子组合与族基线；没有"生成器"式的新拓扑合成 |

### 控制器

| 要求 | 状态 | 证据 |
|---|---|---|
| 按状态/预算选策略 | ✅ | `antopt/controller.py`（规则状态机） |
| 统一调度四模块 | 🟡 部分 | 控制器已串 A/B/C/D，但未做预算分配的最优控制 |

## 二、`gpt_improve2`：参数化建模 13 条

| # | 要求 | 状态 | 证据 |
|---|---|---|---|
| 1 | 识别几何参数与依赖 | ✅ | `lens28_var` 的 7 参数 + `physics` 说明 |
| 2 | 改成设计变量/派生表达式驱动 | ✅ | `revolve_profile` + 参数化轮廓；派生量在 `profile.py` |
| 3 | 统一参数文件（名/单位/默认/上下限/连续离散/物理意义/制造约束） | ✅ | `antopt/parametric/spec.py::ParamDefinition`（含 `manufacturing`） |
| 4 | `update_parameters()` 接口，不重建脚本 | ✅ | `OperationModel.update` + 增量重建（单参 1.1 s） |
| 5 | 几何有效性检查（相交/非法尺寸/端口失效） | ✅ | `geometry_checks` + `validate_simple` |
| 6 | 结构族拆分（参数优化 vs 拓扑重建） | ✅ | `families.py`、`topology/` |
| 7 | 漏波天线优先参数化周期/单元/长度/馈电/渐变 | 🟡 部分 | `cases/lwa.py` 已参数化脊与周期；**渐变用低维函数描述未实现** |
| 8 | 金属透镜分层（单元参数化 + 孔径参数化） | 🟡 部分 | 当前是"整孔径参数化"（tip/盘/环），**单元级参数化未做** |
| 9 | 与 GA/Guard/Evaluator/历史库集成 | ✅ | `benchmark/harness.py`、`gui/runner.py`、`campaign.py` |
| 10 | 参数化模型的自动测试（默认/边界/随机/非法/重复/恢复） | ✅ | `tests/test_lens28_var*.py`、`test_revolve_profile.py`、`test_profile_transforms.py` |
| 11 | 不推倒重建现有工作流 | ✅ | 旧 `lens28_ref` 保留，变量模型并行 |
| 12 | 不把结构变更硬编码成连续变量 | ✅ | 结构算子走 `topology`，参数空间只有尺寸 |
| 13 | 不允许新模型覆盖已有有效设计 | ✅ | 家族基线 + `ParetoArchive` 不覆盖可行解 |
| 14 | 参数化失败保留原模型并记录错误 | ✅ | `apply` 失败即回滚到上次可用几何并记 `error` |

## 二点五、本轮（20 回合）补齐项与真机状态

| 项 | 状态 | 证据 |
|---|---|---|
| 综合有效带宽 / 约束处理 / Pareto 归档 | ✅ | 既有 optimize.py、
sga2.py；本轮补 Pareto 导出 |
| Pareto 前沿导出（数据 + 过程记录 + API + GUI 下载） | ✅ | ntopt/export.py、ntopt/pareto.py、GET /api/pareto |
| 批量采集（qNEHVI 的 q） | ✅ 替代实现 | ntopt/surrogate/batch.py（Kriging believer，首点等价纯 EI） |
| 漏波渐变降维（h_n = h0 + a1*u + a2*u^2） | ✅ | ntopt/cases/lwa.py、	ests/test_lwa_taper.py |
| GUI 变量案例真机求解 | ✅ | atch-20261112T181456（2 候选 COMPLETED） |
| 辐射效率真机取数 | 🟡 受阻 | directivity 表达式导致停滞/扫频 1 行，已回退 |
| yadof 8 候选真机对拍 | ❌ 未达成 | docs/evidence/yadof_single.json |
| 透镜单元级参数化 | ❌ 未做 | 当前仅整孔径参数化 |

## 二点七、终态（v1.9.1）

| 要求 | 终态 | 依据 |
|---|---|---|
| Pareto 导出（数据 + 过程 + API + GUI） | ✅ | export.py / pareto.py / /api/pareto，18 项测试 |
| 辐射效率 | 🟡 数据源不可得 | 该设计只有 Gain*；网格方案破坏运行；数学两侧就绪并默认拒绝 |
| 批量采集（qNEHVI 的 q） | ✅ | surrogate/batch.py，首点等价纯 EI |
| 漏波渐变降维（三参数） | ✅ | cases/lwa.py + 11 项测试 |
| GUI 变量案例真机求解 | ✅ | 2.05 / 2.20 / 2.25 多点完成，2.25 复现两次 |
| yadof 8 候选对拍 | 🟡 部分 | 单点三点一致（<4e-5），8 点未跑满 |
| 透镜单元级参数化 / BoTorch 原生 | ❌ / ⚠️ | 未做 / 用 Kriging believer 替代 |

## 三、仍未落实的清单（按优先级）

| # | 缺口 | 来源 | 建议动作 |
|---|---|---|---|
| ~~1~~ | ~~辐射效率目标~~ | A-3.2 | ✅ v0.83.0：数学与接入已做，缺的是**远场导出带 directivity**（守护侧加一列） |
| ~~2~~ | ~~Pareto 前沿导出~~ | A-3.6 | ✅ v0.83.0：CSV/JSON + 过程记录；GUI 下载按钮排在 P5 |
| 3 | **BoTorch 多目标贝叶斯（qLogNEHVI）** | B-4.5 | 作为 `surrogate` 的可选后端，与现有 EI/UCB 同预算对比 |
| 4 | **自动结构生成器** | D | 目前是"选算子"，需要"按目标合成算子组合"的搜索 |
| 5 | 漏波天线的低维渐变函数 | 2-7 | 用 2–3 个系数描述 20 条脊的渐变（GPT 给的 h_n = h0 + a1·u + a2·u²） |
| 6 | 透镜的单元级参数化 | 2-8 | 单元库 + 孔径相位分布→单元映射 |
| 7 | 控制器预算分配 | 控制器 | 现有规则状态机不优化"给哪个模块多少预算" |

## 四、本次已经补上的两项

1. **NSGA-III**（用户点名）：`antopt/optimizer/nsga3.py` + benchmark 策略 + 6 项测试；
   合成消融里 HV 346.7（NSGA-II 334.4），评估次数 19.6（NSGA-II 20.4）。
2. **yadof 互操作**：`antopt/interop/yadof.py`（参数文件生成 / AEDT 变量表导入 /
   recorded_data 导出）+ 9 项测试，见 `docs/yadof_integration.md`。

> 说明：以上"✅/🟡/❌"以代码与 `docs/evidence/` 为准；若某条与 GPT 原文措辞有出入，
> 以原文要求为准再补。
