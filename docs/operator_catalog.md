# 渲染层算子目录（`antopt/parametric/aedt.py`）

这份目录由 `scripts/gen_operator_catalog.py` 生成，测试会校验它与代码一致。

## 结论先说：新天线能不能只加 `cases/`？

* **能**——如果它需要的几何原语都已在下表里：只写 `cases/<name>.py`（case + model + 注册），
  核心与渲染层都不用改；
* **不能**——如果它需要新的几何原语（例如本项目的漏波天线需要“长方体”和“渐变单元串”），
  就要在渲染层加一个**通用算子**（带参数校验与单测），再写 `cases/`。

实际情况：加漏波天线时新增了 `create_box`、`create_tapered_units` 两个通用算子，
所以严格意义上不是“零改动”，而是“**两个通用算子 + 一个插件文件**”，核心逻辑未动。

## 算子表

| 算子 | 作用 | 主要参数 |
|---|---|---|
| `add_inner_sleeve` | 内导体套筒（先扩介质孔再插 PEC） | radius, z0, length, tag |
| `add_step_ring` | 在喇叭口加接地环（PEC 环，unite 进同轴+下锥） | inner_radius, outer_radius, height, shift, target |
| `add_transition_cone` | 加锥形过渡段 | r_bottom, r_top, height, shift, cone_name, target |
| `backup_object` | 复制对象做备份（unite 会吸收对象时用） | name, suffix |
| `create_box` | 建长方体（波导/脊/域） | name, origin, sizes, material |
| `create_cylinder` | 建圆柱（PEC/介质/真空） | name, origin, radius, height, material |
| `create_tapered_units` | 建一串渐变单元（漏波天线脊） | count, pitch, h0, width, depth, base_y, name |
| `delete_if_present` | 删除指定对象（幂等：不存在也不报错） | names: list[str] |
| `gap_bowls` | 按盘面位置摆放上下两碗（tip_offset） | tip_offset_mm, disc_offset_mm |
| `restore_object` | 从备份复制回原对象（恢复被吸收的对象） | name, suffix |
| `revolve_profile` | 闭合折线绕 Z 旋转成实体（变量驱动几何的基础） | points [[x,z]...], name, angle, material |
| `scale_objects` | 按名字缩放对象 | names, scale |
| `scale_xy_about_axis` | 绕 Z 轴缩放半径（盘口径） | name, factor |
| `scale_z_about_centre` | 绕自身中面缩放厚度（盘厚） | name, factor |
| `set_wedge` | 把对象设为扇区（周期模型） | name, angle, start |
| `shift_cones` | 整体平移上下锥 | dz |
| `shift_objects` | 平移一组对象 | names, dx, dy, dz |
| `subtract` | 布尔减（挖孔/开槽） | target, tools, keep_originals |
| `validate_simple` | 调用 HFSS validate_simple（带重试） | attempts, delay_s |

## 各 case 用到哪些算子

| case | 使用的算子 |
|---|---|
| `lens28_ref` | 盘缩放、碗位、接地环、第二环、内导体套筒 |
| `lwa` | 波导盒、渐变脊（create_box + create_tapered_units） |

## 新增算子的检查清单

1. 在 `KNOWN_OPERATIONS` 注册；
2. 在 `validate_operations` 里校验参数（缺参/越界要报 `OperationError`）；
3. 在 `SnippetRenderer` 里生成幂等的 AEDT 脚本（重复执行不破坏对象名）；
4. 加单测：合法参数、非法参数、生成代码包含关键调用；
5. 跑本脚本重新生成目录，并让 `tests/test_operator_catalog.py` 通过。
