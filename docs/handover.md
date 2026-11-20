# 交接清单（本目标收尾，2026-11-13）

本文件给下一个接手的人/会话：**哪些已用真机验证、哪些没有、从哪继续、证据在哪**。

## 一、三条已验证的真机路径（同一参数，同一个数）

参数：环 2.20 mm、tip 1.0 mm、环高 0.6 mm、8 passes、24–30 GHz 离散扫频、28 GHz 远场。

| 路径 | 带宽 | 增益 | run |
|---|---|---|---|
| 独立脚本 `scripts/run_var_case.py` | 3.0 GHz | 5.881229 dBi | `20261112T145151110233` |
| GUI 批量 `RealRunManager`（单会话） | 3.0 GHz | 5.881195 dBi | `real-20261112T181456-00` |
| yadof 会话（`job_template/evaluation.py`） | 3.0 GHz | 5.881228 dBi | `20261112T212409485591` |

三者相差 **< 4e-5 dBi**。环 2.25 的 GUI 批量为 3.0 GHz / 6.0718 dBi
（`real-20261112T183507-01`）。

复现命令：

```
python scripts/run_var_case.py --ring-outer 2.2 --passes 8            # 脚本路径
python scripts/verify_p3_gui_batch.py --ring 2.2                      # GUI 批量路径
python scripts/yadof_single_candidate.py --ring 2.2                   # yadof 会话路径
```

## 二、收尾状态

### 1. yadof 8 候选对拍（已完成）

八个环径两条臂都测了：7 个完全一致（增益差 ≤1.5e-4 dBi），2.40 增益一致到 3.4e-5
但带宽在 2.0/3.0 GHz 之间翻——该环径压在 −10 dB 边缘，属指标不连续点。
证据：docs/evidence/p2_comparison.json、docs/evidence/batch_arm*.json。

### 2. 辐射效率（代码完成，数据源受阻）

- 该设计只报 Gain* 与几何量，没有 Directivity/功率量（quantities_probe.json）；
- 两条取数路径都被真机否掉：直接读表达式（AEDT 拒绝）、全域网格积分（毒化会话）；
- 数学与接入两侧都实现并有解析单测（目标 + 约束两种用法），默认关闭、显式拒绝；
- 若要重开：需要能稳定导出的方向性/功率数据源。

### 3. 仍未做的可选项

- 透镜单元级参数化（P5 可选项，未做）；
- BoTorch 原生 qLogNEHVI（用 surrogate/batch.py 的 Kriging believer 提供同样的 q 语义，
  未引入 torch 依赖）。

## 三、本轮修复的真实缺陷（都在真机上暴露过）

| # | 缺陷 | 修复位置 |
|---|---|---|
| 1 | `ModelApplier` 直接索引参数 → GUI 只传改动字段时 `KeyError` | `antopt/apply.py`：合并默认值、非法值返回失败 |
| 2 | 只建扫频、不设求解设置 → `E_NOT_CONVERGED` | `antopt/executor.setup_config_code`（唯一一份） |
| 3 | 每 job 新建 bridge → 会话不共享 | `RealRunManager._shared_bridge` |
| 4 | 工作线程里的 MCP 调用落在无会话的事件循环 → `port 0` / `InsertFrequencySweep` | `prepare_in_caller_thread` |
| 5 | 线程里重复配置 setup → `InsertFrequencySweep` | 已配置则跳过 |
| 6 | yadof 会话不设 `farfield_setup_name` → `CreateReport` | 生成器 |
| 7 | 异常被吞 → 只剩 `E_NOT_CONVERGED` | 记录 `setup_error` |
| 8 | 看门狗按进程采样（此环境看不到 AEDT/HF3D）→ 健康求解被误判停滞 | 改整机忙时 CPU |

## 四、证据索引

| 文件 | 内容 |
|---|---|
| `docs/evidence/p3_gui_batch.json` | GUI 批量 2 候选 COMPLETED + 与脚本的四位小数一致 |
| `docs/evidence/p2_comparison.json` | 三条路径对照 + 2.25 失败原因 |
| `docs/evidence/yadof_single.json` | yadof 单候选成功 + 需要哪四步 |
| `docs/evidence/efficiency_investigation.json` | 效率真机五次尝试（含停滞证据） |
| `docs/evidence/watchdog.json` 等 | 停滞检测的实时采样 |
| `docs/benchmarks.md` | 三条路径一致性、S3 五样本、NSGA-III 消融 |
| `docs/conclusions.md` | 四条教训（线程/会话、被吞异常、配置三份、停滞≠失败） |
| `docs/status.md` | 开篇即 P1–P5 判定表 |

## 五、状态与标签

- 测试：**antopt 402 全绿**、**guard 422 全绿**（1 skip）；
- 设计系统 tags：`v0.83.0 … v1.3.0`；守护侧：`v1.16.1`；
- 目标判定：P1（①✅ ②🟡）、P2 适配器✅ / 8 候选对拍❌、P3✅、P4✅、P5✅，
  因此**目标不标记完成**，交接后从第二节的两项继续。
