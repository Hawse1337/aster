# 系统与软件说明（给外部评审）

> 一页说明：这套东西是什么、文件在哪、怎么跑、数据与证据在哪、质量门槛是什么。
> 配合 `docs/handover/open_problems.md` 一起看。当前版本 **1.42.0**，设计仓测试 **713 项全绿**。

## 1. 两个仓库、各自职责

| 仓库 | 角色 | 关键入口 |
|---|---|---|
| `<repo>`（`aster`） | 设计/优化系统：参数化模型、实验编排、代理模型与推荐、页面控制台、打包分发 | `aster/app.py`（服务+页面）、`aster/worker.py`（后台执行）、`scripts/*`（实验与验收） |
| `<guard-repo>`（`aedt_guard`） | 守护层：受控执行 AEDT、结果校验、缓存、失败分类；设计系统**只能通过它**动 AEDT | `bridge.py` / `pyaedt_worker.py` / `aedt_guard/*` |

设计系统不直接 import PyAEDT 去开 AEDT；它通过 guard 的桥（进程内或 stdio）提交"运行"请求，guard 启动/复用 AEDT 并执行。

## 2. 运行链路（真实模式）

```
网页 (index.html)
  └─ HTTP /api/* ──> aster.gui.server (GuiApi)
                        └─ RealRunManager (线程): 阶段推进 / 停滞检测 / 求解预算 / 失败翻译
                              └─ bridge ──> aedt_guard ──> PyAEDT worker ──> AEDT (ansysedt -grpcsrv)
结果写入：queue.sqlite（状态/耗时/参数/指标）、real_runs.jsonl（事件流）、runs/<id>/artifacts（s1p、远场 CSV）
```

`AntennaOptimizer.exe` 是**启动器**：冻结包内无法直接 import PyAEDT，所以它用 venv 解释器重跑
`python -m aster.app`；`--worker` 则重跑到 `python -m aster.worker`。源码路径与打包路径是同一份代码。

### 在已批准的模型范围内自动运行

对已入队的 `lens2g4_10g` 候选，可以把三个尺寸交给守卫登记的 `p0_lens_dimensions`。使用守卫管理的 AEDT PID，选择已经具备设计、求解设置、扫频和远场球的基线工程。下面的 PowerShell 命令采用 P0 示例的设置名和扫频名。将变量替换为本机配置。

```powershell
$modelArgs = @(
    '--real', '--once', '--case', 'lens2g4_10g',
    '--approved-builder', 'p0_lens_dimensions', '--pid', "$managedPid",
    '--project', $baseline, '--builder-outdir', $outputDirectory,
    '--design', 'Trunc_10GHz', '--setup', 'Setup1', '--sweep', 'Sweep_5_18',
    '--campaign', $campaign
)
python -m aster.worker @modelArgs
```

守卫校验通过后，worker 仅提交 builder 创建的独立工程。超出已配置范围时，任务保存为 `APPROVAL_REQUIRED`，终端给出原任务 ID 和请求 JSON 路径。对于 `approval_mode=once` 的请求，操作者在守卫仓库执行 `python scripts/approve_builder_request.py <请求文件>`，核对实际项目、设计和参数并确认。保持原参数和 PID，执行 `python -m aster.worker @modelArgs --retry-approval <原任务ID>`。返回码 3 表示批次中仍有待审批任务。缺少策略或请求字段时，根据响应的 `reason` 修正配置。模型或输入校验失败后不会提交求解。

## 3. 关键文件地图

### 3.1 设计仓（`<repo>`）

