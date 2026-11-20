# 天线优化系统 · 本目标最终报告

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


> 目标：收尾 GPT 模块 A（Pareto 导出 / 辐射效率）+ yadof v0.8.1 协作 + 打通 GUI 变量案例 +
> 漏波渐变降维 + 余量项。测试不得回退，每阶段打 tag、更新 docs，先代码+测试再真实验收。

## 一、结论一页

| 要求 | 判定 | 证据 |
|---|---|---|
| P1① Pareto 前沿导出（CSV/JSON + 每代过程 + API + GUI 下载） | ✅ | `antopt/export.py`、`antopt/pareto.py`、`GET /api/pareto`、GUI「导出 Pareto」按钮；18 项测试 |
| P1② 辐射效率（远场算 η，接入 objectives 与约束，附单测） | 🟡 代码 ✅ / 真机数据源受阻 | 数学 `antopt/physics/directivity.py`（8 项解析测试）、守护 `aedt_guard/evaluator.py`；约束用法 3 项测试；真机两条路径均被求解器否掉，见 `docs/evidence/efficiency_investigation.json` |
| P2 yadof 适配器（受控执行） | ✅ | `antopt/interop/yadof*.py`；`python -m yadof check` 通过 |
| **P2 8 候选 × 2 臂对拍** | ✅ | 8 个环径两臂都测，7 个完全一致（≤1.5e-4 dBi），见第二节 |
| P3 GUI 变量案例真机求解 | ✅ | 2.05 / 2.15 / 2.20 / 2.25 / 2.30 / 2.35 / 2.40 / 2.45 全部完成 |
| P4 漏波渐变降维（3 参数） | ✅ | `cases/lwa.py` + 11 项测试（生成 + 边界） |
| P5 批量采集（qNEHVI 的 q） | ✅ | `surrogate/batch.py`，首点等价纯 EI，6 项测试 |
| P5 Pareto 结果 GUI 下载 | ✅ | 按钮 + `/api/pareto` |
| P5 透镜单元级参数化 | ❌ 未做 | 见第五节 |
| P5 BoTorch 原生 qLogNEHVI | ⚠️ 替代 | 用 Kriging believer 提供同样的 q 语义；环境无 torch |

## 二、真机结果（8 点 × 2 臂）

| 环径 | yadof 臂 | GUI 批量臂 | 差异 |
|---|---|---|---|
| 2.05 | 2.0 GHz / 6.19140 dBi | 2.0 GHz / 6.19154 dBi | 1.4e-4 |
| 2.15 | 3.0 GHz / 6.25977 dBi | 3.0 GHz / 6.25979 dBi | 1.8e-5 |
| 2.20 | 3.0 GHz / 5.881228 dBi | 3.0 GHz / 5.881195 dBi | 3.3e-5 |
| 2.25 | 3.0 GHz / 6.07163 dBi | 3.0 GHz / 6.07182 dBi | 1.5e-4 |
| 2.30 | 3.0 GHz / 6.05460 dBi | 3.0 GHz / 6.05461 dBi | 1.6e-5 |
| 2.35 | 3.0 GHz / 6.10872 dBi | 3.0 GHz / 6.10871 dBi | 1.1e-5 |
| 2.40 | 3.0 GHz / 5.88029 dBi | 2.0 GHz / 5.88026 dBi | 增益 3.4e-5；带宽为指标不连续点 |
| 2.45 | 3.0 GHz / 6.16775 dBi | 3.0 GHz / 6.16780 dBi | 5.2e-5 |

- **一致性**：7/8 完全一致；2.40 的增益一致到 3.4e-5，带宽在 2.0/3.0 GHz 之间翻转
  （该环径压在 −10 dB 边缘，26 GHz 附近一个扫频点决定结果）。
- **耗时**：yadof 单候选 1260–1500 s；GUI 批量单候选 1000–1260 s。
- **可复现**：2.25 两次独立批量运行，增益与波纹四位小数一致。

## 三、这轮修掉的真实缺陷（都在真机上暴露过）

