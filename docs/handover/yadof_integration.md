# yadof 集成说明（借鉴了什么、没借鉴什么、差在哪）

对象：`yadof` 0.8.3（https://github.com/YS-Pan/yadof，快照 commit `ce87150`）。本文件记录我们采用其公开文档中的
**协议/约定**（未复制代码），以及明确**没有**照搬的部分，便于后来者判断边界。

## 1. 超体积约定（v1.43.1 / v1.43.3）

**来源**：yadof `user_doc/config_and_run.md` 的 *History and tools* 一节——成本视图用**固定归一化
参考点 `(1, ..., 1)`** 画超体积带：上沿是截至当前所有代的累计超体积，下沿是当前代自身的贡献。

**我们的实现**：`aster/metrics.py`

- `normalisation_from_case()`：按**案例目标**归一化（带宽 3.0 GHz / 增益 6.0 dBi 为理想，0 为最差），
  **优于目标允许负值**（截断在 −1），避免"刚达标就饱和"；
- `hypervolume_2d()`：二维精确扫描；
- `hypervolume_progress()`：逐次 `cumulative` 与 `generation`（该次自身贡献）；
- 入口：`GET /api/hypervolume`、页面「运行状态」与推荐说明、`scripts/check_hypervolume.py` 证据。

**一个教训**：最初按**实测极值**归一化，结果一个 −5.4 dBi 的坏设计把量程拉爆、所有好点饱和到 1.0。
yadof 是"先算成本再归一化"，所以必须用任务目标而不是测量范围——这一点写进了 benchmarks 与测试。

## 2. 多机协同（v1.43.4）

**来源**：yadof `distributed` 模式（HTCondor）及其文档建议"多机不要共享网络 SQLite"。

**我们的实现**：`aster/distributed.py` + `aster/dist_agent.py` + `worker.py --agent`

| 维度 | yadof | 我们 |
|---|---|---|
| 调度器 | HTCondor | 自带 HTTP + **任务租约**（无集群依赖） |
| 回传 | `rawData.zip` / `individual_metadata.json` / `batch.log` | 指标/状态/run_id（原始证据留节点本地 campaign） |
| 合并 | 按任务结果回传 | 按 `config_hash = sha256(case+排序参数)[:16]` 去重合并 |
| 资源标定 | CPU/内存/磁盘自动标定 | 未做；每节点 `--max-tasks` 限流 |
| 适合 | 有 HTCondor 集群 | 2–5 台工作站、无集群运维 |

## 3. 没有照搬的部分（以及为什么）

| yadof 能力 | 我们的状态 | 原因 |
|---|---|---|
| GPSAF 代理辅助选择 / 条件 INR 代理 | 未采用 | 我们用 BoTorch qLogNEHVI/qEHVI + Kriging believer，已有五臂对拍证据；换框架需要重做基准 |
| `posterior_assisted_selector()`（qNEHVI） | 未采用 | yadof 文档自称实验性且受 readiness 检查阻塞 |
| 生成/代（generation）概念 | 未采用 | 我们的单位是"批次/候选"，与 AEDT 单次求解 20–25 分钟匹配；代语义价值有限 |
| HTCondor 资源标定与超时清理 | 未采用 | 无 HTCondor；等价的租约过期回收已实现 |
| 历史分段存储（`HISTORY_*`） | 未采用 | 我们的证据是 `queue.sqlite` + `real_runs.jsonl`，规模远小于其 5000 次/格 |

## 4. 结论

我们采用 yadof 的两条**约定**（归一化超体积、租约式中心调度 + 按身份合并），
代码独立实现并有测试与证据；其余部分按本项目场景（单机 AEDT、昂贵求解、无集群）**有意不采用**。

## 5. 同步到 yadof 0.8.3（2026-11-17）

- 快照：`v0.8.3`，commit `ce87150fe09c77625385be7094d60d9b348c5cfa`（2026-11-13，
  "Release yadof 0.8.3: skip invalid surrogate training individuals"）；
- 本机 `D:\yadof` 工作副本已 `git fetch --tags` 并 ff 到 v0.8.3，`.venv` 里的 yadof 从 0.8.0
  重新安装为 **0.8.3**（`import yadof; yadof.__version__ == "0.8.3"`）。

### 0.8.1 → 0.8.3 的上游变化（公开文档/变更记录）

| 变化 | 内容 | 对我们的影响 |
|---|---|---|
| 代理训练健壮性 | 单个训练个体 rawData 不可读/为空/含非有限值/字段-形状-dtype-轴-元数据不兼容时**整条跳过**；保留出现最多的完整 schema（并列时字段/数据点更多者优先，再取首次出现）；日志报告跳过数量与有界示例；不修复、不删除证据；行身份连接错误、调用方数组错位、模型/持久化错误仍然抛出 | 与我们"不编造测量值"的原则一致；我们记录为参考约定，代码不复制。注意区别：对**旧参数维度缺失**我们仍用 `ParamSpace.complete()` 补默认值（几何等价），这与 yadof 的"跳过坏个体"不是同一件事，不能混用 |
| 工具目录/入口分类 | `condor_gui.pyw` 移到 `yadof/tools/yadof_tools/console/`；新增 `viewcost.pyw`/`viewtime.pyw`/`viewerror.pyw`/`surrogateviewer.pyw` 与 `user_tools/aedt_parameters_gui.pyw`；Python 侧导入路径改为 `yadof.tools.yadof_tools`、`yadof.tools.user_tools.aedt_parameters`；CLI 名称/参数不变 | 仅文档层面：我们的 workspace 生成器不依赖这些桌面入口路径 |
| 仿真总耗时显示 | GUI 计时用 `HH:MM:SS`，≥24h 用 `D+HH:MM:SS`（如 `1+02:03:04`）；停止/完成/失败冻结，重启归零，仅当前会话 | 未采用（我们页面已有自己的耗时口径，记录避免误解） |
| 新文档 | `user_doc/optimization_workflow.md`：任务定义（参数、workflow/adapter、rawData 与 cost、策略组合）、验证与 smoke、优化与查看 | 作为约定来源记录，后续需要时再对照 |

### 兼容性结论

从 0.8.1 到 0.8.3，`src/yadof/job_template/**` 与 `src/yadof/recorded_data/**` **没有变更**
（变更集中在 `admin_tool`、`surrogate`、`user_doc`、测试），且 0.8.3 仍从
`yadof.job_template` 导出 `Parameter`（我们生成的 `parameters_constraints.py` 用它）。
因此我们生成的 workspace 三处接缝（`parameters_constraints.py` / `workflow.py` / `evaluation.py`）
**继续有效**，无需改生成器；本次只更新版本引用与本文档。
