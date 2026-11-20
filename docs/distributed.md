# 多机协同（分布式 campaign）

本说明对应 `aster/distributed.py`（中心调度）、`aster/dist_agent.py`（节点协议）与
`aster/worker.py --agent`（节点入口）。设计参考了 yadof 0.8.1 的 `distributed` 模式：中心调度、
每台机器本地执行、结果按身份回传合并（其文档 `user_doc/config_and_run.md` 的 "Choose the program
and execution mode" 一节并建议多机不要共享网络 SQLite）；我们只用 HTTP + 租约，不依赖 HTCondor。

## 1. 角色与边界

| 角色 | 在哪 | 职责 |
|---|---|---|
| 协调器（coordinator） | 一台机器，`python -m aster.app` 或打包 exe | 写任务表 `<campaign>/dist.sqlite`、发租约、收结果、合并去重 |
| 节点（agent） | 每台有 AEDT 的机器 | 认领任务 → **本地** campaign + 项目副本执行 → 回传结果 |
| 设计系统 | 两处都有 | 计算、校验、KPI 一律在节点本地完成；协调器不算 |

**唯一写者原则**：`dist.sqlite` 只由协调器写；节点只通过 HTTP 交互。任何机器都不允许把 campaign
目录放到网络共享上由多方直接写。

## 2. 协议（HTTP JSON）

| 端点 | 方法 | 作用 |
|---|---|---|
| `/api/dist/enqueue` | POST | `{case, variants:[{参数…}]}` → 以 `config_hash(case, params)` 去重后入队 |
| `/api/dist/claim` | POST | `{worker, lease_seconds}` → 取一个 `QUEUED` 或**租约过期**的任务，置 `LEASED` |
| `/api/dist/heartbeat` | POST | `{worker, task_id, lease_seconds}` → 延长租约（只有持有者可延长） |
| `/api/dist/finish` | POST | `{worker, task_id, result, error}` → 记录 `DONE/FAILED`；重复提交幂等 |
| `/api/dist/status` | GET | 任务表快照与状态计数 |

状态机：`QUEUED → LEASED → DONE|FAILED`；租约到期未回传即回到可认领状态（`attempts` 递增）。
`config_hash` = `sha256(case + 排序后的参数)` 前 16 位——同一设计在任意机器、任意时刻都得到同一身份，
所以重复提交不会凭空多出一次实测。

## 3. 如何启动

协调器（已有 campaign 目录即可）：

```powershell
python -m aster.app --real --port 8765 --campaign D:\campaigns\dist-demo
# 页面或 HTTP 入队：
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/api/dist/enqueue -ContentType application/json `
  -Body '{"case":"lens28_var","variants":[{"ring_outer":2.30},{"ring_outer":2.35}]}'
```

每台节点（各自有 AEDT 与本地 campaign 目录）：

```powershell
python -m aster.worker --agent --server http://<coordinator>:8765 --real `
  --campaign D:\campaigns\node1 --case lens28_var --passes 8 --max-tasks 4
```

- `--lease-seconds`：租约时长（默认 1800 s），应大于单次求解时间的 2 倍以上；
- `--poll-seconds`：空闲轮询间隔；
- `--worker-name`：在协调器上显示的节点名；
- 节点崩溃/断网：租约到期后任务会被其他节点重新认领，`attempts` 会 +1，便于事后定位不稳定节点。

## 4. 合并与去重

- 协调器按 `config_hash` 存放结果，重复的 `finish` 返回 `duplicate: true`，不会生成第二条记录；
- 需要把结果并回本地 campaign 时，按 `run_id + params + config_hash` 对照合并（当前实现提供
  `DistributedScheduler.results_by_config()`，把"已完成的配置"按哈希分组）；
- **不做**跨机器的"预测值"合并——每台机器只回传实测指标。

## 5. 已验证与未验证（诚实边界）

已验证（`tests/test_distributed.py`、`tests/test_dist_agent.py`，共 20 项）：
- 同一设计不重复入队；claim 原子性；租约过期可被另一节点认领；心跳只能由持有者延长；
- 重复 finish 幂等；他人 lease 不能提交；错误记为 FAILED；
- 两个 agent 并发消费同一队列：**每个设计恰好被评估一次**；
- 一个 agent 认领后"死亡"（不提交）：另一个 agent 在租约过期后接手，`attempts=2`。

未验证：真实多机网络、防火墙/时钟偏差、长时间（数小时）租约行为、多个 AEDT 实例的许可证竞争。
这些属于部署验收，需要至少两台机器与许可证确认。

## 6. 与 yadof distributed 的差异

| 维度 | yadof | 本实现 |
|---|---|---|
| 调度器 | HTCondor | 自带 HTTP + 租约（无集群依赖） |
| 回传物 | `rawData.zip`、`individual_metadata.json`、`batch.log` | 指标/状态/run_id（原始证据留在节点本地 campaign） |
| 资源标定 | CPU/内存/磁盘自动标定 | 未做（每节点单任务、`--max-tasks` 限流） |
| 适合场景 | 有 HTCondor 集群 | 2–5 台工作站、无集群运维 |