| # | 缺陷 | 修复 |
|---|---|---|
| 1 | GUI 只传改动字段 → `KeyError` | `apply()` 先合并默认值，非法值返回失败 |
| 2 | 只建扫频不设求解设置 → `E_NOT_CONVERGED` | `executor.setup_config_code` 唯一一份 |
| 3 | 每个 job 新建 bridge → 会话不共享 | `RealRunManager._shared_bridge` |
| 4 | 工作线程的 MCP 调用落在无会话事件循环 → `port 0` | `prepare_in_caller_thread` |
| 5 | 线程里重复配置 setup → `InsertFrequencySweep` | 已配置则跳过 |
| 6 | yadof 会话不设 `farfield_setup_name` → `CreateReport` | 生成器设置 |
| 7 | 异常被吞 → 全被改名成 `E_NOT_CONVERGED` | 记录 `setup_error` |
| 8 | 看门狗按进程采样（本环境看不到 AEDT） | 改整机忙时 CPU，并写明其局限 |
| 9 | 扫频未解完就导出 → 单行 touchstone | 导出偏短则等待重试（策略可配） |
| 10 | **每候选重建扫频** → `InsertFrequencySweep` + 部分求解 | 会话只配置一次扫频 |

## 四、测试与标签

- 设计系统：**417** 项全绿（目标起点 325，+92）
- 守护侧：**435** 项全绿，1 skip（起点 418，+17）
- 设计侧 tags：`v0.83 … v1.14.0`；守护侧：`v1.16.1 … v1.20.0`

## 五、明确的未做项与理由

1. **透镜单元级参数化**（P5 可选项）：未做。当前是"按环参数化"（环径/环高/环位移），
   单元级需要改几何生成器与参数文件，属独立特性；剩余轮次不足以既改又真机验证。
2. **BoTorch 原生 qLogNEHVI**：环境无 torch，改用 `surrogate/batch.py` 的 Kriging believer
   提供同样的"批量候选、彼此去相关"语义，并有测试与首点等价性验证。
3. **辐射效率真机取数**：求解器不提供 Directivity/功率量，全域网格方案会破坏运行；
   代码与单测就位、默认关闭、显式拒绝并给出证据路径。

## 六、给下一个会话的入口

- `docs/handover.md`：三条已验证路径、未决项、修复清单、证据索引；
- `docs/evidence/`：53 份 JSON，含全部 run id 与数值；
- 重跑命令：`scripts/run_var_case.py`（脚本）、`scripts/run_batch_arm.py`（批量臂）、
  `scripts/yadof_single_candidate.py`（yadof 臂）、`scripts/run_p2_comparison.py`（对拍）。

## 七、软件化（本阶段追加，2026-11-14）

目标从能跑研究的代码库变成能日常使用的软件。六步，全部有测试与真机/真安装验证：

| 阶段 | 交付 | 验证 |
|---|---|---|
| A 统一配置层 | ntopt/config.py：探测 → config.toml → ANTOPT_* | 本机零配置探测全绿；入口脚本不再含硬编码路径（测试钉住） |
| B 稳定性闭环 | ntopt/stall.py、ntopt/status_watch.py、ntopt/failures.py | 停滞→杀→重试一次；12 类失败翻译成中文建议 |
| C 队列持久化 | ntopt/gui/store.py（queue.sqlite）、/api/queue、/api/resume | 终态先落库再发布；重启后可按原参数续跑 |
| D 采集接入 | ntopt/surrogate/backends.select_batch | auto/显式/降级三态；修掉符号错误后 qLogNEHVI 超体积为随机 1.85× |
| E 打包 | packaging/build.py、entry.py、smoke.py | 单目录 exe 62 MB，启动 5.1 s，5 项接口冒烟通过 |
| F 安装与真机 | packaging/installer.iss、erify_installer.py、
eal_acceptance.py | 安装/卸载零残留；打包产物真机求解 COMPLETED，与源码路径差 1e-6 dBi |

### 打包后真机结果

20261113T213049345677：**3.0 GHz / 5.881228 dBi / 1.630717 dB**，源码路径 5.881229 dBi。

### 软件化过程中暴露并修复的打包缺陷

缺 JSON 数据文件（--selftest 抓到）、缺数据集导致 503、worker 用 sys.executable（冻结后是
应用本身）、InProcessBridge/StdioBridge 在冻结进程都不可用、验收脚本因进程树未杀而挂住。
最终架构：**exe 只做启动器**，用 venv 解释器重跑 python -m antopt.app，真机路径与源码完全同一份代码。

