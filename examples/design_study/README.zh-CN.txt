ASTER 离线天线设计研究示例（透镜配置 revision 2，漏波配置 revision 1）

快速回放

在装有本交付版 ASTER 的 Python 环境中打开此目录，执行以下命令。第一行使用主 CLI 的 design-study 子命令，第二行使用等价的 Python 模块入口。两条命令都把下一批建议和预计耗时打印为 JSON。

python -m aster design-study --profile profile-lens.json --history empty-history.json --budget-seconds 2400 --count 2 --seed 7
python -m aster.design_study --profile profile-lwa.json --history empty-history.json --budget-seconds 2400 --count 2 --seed 7

内部 antopt 包的模块名为 antopt.design_study。命令末尾加 --output study-plan.json 可将结果写入新文件。两份配置的空历史均返回两项 geometry 建议，trained_on 为 0，额定费用分别合计 2400 秒、1800 秒。empty-history.json 只含空的 runs 和 pending_action_ids。此时没有已经验证的设计、带宽或增益结果。

如何读两份配置

profile-lens.json 使用已注册的 lens28_ref。在 24–30 GHz 搜索至少 3 GHz 宽的连续采样窗口，门限 S11 ≤ −10 dB，预留 0.5 dB 匹配裕量，相邻样点最大间距 0.5 GHz。水平覆盖要求是在 26、28、30 GHz 各自的 Global 坐标系 θ=90°、φ 完整一周，最低总实现增益为 3 dBi，单频环内最大起伏为 2 dB，方位采样间距至多 10°。窗口匹配和三个固定频点的覆盖分别验收。在已测名义点周围，同时改变环外半径 ring_outer（注册范围 1.8…5.0 mm，交互步长 ±0.2 mm）和环的轴向偏移 ring_shift（注册范围 −0.6…0.6 mm，交互步长 ±0.1 mm）。四个角点均有完整求解后可读交互对比值。单独的制造容差研究检查各自 ±0.05 mm 和 ±0.03 mm 的组合角点以及名义点。

profile-lwa.json 使用已注册的周期漏波天线 lwa。25–31 GHz 全部目标频带使用相同的 S11 门限、0.5 dB 裕量和采样间距。工作频点选为 26.5、28、29.5 GHz，区域为 Global 坐标系 θ=60°…80°、φ 从 330° 经 0° 至 30°，区域内实现增益至少 4 dBi，θ 和 φ 间距均不超过 5°。此 profile 未额外限制扇区内的起伏。case 原本的 realized_gain_h 和 ripple 等约束仍独立参与判定。p_mm 与 h_mm 的交互步长为 0.2 mm 和 0.1 mm，各轴公差分别为 ±0.05 mm 和 ±0.04 mm。容差场景逐轴扰动并保留名义点。

两个 profile 的覆盖方向、阈值、公差和 cost_seconds 都是示例选择。应按实际需求、坐标定义、工艺和候选类型的历史耗时更改。cost_seconds 是每种动作的估计耗时，命令里的 budget-seconds 限制选出动作的预计总耗时。角域指标针对要求的已求解频率和方向。有限样点上的达标与制造良率是不同的结论。

接入实际候选

1. 选择守卫已完成、已确认收敛的高保真 run。手工整理 --history 时，每条求解证据必须明确标记 source 为 MEASURED，状态为 COMPLETED。保留原始 run_id、case、完整 params、metrics、实际 fidelity 和原有 learning_contract。MEASURED 在此指实际求解导出，来源须可追溯。将 profile 的 recipe_id 改为历史真实的 learning_contract.identity，若记录另带 recipe_id，两者必须一致。示例中的 recipe_id 只用于空历史演示。改变网格、端口或工艺流程时，应保留各自的配方和来源。

2. 在同一次已完成求解中导出 S11 Touchstone，例如 50 Ω 的 .s1p，以及绝对总实现增益的原生长表 CSV。导出远场前核对候选、激励、求解设置/扫频、远场球坐标系及坐标定义为 Theta-Phi。CSV 需要 Freq [GHz]、Theta [deg]、Phi [deg]、dB(RealizedGainTotal) [] 四列。频率还支持 Hz/kHz/MHz，角度支持 rad。每个频点保留覆盖要求范围的矩形网格和边界角度。整圈可带与起点相同的闭合列。方向性、普通 GainTotal、归一化曲线与展示图片均不足以给定绝对实现增益下限。已有只保存普通增益的项目，应从对应已保存的解重新导出实现增益。带宽复核需要原始频率点和 S 参数，21 条参考设计的指标摘要不能还原这些曲线。

