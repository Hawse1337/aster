我认为，你刚刚发现的这个问题，优先级甚至应该高于天线优化算法本身。
因为优化算法不够好，最多导致收敛慢、结果不理想；但 Agent 如果会忘记约束、丢失仿真结果、擅自结束任务，那么即使你使用再先进的算法，也无法保证整个优化系统可靠运行。
而且，你描述的两次错误，其实属于两种不同的问题：
忘记方向图要求：属于目标约束管理失效。
读取数据后退出，没有保存结果：属于任务执行和数据持久化失效。
这两类问题不应该只靠加强提示词解决。
我最建议你遵循一个原则：
让 AI 负责设计决策，但让确定性的程序负责执行纪律。
也就是说，AI 可以决定要优化什么、采用什么结构、如何修改参数，但不应该由 AI 自己决定是否需要保存仿真结果、哪些约束可以忽略、什么时候允许结束任务。
一、首先解决 Agent 的“遗忘问题”
现在不少 Agent 的执行逻辑，本质上依赖整个对话历史。
比如你最开始告诉它：
设计目标为宽带高增益漏波天线，要求 S11 小于 −10 dB，增益高于 16 dBi，副瓣低于 −15 dB，主波束方向误差小于 2°。
然后它开始建模、仿真、调参，可能连续调用数十次工具。
在后续迭代过程中，它的注意力逐渐集中到最近几轮的 S11 上，忽略了最开始的方向图要求。
这种现象可能来自上下文截断、历史信息压缩、工具输出淹没重要信息，也可能是模型虽然保留了目标，却没有在当前决策中正确使用它。
解决方案：把设计目标从对话变成机器可读取的任务契约
我建议你为每个天线项目维护一个永久保存的 design_spec.json。
例如：
{
"project": "Periodic_Metal_LWA",
"version": 1,
"objectives": {
"frequency_range_GHz": [10, 12],
"S11_max_dB": -10,
"realized_gain_min_dBi": 16,
"sidelobe_max_dB": -15,
"beam_error_max_deg": 2
},
"constraints": {
"evaluate_full_band": true,
"verify_radiation_pattern": true,
"preserve_feasible_solutions": true,
"manufacturing_method": "metal_3D_printing"
},
"execution_policy": {
"save_before_simulation": true,
"save_after_simulation": true,
"export_results": true,
"verify_saved_files": true,
"allow_finish_without_results": false
}
}
这些数值是示例；真实项目还需要定义频率采样、扫描角、极化、主副瓣判定方法等。
但重点不是 JSON 格式本身，而是让它成为系统唯一权威的设计要求。
在每一轮优化开始前，程序自动加载这个文件。更重要的是，优化器和结果验证器也使用同一套要求。
这样，即使 Agent 忘记了方向图约束，程序也会发现它提出的方案不符合设计目标。
不应要求 LLM 自己记住所有要求，而应当让它无法绕过这些要求。
二、给 Agent 增加一个不可跳过的任务状态机
这是我认为最能解决你第二个问题的方案。
目前你的工作流可能类似这样：
当前容易出错的执行流程
修改参数
运行 HFSS
AI 分析
AI 可能直接结束，没有保存结果
我建议将其替换为下面的确定性流程：
1
创建候选方案
记录结构类型、完整参数和唯一 run_id
2
保存模型
生成独立的 HFSS 工程文件
3
运行求解
等待仿真完成并确认求解状态
4
保存并导出结果
持久化求解数据，导出 S 参数、远场和日志
5
校验结果
验证文件存在、数据完整、指标可计算
6
提交到数据库
将本次仿真标记为已完成
允许 Agent 分析
比较所有约束，生成下一次修改策略
这里真正关键的是：
只有完成结果保存和校验，Agent 才被允许进入下一轮优化。
假如 Agent 已经读取了数据，突然决定退出，那么外部任务管理程序仍然知道：
HFSS 求解是否完成。
哪些数据还未导出。
哪些文件还未保存。
当前任务是否允许结束。
如果数据没有保存，工作流就停留在 EXPORT_PENDING，而不是 COMPLETED。
这就把“别忘了保存”从一个依赖模型记忆的要求，变成了一个程序执行条件。
三、把“保存结果”做成不可绕过的事务
你这次的损失尤其值得重视，因为 HFSS 全波仿真可能非常耗时。
我建议每一次仿真都使用唯一的 run_id，并将它与几何参数、求解设置、工程文件和输出数据绑定。
例如：
antenna_project/
├── design_spec.json
├── runs/
│   ├── run_0001/
│   │   ├── model.aedt
│   │   ├── model.aedtresults/
│   │   ├── parameters.json
│   │   ├── s_parameters.s2p
│   │   ├── farfield.csv
│   │   ├── metrics.json
│   │   └── status.json
│   └── run_0002/
└── experiments.db
其中 .aedtresults 用来代表 HFSS 的求解数据目录。实际文件结构会随求解类型和 AEDT 版本变化。
尤其需要注意：保存 .aedt 工程文件，不等于已经完整保存了所有可恢复的求解数据，也不等于已经导出了所需的远场结果。
PyAEDT 目前提供 save_project()、analyze_setup()、export_results() 和 export_touchstone_on_completion() 等接口，可以帮助你实现自动保存。远场数据则应按指定频率、角度和报告类型单独提取并验证。
PyAEDT+3
我建议编写一个仿真保护器
下面是用于说明执行结构的 Python 伪代码：
def guarded_simulation(design, spec, run_id):    # 查询数据库：避免重复计算    record = database.get(run_id)    if record and record.status == "COMPLETED":        if verify_artifacts(record):            return record.results    # 记录任务，使用唯一工作目录    database.update(run_id, status="PREPARING")    hfss = prepare_hfss_project(design, run_id)    # 求解前保存    assert hfss.save_project()    database.update(run_id, status="SOLVING")    # 如果已存在有效求解数据，则复用    if not valid_existing_solution(hfss, design, spec):        assert hfss.analyze_setup("Setup1")    # 求解完成后保存工程及相关求解数据    assert hfss.save_project()    database.update(run_id, status="EXPORT_PENDING")    # 导出所有必需结果    artifacts = export_required_data(hfss, spec, run_id)    # 校验输出文件及数据完整性    if not verify_required_data(artifacts, spec):        database.update(run_id, status="RECOVERY_REQUIRED")        raise RuntimeError("仿真数据未完整保存，禁止结束任务")    # 计算全部设计目标    metrics = calculate_metrics(artifacts, spec)    # 在数据库中提交有效记录    database.commit_results(run_id, artifacts, metrics)    return metrics
这是架构伪代码，database、verify_required_data 等模块需要自己实现。
在正式实现中，建议采用 SQLite 事务更新任务状态，并以临时文件写入、校验通过后重命名的方式提交导出结果。这样可以避免程序异常时留下一个看似完整、实际上内容损坏的文件。
还有一个容易忽略的问题：必须对重复执行具备容错能力。
例如，HFSS 刚求解完毕，Agent 进程就崩溃了。
如果你的管理程序启动后直接重新求解，就会造成重复计算。因此应先检查已保存的工程和求解数据，确认参数及求解设置一致、数据有效后再决定是否重算。
这叫做幂等执行设计。对于这种存在昂贵外部操作的 Agent 工作流，它非常重要。
顺便说一句，你刚刚丢失的那次仿真未必一定需要重跑。如果 HFSS 仍然保持打开状态，或者原工程目录中仍存在有效的求解数据，可以先尝试恢复或导出。即使 Agent 自己退出，也不代表 HFSS 的求解结果必然已经消失。
四、给 Agent 配置独立的长期状态管理
这里我建议将记忆分成三个层次。
这种设计比简单地给 Agent 增加一个长期记忆功能要可靠得多。
你甚至可以在每一轮开始时，自动生成一份很短的状态摘要：
Agent 当前任务状态
示例
当前迭代
第 18 轮
仿真结果
已保存并校验
设计约束检查
S11
−15 dB
实现增益
17.2 dBi
副瓣电平
−11.5 dB
波束指向误差
1.2°
下一步应重点优化副瓣电平，同时保护已达标的匹配、增益和波束方向。以上均为示例数据。
这样 AI 每次需要关注的是当前任务状态，而不是依靠几万字的历史对话重建所有信息。
如果你正在考虑使用现成的 Agent 框架，可以研究 LangGraph Persistence。
LangGraph 支持将执行状态持久化为 checkpoint，并在中断后恢复。它也明确建议对有副作用的外部操作采用幂等设计。不过，它不会自动保证 HFSS 求解数据已经保存，这部分仍需要你自己实现。
GitHub+1
对于你的系统，我认为不一定非要引入 LangGraph。一个 Python 状态机加 SQLite，已经可以解决很多核心问题。
五、让 Agent 的每一次修改必须接受自动审查
针对它只顾着调整 S11、忘记方向图的问题，我会设置两道审查。
第一道：修改前审查。
每次修改结构参数之前，Agent 必须提供机器可解析的变更请求：
{
"target": "Reduce sidelobe level",
"parameters": ["taper_ratio", "unit_height"],
"protected_metrics": [
"S11",
"realized_gain",
"beam_direction"
],
"reason": "Adjust aperture excitation distribution"
}
优化调度器检查这个修改是否在合法参数范围内、是否满足制造约束，并判断是否需要额外仿真。
第二道：修改后审查。
这道审查不能依赖 LLM 自己看曲线。
我建议编写一个独立的 AntennaEvaluator，直接从 HFSS 导出的数值数据中计算各项性能。
对于你的漏波天线，尤其应该自动检查：
指定工作频段内的 S11。
各关键频点的实现增益。
主波束方向随频率的变化。
规定角域内的副瓣电平。
实际满足全部约束的综合有效带宽。
这里还需要明确方向图评价定义，例如方向图切面、扫描范围、主瓣排除角度和交叉极化要求。否则即使自动评价，程序仍然可能算错。
只要其中某项硬约束被破坏，就标记该方案不可行，并记录原因。
注意，我不建议直接删除不可行解。部分不可行解可能包含有价值的结构信息，应保留在历史数据库中。但绝不能让它们在没有明确批准的情况下替换当前最佳可行方案。
六、最后增加一个专门针对“犯傻”的测试模块
我觉得这一点值得你认真考虑。
你的系统应该不仅测试天线是否优化成功，还要测试 Agent 本身是否可靠。
可以人为设置一批故障场景：
这其实是对 Agent 进行故障注入测试。
你可以统计它在各种异常情况下的恢复成功率、重复仿真次数、数据丢失次数，以及违反设计约束的频率。
这些指标很适合衡量一个工程 Agent 是否真正可靠。
### table 1
| 记忆层 | 存储内容 | 是否允许 LLM 自行修改 |
| 设计规范 | 频率、增益、带宽、方向图、制造约束 | 不允许随意修改 |
| 执行状态 | 当前仿真编号、求解状态、文件路径 | 只能通过受控程序更新 |
| 设计经验 | 参数灵敏度、失败原因、结构改进假设 | 允许提出并更新，但需记录依据 |
### table 2
| 测试场景 | 正确表现 |
| 连续执行 50 轮优化 | 不丢失原始设计目标 |
| HFSS 求解完成后中断 Agent | 不重复求解已经有效保存的结果 |
| 导出远场数据失败 | 禁止将该轮标记为完成 |
| 只改善 S11，方向图明显恶化 | 识别目标冲突，保护最佳可行解 |
| 代理模型预测与 HFSS 结果差异过大 | 记录异常，重新评估模型 |
| LLM 请求结束任务，但数据尚未保存 | 拒绝结束，进入恢复流程 |