### 分发物

| 文件 | 大小 | 说明 |
|---|---|---|
| dist/AntennaOptimizer-1.47.1-setup.exe | 182.3 MB | 安装版：按用户安装、开始菜单、卸载、版本号（1.44.0 重建，安装/卸载零残留已验证，见 docs/evidence/installer.json） |
| dist/AntennaOptimizer-1.47.1-portable.zip | 257.5 MB | 便携版：由 `build.py --portable` 生成，解压即用 |
| dist/AntennaOptimizer/AntennaOptimizer.exe | 62.1 MB | 单目录构建本体（被上面两者包含） |

两个分发物都在 dist/ 下（不进版本库，见 .gitignore）；重建：python packaging/build.py --installer。

## 八、从"能测量的软件"到"会优化的软件"（2026-11-14 第二阶段）

上一个阶段结束时，软件能测、能打包、能装，但优化还是"人给候选、它去测"。这一阶段把它补成闭环。

### 8.1 做了什么

| 步骤 | 交付 | 关键测试/证据 |
|---|---|---|
| 优化闭环 | `antopt/recommend.py`、`GET /api/recommend`、页面「推荐下一批」 | 只认 `COMPLETED` 且字段完整的实测；少于 4 条直接说"数据不足"；候选上标注依据 run id |
| 阶段耗时与失败率 | `RealJob.timings`、`RealRunManager.observability()`、`GET /api/observability` | 修掉"终态先落库导致最后一段永远缺失"的顺序问题 |
| 结果与磁盘 | `antopt/campaigns.py`、`scripts/manage_campaigns.py` | 保留策略默认 dry-run；证据文件（ledger/queue/spec/projects）永不删，有测试逐项确认 |
| 指标可用性 | `CaseRegistration.unavailable_metrics`、页面提示 | 辐射效率不可用与原因写在页面上，不再"点了才知道" |
| 参数与偏好 | `antopt/preferences.py`、`POST /api/preferences` | 边界**只能收窄**不能越过案例物理界限（422 + 原因） |
| 界面状态/版本/英文手册 | `antopt/observability.py`、页脚、`docs/user_manual_en.md` | 中英手册章节数一致（测试守着） |
| 采集算法定论 | qLogNParEGO 接入 + 五臂对拍（8 次 / 20 次，配对统计） | qLogNEHVI 与 qLogEHVI 统计上不可区分；两者比 qLogNParEGO 高约 0.3 超体积（t>4） |

### 8.2 只有真机才暴露的四个问题

这一阶段的收获主要来自"真跑"，而不是单测：

1. **停滞通道误杀健康求解**：guard 状态在 `SOLVING` 本来就会停留 20–25 分钟，600 秒"无变化"
   把正常求解杀掉两次。改为只盯 `CREATED/QUEUED/PREPARED`（状态）与 `CONNECTING/MODEL`（阶段），
   长求解交给 CPU 窗口。
2. **残留 AEDT 无法被 guard 校验**，之后每个任务都立即失败。现在启动时先"探一下"再决定复用或重启。
3. **假性停滞无法被打断**：状态标志要等阻塞的 `evaluate` 返回才被读到，真机上因此空等 394 秒。
   求解放到 worker 线程，主线程每秒轮询三个信号。
4. **推荐给出模型拒绝的几何**（`ring_outer=1.8` 违反同轴壁半径规则，0.13 秒即失败）。
   现在先按 `validate_geometry` 收窄搜索范围，再逐点校验，全部不合格时明确说"没有可排队的点"。

### 8.3 真机闭环结果（打包 exe 驱动）

| 运行 | 环径 | 带宽 | 增益 | 波纹 | 耗时 |
|---|---|---|---|---|---|
| `real-20261114T100345-00` | 2.30 | 3.0 GHz | 6.0546 dBi | 1.623 dB | 1224 s |
| `real-20261114T102409-01` | 2.35 | 3.0 GHz | 6.1088 dBi | 1.498 dB | 1413 s |

这两次是"测量 → 用实测数据推荐 → 再测量"的闭环中间两步，全部由 `dist/AntennaOptimizer` 启动。
第一轮闭环里推荐出的点被几何规则拒绝，正是 8.2-4 的来源；修复后的最后一次闭环见
`docs/evidence/real_loop_final.json`。