3. CSV 通常缺少坐标系。为实际 CSV 建立同名加 .meta.json 的 UTF-8 侧文件。以下 PowerShell 从真实记录和导出物生成摘要。请把路径替换为现场文件，并把 $cs 改为该次实际记录的坐标系。source 的 MEASURED 在本流程表示来自实际求解导出。

$run = Get-Content -Raw '.\actual-completed-run.json' | ConvertFrom-Json
$csv = Get-Item -LiteralPath '.\actual-run\coverage.csv'
$cs = 'Global'
@{sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $csv.FullName).Hash.ToLowerInvariant(); coordinate_system=$cs; angle_definition='Theta-Phi'; source='MEASURED'; quantity='dB(RealizedGainTotal)'; run_id=$run.run_id} | ConvertTo-Json | Set-Content -Encoding utf8 -LiteralPath ($csv.FullName + '.meta.json')

也可直接提供记录了上述 quantity、source、coordinate_system 和 run_id 的真实快照 JSON，samples 的每行依次为 GHz、θ 度、φ 度、实现增益 dBi。多个文件共同覆盖工作频点时应给出相同的 run_id。若 CSV 变更，需从真实导出信息重新核对并生成匹配的侧文件。

4. 新建 history-real.json，将记录放入 runs 数组。每条记录的 touchstone 对象须含 path、run_id 和 sha256。path 和 coverage_files 中各项均相对 history-real.json，run_id 应等于实际求解 ID。已有内嵌 s11_points 的记录要选择一种 S11 来源。以下示例使用 S1P 文件。

$s1p = Get-Item -LiteralPath '.\actual-run\result.s1p'
$curve = @{path='actual-run/result.s1p'; run_id=$run.run_id; sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $s1p.FullName).Hash.ToLowerInvariant()}
$run | Add-Member -Force -NotePropertyName touchstone -NotePropertyValue $curve
$run | Add-Member -Force -NotePropertyName coverage_files -NotePropertyValue @('actual-run/coverage.csv')
@{runs=@($run); pending_action_ids=@()} | ConvertTo-Json -Depth 80 | Set-Content -Encoding utf8 '.\history-real.json'

把快速回放命令中的 empty-history.json 换成 history-real.json。检查 assessments 的原有 case 判定、S11 频率间隙、最差增益方向、各频点起伏及缺失证据。nominal_front 与 tested_tolerance_front 分别依赖名义设计和已测容差场景。空的前沿也可能反映证据缺失、尚无达标设计或历史与 profile 配方不匹配，可查看 excluded 和逐点判据。

5. 若实际运行记录已在本机 campaign 中，--campaign 可直接读取对应的事件、账本和同一 run 的导出物。以下两条命令中的目录应替换为真实 campaign 根目录，profile 中应填既有守卫配方。先查看只读计划。如需将本批可执行的几何候选（geometry、interaction）写入该 campaign 的本地工作队列，再执行第二条。

python -m aster design-study --profile profile-lens.json --campaign "C:\path\to\campaign" --budget-seconds 2400 --count 2 --seed 7
python -m aster design-study --profile profile-lens.json --campaign "C:\path\to\campaign" --budget-seconds 2400 --count 2 --seed 7 --enqueue-geometry

查看结果中的 queue.enqueued 和 queue.skipped。入队需要已有匹配的 approved-builder 学习契约。后续仍由受管 worker 对候选副本执行预检、求解、收敛检查和导出。补频、补角度和独立公差复验应按 action 中的 run_id、频率或场景通过相应的受管测量流程安排。上一份审计修复包中的 p0_lens_dimensions 示例属于 lens2g4_10g，lens28_ref 和 lwa 需要各自匹配的 builder、获准模板/设计、变量范围和输出目录。遇到 approval_required 时，把该次请求交给操作员审批。手工整理 --history 可将已在运行的动作 ID 列入 pending_action_ids，供下次规划查重。
