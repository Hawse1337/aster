# 天线优化系统 · 使用手册

本文面向**使用者**（不是开发者）。开发者文档在 `docs/status.md`、`docs/benchmarks.md`、
`docs/handover.md`，本目标的技术结论在 `docs/final_report.md`。

## 1. 安装

1. 二选一：
   - **安装版**：运行 `AntennaOptimizer-1.21.0-setup.exe`——按用户安装（不需要管理员权限），
     带开始菜单、卸载项与版本号；
   - **便携版**：把 `AntennaOptimizer-1.21.0-portable.zip` 解压到任意目录
     （例如 `D:\Tools\AntennaOptimizer`），双击其中的 `AntennaOptimizer.exe`；
2. 双击 `AntennaOptimizer.exe`；
3. 首次启动若缺少配置，会在控制台里逐项询问 AEDT、守护仓库、参考工程的位置，
   回答后写入 `%APPDATA%\antopt\config.toml`，以后不再询问。

**不需要安装 Python**：可执行文件自带解释器。但**真实求解**需要：

| 依赖 | 用途 |
|---|---|
| AEDT 2026 R1 或更高 | 实际电磁仿真 |
| `ansys-aedt-mcp` 仓库及其 `.venv`（含 PyAEDT） | 受控执行与导出 |

## 2. 第一次运行

启动后浏览器会自动打开 `http://127.0.0.1:8765/`（端口被占用时会自动换一个，以控制台打印为准）。

- **只读模式**（默认）：可以用"试算"按钮在**已测量的曲面上**做插值估算，不启动 AEDT；
- **真实求解**：启动时加 `--real`，或使用开始菜单里带 `--real` 的快捷方式。

## 3. 界面

| 区域 | 作用 |
|---|---|
| 参数面板 | 环径、环高、环位移、馈源等；越界会立刻标红。每项下方可改 **下界/上界**（只能收窄，不能越过案例的物理范围） |
| 试算 / 真实运行 | 试算=插值；真实运行=提交 AEDT |
| 批量细扫 | 一次提交多个候选，按顺序跑，可中途查看进度 |
| **推荐下一批** | 用**已有实测数据**拟合并推荐候选：填**个数**与**预算(分)**，选后端；表格给出参数、**预计耗时**、可行性（按最近实测估计）与依据 run id。点「推荐结果入队」直接排队 |
| **带宽权重 / 增益权重** | 表达"更看重哪个目标"；保存后参与推荐（标量化后端与理由说明里都会体现） |
| **保存边界与权重** | 把边界与权重写入 `%APPDATA%\antopt\preferences.json`，下次启动仍然有效 |
| **运行状态** | 运行次数、成功率、各阶段耗时中位数、最慢的几次；数据来自 `GET /api/observability` |
| 结果表 | 每个候选的带宽、增益、波纹、是否可行 |
| 导出 Pareto | 把当前非支配前沿导出 CSV/JSON（含每代进度） |
| S 参数曲线 | 真实运行跑完后可查看实测 touchstone |
| 效率提示 | 若某指标该设计根本产不出（如辐射效率），会在「约束」下方直接说明原因，不必等运行失败 |

### 推荐是怎么算的（也是"为什么是这几个点"）

1. 只取 `COMPLETED` 且参数/目标完整的实测行作为训练集；少于 4 条直接提示"实测数据不足"；
2. 只在**实测覆盖过的范围**内搜索：没变化过的参数钉在实测值，变化过的参数只在 [最小值, 最大值] 内；
3. 用模型的几何校验剔除会被拒绝的设计，并按 `budget_seconds` 剔除**预计超预算**的点；
4. 每个候选都带 `predicted_cost`（预计分钟数、区间、样本数、失败率、方法），
   页面上的「预计耗时」就是它。

## 4. 关掉之后还能继续吗

能。每次运行的状态都会写进任务库（`<campaign>\queue.sqlite`）。

- `GET /api/queue` 列出**还没跑完**的批量；
- 页面上的"继续上次批量"会按**原参数**重新排队未完成的候选。

## 5. 常见错误码

系统会把错误码翻译成人话显示在结果里。对照表：

| 错误码 | 含义 | 怎么办 |
|---|---|---|
| `E_NOT_CONVERGED` | 8 次自适应后仍未收敛 | 提高自适应次数或放宽 `MaxDeltaS` |
| `E_SWEEP_EXPORT_INCOMPLETE` | 扫频没解完就导出 | 系统会自动等待重试；连续失败请加大扫频步长 |
| `INVALID_PARAMS` | 参数越界 | 按提示调整 |
| `GEOMETRY_REJECTED` | 这组参数会生成自交实体 | 调整环径/环高组合 |
| `NO_AEDT` | 找不到运行中的 AEDT | 在配置里指定 `aedt_exe`，或先手动启动 AEDT |
| `REAL_DISABLED` | 当前是只读模式 | 用 `--real` 启动 |
| `STALLED` | 求解停滞（进程活着但没进展） | 系统会杀掉并重试一次；反复出现请检查网格规模 |

## 6. 配置

配置文件：`%APPDATA%\antopt\config.toml`（也可用 `ANTOPT_CONFIG` 指定别的路径）。

```toml
[paths]
aedt_exe = "D:\\ANSYS Inc\\v261\\AnsysEM\\ansysedt.exe"
guard_repo = "C:\\ProgramData\\ansys-aedt-mcp"
venv_python = "C:\\ProgramData\\ansys-aedt-mcp\\.venv\\Scripts\\python.exe"
reference_project = "C:\\Users\\me\\Desktop\\lensfinal\\LensFinal.aedt"
campaign_root = "D:\\AgentAEDT_Collab\\antopt_bench"
```

优先级：**自动探测 < 配置文件 < 环境变量**（`ANTOPT_AEDT_EXE` 等）。
想看当前生效的值：`AntennaOptimizer.exe --selftest`。

## 7. 命令行

| 命令 | 作用 |
|---|---|
| `AntennaOptimizer.exe` | 启动页面（只读模式） |
| `AntennaOptimizer.exe --real` | 启用真实求解 |
| `AntennaOptimizer.exe --wizard` | 重新走一遍配置向导 |
| `AntennaOptimizer.exe --selftest` | 检查安装（依赖、页面、配置） |
| `AntennaOptimizer.exe --version` | 版本 |
| `--port 8765` / `--campaign DIR` / `--project X.aedt` | 覆盖默认值 |

## 8. 卸载

1. 删除程序目录；
2. 需要的话删除 `%APPDATA%\antopt\`（配置）与各 campaign 目录（结果）。

AEDT 与 `ansys-aedt-mcp` 不受影响。

## 9. 出问题时从哪里看

1. 页面上的错误提示（已翻译成中文说明与建议）；
2. 控制台窗口的启动日志；
3. `<campaign>\queue.sqlite` 里的任务状态；
4. 若要反馈问题，请附上 `--selftest` 的输出与 run id。