### 8.3 闭环的真机结果（补充）

`docs/evidence/real_loop_final.json` 是第一次完整闭环：

1. 测 `ring_outer = 2.30`：**COMPLETED**，3.0 GHz / 6.0546 dBi / 1.623 dB，1245.58 秒；
2. 4 条实测拟合后由 qLogNEHVI 推荐 1 个候选，`bounds_used` 显示几何规则已把 `ring_outer`
   收窄到 **[2.2, 5.0]**；
3. 推荐点入队实测：**COMPLETED**，787.63 秒。

但这次推荐本身是个反面教材：4 条数据里只有 `ring_outer` 变过，其余 6 维的 GP 无信息，
采集把它们推到极端角落，实测增益 **-5.41 dBi**。修法是"只在实测覆盖范围内搜索"
（`measured_envelope`：没变化的参数钉住，变过的参数只在 [min, max] 内），
并用 `scripts/check_envelope.py` 在同一真实 campaign 上验证：
两个推荐点的每个变维都落在实测范围内、其余维度等于实测值、几何校验通过。

这提醒了一件事：**闭环跑通 ≠ 推荐有用**。没有覆盖度约束的贝叶斯优化，在样本极少、
高维且多数维度未搅动时，会给出"数学最优、物理荒谬"的点。

### 8.4 测试与标签

- 设计仓：**526 → 612+** 项测试全绿；标签 v1.24.0 … v1.32.0（每步一个）
- 守护仓：439 项不变
- 文档：`docs/status.md`（第 1–7 步）、`docs/benchmarks.md`（五臂两种预算 + 统计检验）

### 8.5 仍然没做的

| 项 | 状态 | 理由 |
|---|---|---|
| 透镜单元级参数化 | 未做 | 需要新的几何模型与真机验证，与"把优化做成闭环"是两件事 |
| 辐射效率 | **已定论：本设计不可得** | 三条路径（直接性报告 / 全向网格 / 功率量）均有真机或 PyAEDT 证据，见 `docs/decisions/efficiency.md`；界面已标注不可用及原因 |
| 多目标偏好接入超体积 | 部分 | 权重已接入标量后端；超体积参考点仅落盘展示 |
| 推荐器不理解求解代价 | **已解决（v1.38.0）** | `antopt/cost.py` + 预算约束 + 页面预计耗时；留一法误差 7.3 分钟 |

## 九、1.39.0 现状（软件化第二阶段收尾）

### 9.1 软件现在能做什么

| 能力 | 入口 | 证据 |
|---|---|---|
| 用实测数据推荐下一批，并说明理由 | 页面「推荐下一批」/ `GET /api/recommend` | `docs/evidence/envelope_check.json`、真机 `real_loop_final.json` |
| 推荐不会给出"注定失败"的点 | 几何校验 + 实测覆盖约束 + 预算过滤 | `tests/test_recommend_validation.py`、`tests/test_cost_budget.py` |
| 预测单次求解代价并卡预算 | `antopt/cost.py`、`budget_seconds` | `docs/evidence/cost_model.json`（留一法 7.3 分钟） |
| 运行中可见、失败可读 | 页面「运行状态」、`/api/observability`、`/api/runs` | `docs/evidence/live_queue.json`（0.0 秒可见） |
| 停滞自动处置 | 三通道 + 求解预算（45 分钟兜底） | `docs/evidence/stall_channel.json`（6 秒识别） |
| 结果与磁盘管理 | 页面导出、`scripts/manage_campaigns.py` | `tests/test_campaigns.py` |
| 参数边界与目标偏好可改 | 页面参数面板 + 「保存边界与权重」 | `%APPDATA%\antopt\preferences.json` |
| 可安装、可便携 | `dist/AntennaOptimizer-1.39.0-setup.exe` / `-portable.zip` | `docs/evidence/installer.json`、`live_queue.json` |
| 本设计不产出的指标提前声明 | 「约束」下方不可用提示 | `docs/decisions/efficiency.md` |

### 9.2 明确仍受限的事

