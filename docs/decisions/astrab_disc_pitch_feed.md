# AstraBB 盘环距与馈源协同参数化（2026-11-18）

## 背景

复现 AstraBB（阶梯双锥同轴馈源 + 70 mm 波纹盘）时，装盘后 24–32 GHz 带宽只有 3.0 GHz。A/B 实测确认馈源本身全带 8 GHz，问题在馈源-盘内孔耦合，且 32 GHz 缺口与盘周期结构有关。

## 决策

1. 构建器新增 `disc_inner_radius_mm`（盘内孔覆盖）、`outer_radial_scale`（馈源外段独立缩放）、`disc_pitch_scale`（均匀环距缩放）、`disc_pitch_gradient`（环距随半径渐变），均默认 1.0/0.0 保持原几何不变；
2. 采用**每变体独立 AEDT** 的测量纪律（campaign 工程名冲突会静默复用旧工程）；
3. 全带未达成的结论：短时间不采用改盘外圈或加吸收材料等更重结构，先把 7.0 GHz 版与 5.09 dBi 增益版作为两条可行折中交付，后续建议局部渐变（外圈 3–5 环）再试。

## 证据

- `docs/evidence/feed_disc_cotuning.json`、`docs/evidence/final_24_32_acceptance.json`；
- 本机 AEDT 2026.1 实测，12 步收敛、MaxDeltaS 0.02。