| 路径 | 作用 |
|---|---|
| `aster/config.py` | 配置层：探测 < `%APPDATA%\aster\config.toml` < `ASTER_*` 环境变量 |
| `aster/cases/` | 案例定义（`lens28_ref`、`lens28_var`、LWA）：参数空间、目标、约束、几何校验 |
| `aster/parametric/` | 参数化建模操作（剖面重建、环、馈源等）与 `OperationModel` |
| `aster/surrogate/` | 代理模型与采集：`botorch_acq.py`（qLogEHVI/qLogNEHVI/qLogNParEGO）、`backends.py`（统一调度）、`batch.py`（believer） |
| `aster/recommend.py` | 「推荐下一批」：实测→拟合→几何校验→实测覆盖约束→预算/风险过滤→候选与理由 |
| `aster/cost.py` / `aster/risk.py` | 求解耗时回归；候选级失败风险（近邻实测失败率） |
| `aster/preferences.py` | 参数边界（只能收窄）、目标权重、超体积参考点，落盘 `preferences.json` |
| `aster/gui/server.py` | HTTP API：spec/recommend/batch/enqueue/queue/status/observability/preferences/pareto/s11… |
| `aster/gui/runner.py` | `RealRunManager`：单次真实运行的全部纪律（阶段、停滞、预算、失败翻译、持久化） |
| `aster/gui/store.py` | `JobStore`（SQLite）：任务/批次/claim 队列 |
| `aster/worker.py` | 后台执行：consume `queue.sqlite` 的 QUEUED 行，`--once/--watch/--limit/--aedt-ng` |
| `aster/stall.py` / `status_watch.py` / `failures.py` | 停滞检测信号、guard 状态监视、错误码→中文建议（12 类） |
| `aster/campaigns.py` | 结果与磁盘管理：保留策略（默认 dry-run）、导出 CSV/JSON |
| `aster/export.py` / `pareto.py` | Pareto 前沿导出（CSV/JSON + 每代过程） |
| `aster/app.py` | 入口：`--real/--wizard/--selftest/--worker/--aedt-ng/--solve-budget/…` |
| `aster/version.py` | 版本唯一来源（`scripts/release.py --check` 与最新 tag 对账） |
| `docs/status.md` | 每个阶段的动机/做法/证据（当前到第 20 步） |
| `docs/benchmarks.md` | 采集算法对拍（五臂 × 两种预算 + 配对检验）、参考点影响、默认后端依据 |
| `docs/final_report.md` | 总报告与现状（第九章 = 1.39.0 能力表与受限项） |
| `docs/known_issues.md` | **已解决问题清单（带版本+证据）与仍开放清单（带重开条件）** |
| `docs/decisions/` | 决策记录（目前：辐射效率不可得） |
| `docs/evidence/` | 全部真机/测量证据 JSON |
| `docs/handover/` | 本目录：给外部评审的问题清单与本说明 |
| `tests/` | 713 项单元/契约/集成测试（含页面契约、i18n、队列 claim、worker、预算、风险、参考点） |
| `packaging/` | `build.py`（`--installer/--portable`）、`installer.iss`、`smoke.py`、`verify_installer.py`、`verify_live_queue.py` |
| `scripts/` | 实验/验收/校核脚本：`run_var_case.py`、`check_cost_model.py`、`check_risk_model.py`、`check_reference_point.py`、`manage_campaigns.py`、`release.py` 等 |

### 3.2 守护仓（`<guard-repo>`）

| 路径 | 作用 |
|---|---|
| `bridge.py` / `aedt_guard/bridge.py` | 设计系统→guard 的桥 |
| `pyaedt_worker.py` / `pyaedt_backend.py` | 独立 worker 进程：连 AEDT、执行命令（支持 `-ng` 识别、PID 校验） |
| `aedt_guard/evaluator.py` | 指标计算、辐射效率公式、约束判定 |
| `aedt_guard/pattern.py` | 全向网格→方向性积分 |
| `aedt_guard/spec.py` | 运行规格校验（含 `radiation_efficiency` 指标类型） |
| `aedt_guard/*.py`（其余） | 缓存、锁、失败分类、项目副本、导出 |
| `tests/` | 439 项测试 |

## 4. 数据与证据

**campaign 目录**（一个实验一个）：

```
<campaign>/
  campaign.sqlite      # guard 侧运行记录
  queue.sqlite         # 软件侧任务/批次/claim 队列（重启可续）
  real_runs.jsonl      # 事件流（阶段变化每行一条；读取时按 run_id 取最后一条）
  projects/            # 项目副本（可复用）
  runs/<run_id>/artifacts/  # s1p、远场 CSV 等
  design_spec.json
```

**证据约定**：只有 `COMPLETED` 且字段完整的实测行才进代理模型/推荐；预测值永不写成证据
（`requirements` 里叫"never write predicted numbers as measured"）。每次真机关键结论都落一份 JSON 到
`docs/evidence/`，并在 `docs/status.md` 引用。

## 5. 怎么跑

### 5.1 源码

```powershell
$py = "<guard-repo>\.venv\Scripts\python.exe"
& $py -m aster.app --real                 # 页面控制台（浏览器打开提示的地址）
& $py -m aster.app --selftest             # 自检（导入/页面/配置）
& $py -m aster.worker --campaign <dir> --real --supervised --once          # 后台执行队列
& $py -m aster.worker --campaign <dir> --real --supervised --watch 60 --aedt-ng
& $py -m unittest discover -s tests        # 713 项
```

### 5.2 打包产物（截至本说明：**1.39.0 已构建**，1.40–1.42 的修复尚未重新打包，重建命令见下）

```
dist/AntennaOptimizer-1.39.0-setup.exe      # 安装版（按用户安装、开始菜单、卸载）
dist/AntennaOptimizer-1.39.0-portable.zip   # 便携版
python packaging/build.py --installer --portable           # 重建
python packaging/verify_installer.py                       # 安装/卸载零残留验证
python packaging/verify_live_queue.py                      # 打包产物上验证"运行中可见"
```

## 6. 质量门槛与约定

- 测试不得回退；每个阶段一个 git tag（当前 `v1.42.0`），并同步 `status/benchmarks/final_report`。
- 版本单一来源：`aster/version.py` 与最新 tag 由 `scripts/release.py --check` 对账。
- 失败必须**翻译**：每个错误码有中文标题/说明/建议动作，不把 traceback 丢给用户。
- 每个可疑结论先写证据脚本再下结论（本轮就靠这个过程抓到"参考点没传到底"的真实 bug）。

## 7. 目前已知的外部限制（与问题清单对应）

- 单 AEDT、单机、单候选 20–25 分钟；
- 扫频导出间歇不完整；一次失败报告会毒化 worker 会话；
- 某些贴边几何不收敛；
- 辐射效率在本设计上不可得（三条路径均有证据）。