| 事项 | 状态 | 说明 |
|---|---|---|
| 辐射效率 | **本设计不可得（已定论）** | 三条路径均有证据；换设计/换 AEDT 可按决策记录重开 |
| 推荐器不理解"收敛概率"的物理原因 | 部分 | 能预测耗时与失败率，但不知道*为什么*某个几何不收敛 |
| 多目标偏好的超体积参考点 | 部分 | 权重已接入标量化后端；参考点仅落盘 |
| 求解吞吐 | 环境限制 | 单机单 AEDT、单候选 20–25 分钟，优化一轮 4 个点约 1.5 小时 |

### 9.3 质量与发布

- 设计仓测试 **657 全绿**（本阶段起点 526）；守护仓 **439 全绿**；
- 版本单一来源（`antopt/version.py` ← 最新 tag，`scripts/release.py --check` 守护），
  `CHANGELOG.md` 由标签间提交生成；
- 分发物 1.39.0 已重建并完成安装/卸载零残留与打包冒烟验证。

### 9.4 从哪里开始读

1. `docs/user_manual.md` / `docs/user_manual_en.md`：日常使用；
2. `docs/status.md`：每个阶段的动机、做法与证据；
3. `docs/decisions/efficiency.md`：一个"做不了"的结论是如何定下来的；
4. `docs/evidence/`：全部真机与测量证据（JSON）。

## 十、1.44.0：Astra 评审的落地与 yadof 集成（2026-11-15）

### 10.1 Astra 评审（`docs/handover/review_2026-11-15.md`）落地了什么

| Astra 的问题 | 处理 | 证据 |
|---|---|---|
| 重试复用被阻塞线程池，"声称 2 次实际 1 次" | **重写尝试生命周期**：独立 attempt/PID/bridge/executor、只杀自有 PID、`RECOVERY_REQUIRED`、晚到结果不回写 | `tests/test_recovery_lifecycle.py`（4 项故障注入） |
| S11 单位与带宽跨过不达标点 | 单位换算 + 频带断开语义（Astra 直接改的代码，本轮合并） | `tests/test_handover_regressions.py` |
| CPU 监测不可用被当 0、连接异常被忽略 | 未知信号不触发假停滞；连接失败即终止 | 同上 |
| 许可证拒绝未分类 | 新增「许可证不可用」规则（环境失败、可重试），不与环境/几何混淆 | `tests/test_fault_injection.py` |
| "效率不可得"证据不足 | **旧结论被推翻**：按类别枚举出 93 个 Antenna Parameters 量；后续诊断表明该已解工程取不到数 | `quantities_full.json`、`efficiency_query_diagnostic.json` |
| 最小故障注入集 | 六类场景全部守住不变量并落证据 | `docs/evidence/fault_injection.json` |
| 心跳/进度/预算分开 | 四类信号分离、分类器**无 kill 语义**、随任务持久化 | `tests/test_progress.py`、`test_telemetry_run.py` |

### 10.2 yadof 集成（`docs/handover/yadof_integration.md`）

采用其两条公开**约定**（未复制代码）：

1. **归一化超体积**：固定参考点 `(1,…,1)`、累计 + 逐次贡献；按案例目标归一化（教训：按实测极值会被坏点拉爆）；
2. **多机协同**：中心调度 + 任务租约 + 按 `config_hash` 合并去重；不引入 HTCondor，也不共享网络 SQLite。

明确未采用：GPSAF/INR 代理、实验性 qNEHVI selector、generation 语义、HTCondor 资源标定、历史分段存储。

### 10.3 当前状态

- 设计仓 **837 测试全绿**（本阶段 765 → 837）；守护仓 439；
- 标签 v1.43.0 → **v1.44.0**；
- 已知未闭环项见 `docs/known_issues.md`（最重要一条：效率需要一次带保存场的受控运行）。

### 10.4 安装版启动缺陷（v1.44.1，用户现场发现）

用户在 `D:\AntennaOptimizer` 安装 1.44.0 后双击无反应、手册也打不开。两个真实缺陷：手册路径
指向 `{app}` 而文件在 `_internal`；以及**冻结包只带了 `antopt` 的数据文件**，重启的
`python -m antopt.app` 无法导入包——此前打包验证都以仓库为 cwd，源码树掩盖了它。修复：把包源码
打进 `_internal`、启动失败写日志并弹窗、修正手册路径、三个验证脚本改用临时 cwd，
并新增 `tests/test_packaging_layout.py`。已在本机 `D:\AntennaOptimizer` 重装 1.44.1 并从
`<user-home>` 启动验证：`/api/about` 返回 `antopt 1.44.1`。

