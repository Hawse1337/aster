# 实机运行时操作纪律（经验教训）

本文件记录真实 HFSS 运行期间必须遵守的约束，避免用工程时间换教训。

## 1. 单实例

同一台机器**只允许一个 AEDT 实例**做受控运行（交接书要求，也是守护队列的前提）。
启动新战役前先确认：

```powershell
Get-Process ansysedt,hf3d -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'guard_daemon' }
```

`guard_daemon` 每战役最多一个（v1.15.5 起，活着的守护在长跑期间不再被接管）。

## 2. 求解期间不要跑重内存/长任务

2026-11-10 07:27 的 `stepL3` 在附加 setup 求解时出现
`AEDT broker exited with code 0`，时间点与一个内存失控的本地调试进程重合。
求解期间只做轻量工作（读日志、写文档、跑不占内存的小测试）。

## 3. 成本基线（B0 透镜模型，16 核）

| 配置 | 耗时 |
|---|---|
| 单点 28 GHz，主 setup + 附加 `FF_Swp`，4 passes/0.04 | ~25–30 分钟 |
| 单点，主 setup 直接是 `FF_Swp`（无附加） | ~10–12 分钟 |
| 全频段（主 + `Swp_fast` 9 点离散） | ~35–45 分钟 |
| 全频段 + Interpolating 扫频 | ~20–30 分钟 |

结论：做算法对比时用**单点**（成本可控、指标同质）；全频段只在候选收敛后做一次确认。

## 4. 失败恢复

- `RECOVERY_REQUIRED` 且 AEDT 仍存活：`scripts\resume_run.py` 从保存阶段续跑（不重算）；
- AEDT 已退出：工程未保存则只能重跑；重跑前清锁（`clear_stale_lock` 会随下次 attach 自动调用）。

## 5. 报告纪律

- 只能用实测指标下结论；预测值一律标 `predicted`；
- 每个结论附 `run_id` 与产物路径；
- 近似设置（单点、粗扫、少 passes）必须在报告里注明。

## 6. 扫频导出的完整性（2026-11-11 教训）

同一个几何（环 2.35 mm）两次测量：一次导出 4/7 行、带宽算成 2.0 GHz；另一次导出 7/7 行、
带宽 3.0 GHz，而两次的逐频 S11、增益、波纹**完全一致**。稀疏导出会静默地把带宽算小，
进而误导代理、控制器和整条优化链。

规则：

- 带宽类指标**先校验采样完整性再计算**；
- 守护 v1.15.12 起会从 sweep 的频点列表得到 `expected_rows`，行数不足直接判
  `E_SWEEP_EXPORT_INCOMPLETE`（失败关闭）；
- 看到"带宽突然变 0 / 变小"时，先查 `artifacts/result.s1p` 的行数，再谈设计结论；
- `scripts/audit_exports.py` 可一次性审计所有历史 run 的行数并标出不可信结论。

## 7. 收敛门槛按改动幅度分级（2026-11-11 教训）

- 单参数微调（环径 0.05 mm、tip 0.1 mm）：**8 passes / MaxDeltaS 0.02** 足够；
- 叠加结构改动（环 + 盘厚、环 + 馈电阶、多环）：**12 passes**；
- 8 passes 出现 `E_NOT_CONVERGED` 时不要当作"指标差"，那是**没算完**，必须提高 passes 重跑；
- 收敛失败的 run 不得进入归档与代理训练集。
