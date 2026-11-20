# 测量结果与方向图报告的保留策略（2026-11-19）

用户问题：**"Result 文件会定期删除吗？还是一直留着？"** 以及要求：既然结果一直占地方，就把
3D/E/H/EH 四张方向图直接建进 Report，并且不要每次靠对话解决，要写进系统。

## 1. 结论：结果不会被“定期删除”，但工程副本会被下一次运行覆盖

代码里的事实（`aster/campaigns.py`）：

- **受保护、不做清理**：`campaign.sqlite`、`queue.sqlite`、`real_runs.jsonl`、`design_spec.json`、
  `dry_runs.jsonl`、**`projects/`**（含工程副本和它的 `.aedtresults` 解）。
- `retention_plan()` / `apply_retention()` 只处理 `runs/<id>` 下的**旧运行目录**，默认保留最近 5 次，
  而且 **`apply=False` 时只出计划、不删**；真正删除必须显式执行 `scripts/manage_campaigns.py ... apply`。
- 因此：**账本与解不会被自动删除**；只有旧的运行产物（touchstone、远场 CSV、PNG 等）可以被显式清理。

但有一个容易误解的行为：`RealRunManager` 默认 `reuse_project=False`，**每次测量都会把源工程重新
复制成 `campaign/projects/lens28_var.aedt`**。所以：

- “上一次测量”的解/Report 会被这次的新副本替换，工程里永远只有“最近一次”的解；
- 历次运行的交付物（`result.s1p`、`farfield/*.csv`、`native_reports/*.png`）留在 `runs/<id>/artifacts/`，
  它们才是历史证据；
- 如果希望工程里保留连续多次的解，设置 `reuse_project=True`；代价是每次运行要复用同一工程。

## 2. 系统改动：每次成功测量后自动保留四张方向图（代码，不靠对话）

`aster/farfield.py` 新增 `persist_pattern_reports()`，由 `RealRunManager` 在 POSTPROCESS 阶段、
已验收的测量完成后自动调用（`keep_pattern_reports=True` 默认开启）：

1. 在**已求解的工程副本**里重建并 **保存** 四个 28 GHz Report（不再像 capture 阶段那样删除）：
   - `ASTER_3D_Gain_28GHz`（3D Polar Plot）
   - `ASTER_E_Plane_28GHz`（Radiation Pattern，φ=0°）
   - `ASTER_H_Plane_28GHz`（Radiation Pattern，θ=90°）
   - `ASTER_EH_Overlay_28GHz`（E/H 两切面叠在同一极坐标图）
2. 把四张 PNG 导出到 `<campaign>/pattern_reports/<run_id>/`（持久，不受下一次工程覆盖影响；
   也可用 `pattern_reports_dir` 改根目录）；
3. 写 `<campaign>/pattern_reports/<run_id>/persist_result.json`（报告名、图片、错误），
   并在工程旁生成可双击的 `*_鏌ョ湅鏂瑰悜鍥?cmd` 重开入口；
4. 结果同时写入运行账本的 `pattern_reports` 字段，测量失败或 COM 失败时只记录错误、**不影响已验收的指标**。

这样同时满足两条：**工程里能看到四个 Report**，且**即使工程被下一次副本覆盖，也有 PNG+manifest 作为持久证据**。
旧的“capture 阶段创建后立即删除”保留原样（避免 PyAEDT 后处理枚举到无法解析的 report 而刷错误），
删除之后紧接着由 persist 步骤重建并保存。

配置入口：`RealRunManager(keep_pattern_reports=False)` 可关闭；`pattern_reports_dir` 可指定目录。

## 3. 验证

- `tests/test_pattern_reports.py`：生成代码包含 `keep_reports=True` 与四个 report 名前缀；
  manifest 写入、bridge 失败不抛出、`RealJob.pattern_reports` 账本往返、manager 默认开关。
- 现场验证：对已完成的 E4 campaign 副本执行 `astra_persist_reports.py`，四个 Report 已保存，
  PNG 在 `<workspace>\astra_codesign\pattern\E4_reports\`。