### 10.5 启动体验（v1.44.2）

用户报告：真实求解模式下一打开软件就先启动 AEDT，并出现黑色命令行窗口，看不到 GUI。原因是启动器
用控制台版解释器重跑、`--real` 在启动时急切拉起 AEDT、浏览器打开结果被忽略。现在：子进程以
`CREATE_NO_WINDOW` 启动；AEDT 改为第一个排队任务开始时惰性启动；浏览器打开失败会弹窗给出 URL；
启动日志记录模式与 GUI 地址。已在安装版上从 `<user-home>` 以 `--real` 验证：
启动前后 AEDT 进程数 0→0，`/api/about` 返回 `antopt 1.44.2`。

### 10.6 方向性目标（v1.44.4）

用户要求金属周期结构透镜的辐射集中在 θ=90°，方向性进入优化指标（目标 7 dBi、主瓣偏差 ±5°）。
guard（v1.22.0）新增 `directivity_horizontal_from_grid()`：由同一张球面增益网格积分同时给出 η、
峰值方向性与 θ=90° 平面峰值方向性，evaluator 输出 `directivity_horiz_db`；设计系统新增该 metric
kind、`lens28_var` 的硬约束（≥7 dBi、主瓣 85°–95°）与第 3 个优化目标，页面显示两个新指标。
设计仓 856、守护仓 444 项测试全绿。**数值尚未在真实求解上验证**——下一步是一次受控真机运行。

### 10.7 阶段 B：方向性真机验证与效率单源（v1.44.8）

一次带 `include_directivity` 的真机运行（21 分钟）在实时管线给出 **θ=90° 方向性 6.747 dBi、
主瓣 90.0°**，默认设计因此被判为不可行（目标 7 dBi / 3 GHz）——这表明指标链路正确，优化目标明确。
此前首个运行曾**静默丢失**方向性：AEDT 导出的球面网格是扁平表（Freq, Phi, Theta, 一行一对），
而读取器只认矩阵布局，异常被可选指标的 try 吞掉；已在 guard v1.22.1 改为表头驱动并补 4 项测试。
离线重放真实产物得到峰值方向性 6.803 dBi、水平面 6.747 dBi（独立梯形积分差 <0.1 dB）、η≈1.01
（PEC 透镜，物理一致）。η 仍为单一来源，Antenna Parameters 独立取数受 PyAEDT 附着限制，
因此效率保持待验证、不进优化。

### 10.8 阶段 C 扫频 A/B 与阶段 D 首轮 DoE（v1.44.9）

同一设计对比两种测量方式：0.5 GHz **离散** 13 点扫频在 30 分钟求解后仍未导出，被
`E_SWEEP_EXPORT_INCOMPLETE` 明确拒绝（不把一行数据当带宽）；**插值扫频** 29 分钟完成，导出 13 个
频点，带宽 2.0 GHz 与 1 GHz 离散参考一致，6 个共同频点 S11 最大差 0.018 dB。结论：默认保持
1 GHz 离散，细节用插值扫频。首轮 DoE（10 点拉丁超立方）已按身份去重入队并由 worker 逐点执行；
排队路径顺带修掉"未注册案例导致全部任务 unknown case"的真实 bug，FAILED 任务现在允许重排。

### 10.9 首轮 DoE 结果与 v1.45.0 收口

10 点拉丁超立方在真实 AEDT 上跑完 8 点：**没有任何点同时满足**"θ=90° 方向性 ≥7 dBi、带宽 ≥3 GHz、
增益 ≥6 dBi"。方向性分布 0.04–6.88 dBi（默认设计 6.75 dBi），而铺底点带宽**全部为 0**——带宽是绑定
约束。参数粗效应给出下一步方向：`disc_radial` 增大、`disc_factor`/`ring_height` 减小有利方向性；
真正的提升需要围绕可行种子做定向优化或改结构。同时用 AEDT 消息缓冲直接验证：修复后
"Solution is not a property name" 计数为 **0**（修复前每次作业数十条）。版本收口 1.45.0，
设计仓 918 测试、守护仓 459 测试全绿；效率独立来源与真实多机仍如实列为未验证。

### 10.10 种子搜索定论与结构改版决策（v1.46.0）

从已测数据挖出带宽种子区（ring_outer 2.15–2.35 → 3.0 GHz），再用 7 个真机设计确认：
**方向性 ≥7 dBi 与带宽 3 GHz 在现有 7 参数几何内互斥**——3 GHz 需要 ring_height≈0.6（方向性 6.8–6.9 dBi），
方向性 ≥7 需要 h≈0.5（带宽 2.0 GHz）；旁瓣全部在 −1.7 ～ −3.0 dBc（栅瓣级）。
增益 ≥6 dBi 与方位全向可同时满足（6.40 dBi / 1.30 dB 纹波）。
决策记录 `docs/decisions/structure_change.md` 给出改版方案：优先**双高度环**（内高 0.5/外高 0.6），
宽带需求再做馈源/介质层，低旁瓣做非周期/渐变结构；证据 `docs/evidence/design_tradeoff.json`。

### 10.11 24–32 GHz 阶段（v1.47.0）

频带扩到 24–32 GHz（8 GHz 目标）后：结构 A 在 24–30 内同点达标（3.0 GHz、7.09–7.20 dBi、
6.43 dBi、1.40 dB），但 24–32 未达到；结构 B（三层环）实现并通过测试，批次因会话问题未完成。
本阶段定位到用户真实天线族为迁移包中的 AstraBB（阶梯双锥馈源 + 70 mm 波纹盘 29 圈 + 三扇区支撑），
其原始构建器可在 AEDT 内执行；两个随包集成候选在 24–32 实测均为 3.0 GHz / 2.6–2.7 dBi（主瓣 42°，
纹波 6.6–6.8 dB），说明截图目标是同结构经额外优化后的版本，迁移包中没有该工程。
下一步：对 AstraBB 结构做 6–9 点少变量优化（flare/radial/台阶/盘内径），并先解决 AEDT 附着稳定性。

## 11. 24–32 GHz 全频段专项（AstraBB 复现）

本轮以迁移包原始脚本重建 AstraBB（阶梯双锥同轴馈源 + 70 mm/29 圈波纹盘 + 三扇区支撑），在本机 AEDT 2026.1 上做 24–32 GHz 九点、12 步收敛的实测调参。关键结论：

1. 馈源单独 24–32 GHz 连续 8.0 GHz；装盘后基线只有 3.0 GHz，失败点是**馈源-盘内孔净空**（原孔 6.2 mm 是按旧双锥馈源调的）。把内孔开到 8 mm 并缩小馈源径向包络到 radial 1.25–1.30 后，连续带宽到 **7.0 GHz（24–31）**。
2. 剩下 32 GHz 的缺口来自**盘的周期波纹结构**：缺口位置随环距移动（环距 1.0 → 缺口在 32，0.90 → 30–31，1.10 → 31，1.15 → 30），任何单一环距都无法覆盖 24–32；强径向渐变（+0.25）会把缺口劈成两个、带宽更差。
3. 增益在环距 1.15 时首次达到 **5.09 dBi**（参考截图 5–8 dBi 区间的下沿）；θ=90° 面纹波 1.1–1.7 dB，但主瓣仍在 θ≈42°/138°，未回到赤道面。
4. 方法学修复：两次 campaign 共用一个 AEDT 时工程名都叫 lens28_var，后一次会静默测到前一次的工程（曾出现两次几何不同但 S11 完全相同的假数据）；现在每个变体独立启停 AEDT。

### 1. 带宽优先：candidate30 + 内孔 8 mm + 馈源 radial 1.30 + 盘环距 1.0

| 频率 (GHz) | 24 | 25 | 26 | 27 | 28 | 29 | 30 | 31 | 32 |
|---|---|---|---|---|---|---|---|---|---|
| S11 (dB) | -12.38 | -16.02 | -20.38 | -24.67 | -26.63 | -10.86 | -11.29 | -14.99 | -7.05 |

- 连续 ≤−10 dB 带宽：7.0 GHz（24.0–31.0 GHz）；
- 峰值增益：4.25 dBi；θ=90° 纹波：1.40 dB；最大旁瓣：−0.60 dBc；主瓣 θ：40°。

### 2. 增益优先：candidate30 + 内孔 8 mm + 馈源 radial 1.30 + 盘环距 1.15

| 频率 (GHz) | 24 | 25 | 26 | 27 | 28 | 29 | 30 | 31 | 32 |
|---|---|---|---|---|---|---|---|---|---|
| S11 (dB) | -14.21 | -17.42 | -23.21 | -21.02 | -14.77 | -13.53 | -9.53 | -11.27 | -12.93 |

- 连续 ≤−10 dB 带宽：5.0 GHz（24.0–29.0 GHz）；
- 峰值增益：5.09 dBi；θ=90° 纹波：1.66 dB；最大旁瓣：−0.75 dBc；主瓣 θ：138°。

### 交付物

- `<workspace>\astra_deliverables\`（工程 + 参数 + 实测 s1p + 方向图 + README）；
- 证据：`docs/evidence/feed_disc_cotuning.json`、`final_24_32_acceptance.json`；
- 构建/测量工具：`<workspace>\astra_builder.py`、`astra_probe.py`、`run_variant_once.py`。

## 12. θ=90° 扁平甜甜圈方向图专项（AstraBB）

目标（用户参考 "Final" 工程，25–33 GHz）：辐射集中在 θ≈90°、峰值 6–7.8 dBi、方位近圆、轴向深零陷。基线（现有实心波纹盘）实测只有 θ=40° 的锥面、θ=90° 最大 1.95 dBi、θ∈[60,120] 功率占比 0.30。

本轮用真实 AEDT 逐项验证了允许改动的结构杠杆：

1. 环高径向渐变 0.45/0.65/0.8：0.45 仍沿轴，0.65–0.8 饱和在 θ=124–126° 锥面（峰值 6.1–6.5 dBi），带宽掉到 2.0 GHz；
2. 馈源轴向偏移 0/−0.5/−1.0 mm：双稳态换瓣（40° ↔ 138°），带宽保持 7.0 GHz，但没有 90° 中间态；
3. 加厚实心盘体/边缘：锥面 140°、带宽 2.0 GHz；
4. 实心边缘环（径向环 5 mm）：锥面 34°、带宽 7.0 GHz；
5. 径向平行板（盘下 3 mm 平板）：锥面 142°、带宽 2.0 GHz。

**结论：以上结构改动均未形成 θ=90° 甜甜圈模式。** 实心盘及其边缘变体的辐射始终是“边缘绕射锥”（约 40° 或 125–142°），要得到参考图的赤道面甜甜圈，需要在盘边缘做出明确的径向口径（双平板+开口缝隙/径向喇叭），这超出本轮试过的外圈周期参数范围，需要用户确认后再做结构级改动。

验收指标对比（全部为本机真实 AEDT 实测）：

| 变体 | horizon max | ripple | horizon−axial | near-axis max | 功率占比 | 连续带宽 | 全指标达标 |
|---|---|---|---|---|---|---|---|
| B2_feed_z_offset_m0p5 | 1.46 | 1.54 | -2.99 | 3.59 | 0.3015 | 7.0 | 否 |
| D1_rim_ring5 | 1.41 | 1.37 | -3.48 | 4.15 | 0.2351 | 7.0 | 否 |
| B1_feed_z_offset_m1 | 1.13 | 1.17 | -3.66 | 3.88 | 0.3142 | 7.0 | 否 |
| C1_thick_disc_rim | 0.04 | 1.61 | -5.67 | 5.71 | 0.1426 | 2.0 | 否 |
| A1c_height_gradient_0p65_narrowband | -10.16 | 4.1 | -14.77 | 4.61 | 0.1765 | None | 否 |
| E3_radial_plate_gap3 | -10.64 | 4.79 | -16.39 | 4.84 | 0.2176 | 2.0 | 否 |
| A1_height_gradient_0p8 | -10.97 | 7.79 | -15.03 | 3.84 | 0.1434 | 2.0 | 否 |
| A1b_height_gradient_0p45_p10 | -11.66 | 11.9 | -16.83 | 5.17 | 0.2293 | None | 否 |

证据：`docs/evidence/directivity_optimization.json`、`docs/evidence/directivity_final_verdict.json`。
