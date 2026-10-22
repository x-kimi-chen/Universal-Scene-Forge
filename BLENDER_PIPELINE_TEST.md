# Blender 全流程运行证据（BLENDER_PIPELINE_TEST.md）

- 测试对象：`universal_scene_forge/` 当前工作树（本地源码为唯一事实来源）
- 测试时间：2026-10-21 ~ 2026-10-22
- 测试环境：Windows 10/11（PowerShell）；审计 venv `build/audit/venv`（Python 3.14.6）
- Blender：Steam 版 `D:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe`
- 结论：Blender 阶段在无头模式下 **多轮成功（含全格式、USD 输入、EXE 冻结环境）+ 1 轮边界失败**；源环境与 EXE 端到端均 **退出码 0**，全部格式产物可被 Blender 重新导入。

---

## 1. Blender 版本与调用方式

### 1.1 版本（已实测）

```
$ & 'D:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe' --version
```

落盘：`build/audit/blender_version.log`

```
Blender 5.2.2 LTS
    build date: 2026-09-15
    build time: 01:37:04
    build hash: d13f752e3b9c
    build branch: blender-v5.2-release
    build platform: Windows
    build type: Release
```

### 1.2 子进程封装（与既有阶段容错对齐）

| 关注点 | 实现位置 | 行为 |
|---|---|---|
| 可执行文件解析 | `app/dcc/blender_bridge.py:61` | `resolve_executable` 拒绝相对路径/CWD 同名 exe |
| 参数传递 | `app/dcc/blender_bridge.py:67-70` | 写临时 JSON，避免命令行长度与转义问题 |
| 命令构造 | `app/dcc/blender_bridge.py:72-78` | 列表形式 `[blender.exe, --background, --factory-startup, --python, script, --, params.json]`，**无 `shell=True`** |
| 完整命令日志 | `app/dcc/blender_bridge.py:80` | `log.info("Blender 命令: %s", cmdline)`，含引号与全部参数 |
| 环境隔离 | `app/dcc/blender_bridge.py:83-86` | `subprocess.Popen(..., env=scrubbed_env())`，剥离 `PYTHONPATH`/`PYTHONHOME` 并收缩 `PATH` |
| 超时与进程树 | `app/dcc/blender_bridge.py:91-114`、`:197-205` | 轮询 + `_kill_tree`（`system_tool("taskkill.exe")`），超时抛 `BlenderBridgeError` |
| 退出码/耗时 | `app/dcc/blender_bridge.py:121` | `Blender 退出码 %d, 耗时 %.1fs, 声明产物 %d 个` |
| 产物校验 | `app/dcc/blender_bridge.py:126-140` | 退出码 0 但产物缺失/0 字节仍判失败（M-04） |
| 脚本侧上下文覆盖 | `scripts/blender/mesh_to_fbx.py:83-95` | `bpy.context.temp_override(...)`；旧版 Blender 退化直接调用 |
| 脚本侧状态重置 | `scripts/blender/mesh_to_fbx.py:101-125` | `reset_export_state()` + `state_snapshot()`，每次导出后比对选择/活动对象/UV/变换 |
| 导出调用 | `scripts/blender/mesh_to_fbx.py:399-411`（FBX）、`:414-430`（OBJ，`bpy.ops.wm.obj_export`）、`:452-462`（GLB，`bpy.ops.export_scene.gltf`）、`:464-467`（BLEND）、`:471-507`（USD，`bpy.ops.wm.usd_export` + RNA 参数探测） | Blender 4.0+ API |

脚本入口：`scripts/blender/mesh_to_fbx.py:510` `main()`；完成/失败标记 `:613` `[USF] DONE`、`:620` `[USF] FATAL`。

调用形态（全部无头，无 GUI 依赖）：

```powershell
& $blender --background --factory-startup --python scripts\blender\mesh_to_fbx.py -- <params.json>
```

`params.json` 字段：`input`、`out_dir`、`base_name`、`formats`、`lod_ratios`、`bake_texture`、`bake_ao`（示例：`build/audit/runs/run4/params.json`）。

---

## 2. 轮次与实测结果

### Round 1：直调 Blender（夹具 sphere.obj，捕获退出码）

- 输入：`build/audit/fixtures/sphere.obj`（266 顶点 / 288 面）
- 参数：`build/audit/runs/run4/params.json`，格式 `fbx,obj,glb,blend,usd`，LOD `1.0,0.5`
- 命令：

```powershell
$exe='D:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe'
& $exe --background --factory-startup --python 'scripts\blender\mesh_to_fbx.py' -- 'build\audit\runs\run4\params.json'
```

- **实测退出码：`BLENDER_EXIT=0`**
- 产物（非零字节）：

| 文件 | 字节 |
|---|---:|
| `usf_scene.fbx` | 25,724 |
| `usf_scene.obj` | 36,582 |
| `usf_scene.glb` | 39,696 |
| `usf_scene.usda` | 73,978 |
| `usf_scene.blend` | 111,453 |
| `usf_scene_lod1.fbx` | 22,396 |
| `usf_scene_lod1.obj` | 20,011 |
| `usf_scene_lod1.glb` | 23,256 |
| `usf_scene_lod1.usda` | 42,803 |

- 再导入校验（`build/audit/validate_imports.py`，独立 Blender 进程）：

```
$ & $exe --background --factory-startup --python 'build\audit\validate_imports.py' -- 'build\audit\runs\run4\validate_args.json'
VALIDATE_EXIT=0
[CHECK] OK .../usf_scene.fbx objects=1 verts=266 faces=288
[CHECK] OK .../usf_scene.obj objects=1 verts=266 faces=288
[CHECK] OK .../usf_scene.glb objects=1 verts=1104 faces=528
[CHECK] OK .../usf_scene.usda objects=1 verts=266 faces=288
[CHECK] OK .../usf_scene.blend objects=3 verts=666 faces=764
[CHECK] OK .../usf_scene_lod1.fbx objects=1 verts=134 faces=188
[CHECK] OK .../usf_scene_lod1.obj objects=1 verts=134 faces=188
[CHECK] OK .../usf_scene_lod1.glb objects=1 verts=640 faces=264
[CHECK] OK .../usf_scene_lod1.usda objects=1 verts=134 faces=188
```

日志：`build/audit/runs/run4/blender_stdout.log`、`build/audit/runs/run4/validate.log`。

### Round 2：经 BlenderBridge 调用（同一夹具）

```powershell
& 'build\audit\venv\Scripts\python.exe' build\audit\bridge_run.py `
  build\audit\fixtures\sphere.obj build\audit\runs\run2\out `
  'fbx,obj,glb,usd,blend' '1.0,0.5' usf_scene `
  'D:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe' `
  *> build\audit\runs\run2\harness.log
```

日志摘录（`build/audit/runs/run2/harness.log:2,77-79`）：

```
22:53:36 [INFO   ] USF.BLENDER: Blender 命令: "D:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe" --background --factory-startup --python ...\scripts\blender\mesh_to_fbx.py -- C:\Users\<user>\AppData\Local\Temp\tmp_ovdigoc.json
22:53:37 [INFO   ] USF.BLENDER: Blender 退出码 0, 耗时 1.6s, 声明产物 9 个
22:53:37 [INFO   ] USF.BLENDER: Blender 完成, 有效产物 9 个: usf_scene.fbx, usf_scene.obj, usf_scene.glb, usf_scene.usda, usf_scene_lod1.fbx, usf_scene_lod1.obj, usf_scene_lod1.glb, usf_scene_lod1.usda, usf_scene.blend
[HARNESS] OK [...9 个路径...]
```

### Round 3：经 BlenderBridge 调用（输入为 Round 1 的 FBX，验证多格式副作用）

```powershell
& 'build\audit\venv\Scripts\python.exe' build\audit\bridge_run.py `
  build\audit\runs\run1\out\usf_scene.fbx build\audit\runs\run3\out `
  'obj,glb,usd,blend' '1.0,0.5' reimported `
  'D:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe' `
  *> build\audit\runs\run3\harness.log
```

- **退出码 0，耗时 1.6s，7 个产物**
- 每次导出后状态检查均通过（`build/audit/runs/run3/harness.log:24,34,37,42,50,53`）：

```
blender| [USF] obj 导出后状态一致（选择/活动对象/UV/变换无副作用）
blender| [USF] glb 导出后状态一致（选择/活动对象/UV/变换无副作用）
blender| [USF] usd 导出后状态一致（选择/活动对象/UV/变换无副作用）
```

- 再导入校验 `build/audit/runs/run3/validate.log`：OBJ / GLB / USDA / BLEND 全部 `[CHECK] OK`，`VALIDATE_EXIT=0`。

### 边界轮：零面网格（空实体）

- 输入：`build/audit/fixtures/points_only.ply`（100 顶点 / 0 面）
- 日志（`build/audit/runs/run_edge/harness.log:8-13`）：

```
blender| [USF] 网格统计: 100 顶点 / 0 面 / 松散顶点 100 / 非流形边 0
blender| [USF] FATAL 网格为 0 面（仅点/边，无法导出实体网格）；请检查 MESH 阶段产物
22:54:08 [INFO   ] USF.BLENDER: Blender 退出码 1, 耗时 0.6s, 声明产物 0 个
[HARNESS] FAIL BlenderBridgeError: Blender 脚本未完成 (退出码 1): ...
```

结论：脚本给出可读错误而非崩溃；Blender 退出码 **1**；桥接层抛 `BlenderBridgeError`，符合“阶段失败但流水线可继续”的 `_safe()` 语义。

### 端到端 E2E（真实 USF CLI）

命令（cwd=`universal_scene_forge`）：

```powershell
$env:PYTHONUNBUFFERED='1'
& 'D:\AI_MADE\MADE_one_kaiyuan_Github_Trea\universal_scene_forge\build\audit\venv\Scripts\python.exe' `
  main.py --cli --input 'build\audit\e2e\input_frames' `
  --output 'build\audit\e2e\work_final2' `
  --model 'build\audit\e2e\model\point_cloud\iteration_7000\point_cloud.ply' `
  --accept-3dgs-license *> 'build\audit\e2e\cli_stdout_final2.log'
```

- **实测退出码：`EXIT=0`**
- 输入夹具：`build/audit/e2e/input_frames`（12 张图，`build/audit/e2e/make_e2e_fixture.py` 生成）；模糊帧剔除后 9 帧（`cli_stdout_final2.log:24,34`）
- 3DGS 替代：`--model` 注入上游格式点云 PLY（16000 高斯），**不执行 Inria 训练代码**；网格重建 906 顶点 / 799 面（`:43-45`）
- Blender 后处理：退出码 0，耗时 1.8s，13 个产物（`:186-188`）
- 导出阶段再次调用 Blender：退出码 0，耗时 1.7s，13 个产物（`:193-198`）
- 日志：`logs/usf_20261021_232640.log`（6,757 字节），含两次完整命令、退出码、耗时

Blender 命令与关键日志（`cli_stdout_final2.log:61,186`）：

```
23:26:43 [INFO   ] USF.BLENDER: Blender 命令: "D:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe" --background --factory-startup --python ...\scripts\blender\mesh_to_fbx.py -- C:\Users\<user>\AppData\Local\Temp\tmpkkqqo0dy.json
23:26:45 [INFO   ] USF.BLENDER: Blender 退出码 0, 耗时 1.8s, 声明产物 13 个
23:26:45 [INFO   ] USF.PIPELINE: [OK] Blender 网格后处理 完成 (1.8s)
```

最终 `output/` 19 个文件（全部非零字节）：

| 格式 | LOD0 | LOD1 | LOD2 |
|---|---:|---:|---:|
| FBX | 599,276 | 582,876 | 572,876 |
| OBJ | 89,233 | 52,418 | 30,459 |
| GLB | 580,444 | 561,580 | 551,704 |
| USDA | 195,878 | 111,330 | 64,636 |
| MTL | 240 | 240 | 240 |

另有 `usf_scene.blend` 158,772、`usf_scene_texture.png` 535,967、`usf_scene_3dgs.ply` 1,088,471。

再导入校验 `build/audit/e2e/validate_final2.log`：13 个格式全部 `[CHECK] OK`；BLEND 内 4 对象 / 2,399 顶点 / 2,119 面；LOD0 778v/771f，LOD1 514v/385f，LOD2 329v/192f（GLB 因法线拆分顶点数略高）。

### 退出码语义实测汇总

| 场景 | 退出码 | 证据 |
|---|---:|---|
| E2E 全链成功 | **0** | `cli_stdout_final2.log` 末行 + `EXIT=0` |
| 无 COLMAP、无点云 → 多阶段失败 | **1** | `build/audit/e2e/cli_exit1_redacted.log` |
| 非法参数（`--cli` 缺 `--input`） | **2** | `build/audit/e2e/cli_argerror.log`（`ARGERR_EXIT=2`） |
| 输出为盘符根 / 输入不存在 | **3** | `cli_reject_driveroot.log`、`cli_reject_input.log` |
| Blender 边界（0 面网格） | **1**（子进程） | `build/audit/runs/run_edge/harness.log:12` |

---

## 3. 格式覆盖与“不适用”声明

| 格式 | 状态 | 证据 |
|---|---|---|
| FBX | 已实测 | 再导入 `[CHECK] OK`，`FBX version: 7400` |
| OBJ | 已实测 | `bpy.ops.wm.obj_export`；再导入 OK |
| GLB | 已实测 | `bpy.ops.export_scene.gltf`；再导入 OK |
| BLEND | 已实测 | `bpy.ops.wm.save_as_mainfile`；`[CHECK] OK` |
| USD（.usda） | 已实测 | `bpy.ops.wm.usd_export`，RNA 参数探测 `scripts/blender/mesh_to_fbx.py:471-507`；再导入 OK |
| UASSET | **不适用** | Blender 5.2 无 UASSET 导出器；UASSET 只能由 UE5 编辑器（`app/dcc/ue5_bridge.py`）生成。本机未安装 UE5 且无 UE5 授权，故按合规要求跳过，不以 mock 冒充 |

---

## 4. 已知限制

1. **UASSET 未验证**：原因见 §3；UE5 阶段在 E2E 中记录 `[LICENSE] 跳过 ue5`（`cli_stdout_final2.log:58-59`）。
2. **Metashape 未执行**：本机未安装且无授权，流水线记录 `[SKIP] Metashape 洞穴修复 跳过: 未启用 Metashape 洞穴修复`（`cli_stdout_final2.log:190`）。不 mock、不绕过许可证。
3. **gltf-validator 未使用**：`npx gltf-validator` 被本机 PowerShell 执行策略阻止；改用 Blender `bpy.ops.import_scene.gltf` 再导入校验（同等验证“可被消费端读取”）。未实测项，原因记录于此。
4. **非流形网格**：E2E 网格有 663 条非流形边，Blender 脚本告警后继续导出（`cli_stdout_final2.log:70,78`），导出文件仍可再导入；属设计取舍（L-03）。
5. **Blender 缩略图缓存写入用户目录**：`cli_stdout_final2.log:165` 出现 `OpenImageIO write failed ... C:\Users\kimi\.thumbnails\...`，这是 Blender 保存 .blend 时尝试写文件预览图，失败不影响 .blend 产物（同轮 `[CHECK] OK`）。后台任务在沙箱下会因此被拦截，故本报告采用前台实测。
6. **纹理烘焙**：默认 2048×2048，E2E 单次约 1 秒级；更大分辨率未测。
7. **耗时**：Blender 后处理 1.6–1.8s（夹具规模），不代表大场景性能。
8. **EXE 内 open3d 不打包**：按既有体积设计，open3d（MIT，约 230MB）不进入安装包，由「依赖体检」补装到 `~/.universal_scene_forge/python_pkgs`。本次 EXE 实测即使用该补装路径；未补装时 MESH 阶段会给出可读错误（`app/core/mesh.py:71-74`），不会崩溃。

---

## 5. EXE 打包与冻结环境实测（2026-10-22）

### 5.1 构建

- 构建工具：PyInstaller **6.22.3**（GPL-2.0-or-later with bootloader exception，仅构建期使用、不随产物分发）
- 构建解释器：Python **3.14.6**（`build/pack_venv`）
- 构建命令：

```powershell
& 'build\pack_venv\Scripts\python.exe' -m PyInstaller `
  'installer\UniversalSceneForge.spec' --noconfirm --clean
# BUILD_EXIT=0
```

- onedir 产物：`dist\UniversalSceneForge\`（最终 162,760,865 字节）
- 单文件安装包：`installer\UniversalSceneForge_0.9.8_Preview_Setup.exe`（0.9.8 重建）
- 构建日志：`build/audit/package_build.log`、`build/audit/package_nsis.log`

### 5.2 启动验证

```powershell
& 'dist\UniversalSceneForge\UniversalSceneForge.exe' --version
# EXIT=0
# Universal Scene Forge 0.9.8-preview
```

GUI 模式：进程启动后持续运行 6 秒未退出（人工终止），无 Shiboken/MKL 缺失错误。关键修复见 `LICENSE_COMPLIANCE_CHANGES.md` §7.2。

### 5.3 EXE 端到端（真实 Blender 子进程）

```powershell
$env:USERPROFILE = '<repo>\build\audit\exe_home'   # 隔离用户目录，避免沙箱拦截
& 'dist\UniversalSceneForge\UniversalSceneForge.exe' --cli `
  --input 'build\audit\e2e\input_frames' `
  --output 'build\audit\exe_e2e\work7' `
  --model 'build\audit\e2e\model\point_cloud\iteration_7000\point_cloud.ply' `
  --accept-3dgs-license
# EXIT=0
```

日志（冻结模式 `~/.universal_scene_forge/logs/usf_20261022_131748.log`）：

```
USF.MESH: 网格: 906 顶点 / 800 面
USF.BLENDER: Blender 命令: "...\blender.exe" --background --factory-startup --python ...\_internal\scripts\blender\mesh_to_fbx.py -- C:\Users\<user>\AppData\Local\Temp\tmpheeej467.json
USF.BLENDER: Blender 退出码 0, 耗时 1.6s, 声明产物 10 个
USF.BLENDER: Blender 退出码 0, 耗时 1.6s, 声明产物 10 个
```

产物：16 个文件（FBX/OBJ/GLB 各 3 个 LOD、BLEND、MTL、纹理 PNG、3DGS PLY）。
- 首轮 EXE 再导入校验：`build/audit/exe_e2e/validate7.log`，10 个格式文件全部 `[CHECK] OK`（`VALIDATE_EXIT=0`；最终全格式轮见 §6.5，13/13）：

```
[CHECK] OK .../usf_scene.blend objects=4 verts=2405 faces=2125
[CHECK] OK .../usf_scene.fbx objects=1 verts=780 faces=773
[CHECK] OK .../usf_scene.glb objects=1 verts=865 faces=773
[CHECK] OK .../usf_scene.obj objects=1 verts=780 faces=773
[CHECK] OK .../usf_scene_lod1.fbx objects=1 verts=515 faces=386
[CHECK] OK .../usf_scene_lod1.glb objects=1 verts=492 faces=386
[CHECK] OK .../usf_scene_lod1.obj objects=1 verts=515 faces=386
[CHECK] OK .../usf_scene_lod2.fbx objects=1 verts=330 faces=193
[CHECK] OK .../usf_scene_lod2.glb objects=1 verts=291 faces=193
[CHECK] OK .../usf_scene_lod2.obj objects=1 verts=330 faces=193
```

### 5.4 EXE 合规边界

- `dist` 内无 `external/`、无 `plyfile/`；`COLLECT/PKG/EXE-00.toc` 对 `plyfile|gaussian-splatting` **0 命中**。
- `LICENSE`、`THIRD_PARTY_LICENSES.md`、`NOTICE` 随包位于 `dist\UniversalSceneForge\_internal\`，NSIS 安装时另复制到安装目录。
- 旧版 `0.9.5/0.9.6/0.9.7` 安装包已删除，仅保留重新构建的 `0.9.8`。
- 单文件安装包未实际安装（需要管理员/UAC）；其内容来自已验证的 `dist`，NSIS 脚本已移除 external 分支。

---

## 6. Blender/MESH 全跑通补测（2026-10-22）

### 6.1 本轮修复的缺陷

| # | 缺陷 | 修复位置 | 证据 |
|---|---|---|---|
| 1 | Open3D 0.20 的 `cluster_connected_triangles()` 返回 3 元组，旧代码按 2 元组解包 → `TypeError`，碎片移除被整体跳过 | `app/core/mesh.py:120-206` | 修复前 `source_final.log:17`：`细化-碎片移除跳过: '>' not supported...` |
| 2 | 碎片策略在“只保留最大块”与“整体跳过”之间摇摆 | **最终由 §7.2 智能保留策略取代**（主体 + 近邻/足够大的碎片） | 中间态 `source_final2.log`：`移除 90 个小碎片, 保留 60 面`；最终 `source_final4.log`：`智能保留 46/90 连通块, 面 803→733` |
| 3 | LOD 下限 `max(1_000, ...)` 使 <1000 面网格的 LOD1/LOD2 与 LOD0 完全相同 | `app/core/mesh.py:106-115`、`app/core/exporter.py:169-175` | 修复前 LOD1/LOD2 均 800 面；修复后 `802 → 401 → 200` |
| 4 | `.blend1` 自动备份混入交付目录 | `scripts/blender/mesh_to_fbx.py:545-554`：`save_version=0` | `run5/out` 与 `work8/output` 的 `*.blend1` 数量均为 **0** |
| 5 | LOD0 重复复制原对象，.blend 内多一份同模对象 | `scripts/blender/mesh_to_fbx.py:663-665` | .blend 对象数由 4 降为 3（LOD0+LOD1+LOD2） |
| 6 | 不支持 USD 输入；USDC 请求仍写 `.usda` | `mesh_to_fbx.py:218-234`、`:587-592`、`:671` | `run6` 成功 `已导入 usf_scene.usda via bpy.ops.wm.usd_import` |
| 7 | 默认导出格式缺 USD，uasset 无 UE5 时只告警 | `app/models/settings.py:125-126`、`settings.json:57-64` | 源/EXE E2E 均产出 `usf_scene.usda` + LOD USDA |

### 6.2 全格式轮（run5）

- 输入：`build/audit/fixtures/sphere.obj`（266v/288f）
- 格式：`fbx,obj,glb,blend,usd,usdc`；LOD `1.0,0.5,0.25`
- 命令与日志：`build/audit/runs/run5/`
- **退出码 0，16 个产物**；再导入校验 `run5/validate.log`：**16/16 `[CHECK] OK`**
- LOD 面数：LOD0 288 → LOD1 188 → LOD2 106
- 关键产物：`usf_scene.usda` 73,960 / `usf_scene.usdc` 23,103 / `usf_scene.blend` 114,804；无 `.blend1`

### 6.3 USD 输入回环（run6）

- 输入：`run5/out/usf_scene.usda`
- 格式：`obj,glb,blend,usd`；LOD `1.0,0.5`
- **退出码 0，7 个产物**：OBJ/GLB/USDA ×2 + BLEND
- 证明 Blender 阶段不是只能吃 OBJ，可对 USD 资产继续后处理。

### 6.4 源环境全链（source_final3）

- 命令：`main.py --cli --input build\audit\e2e\input_frames --output build\audit\e2e\source_final3 --model ...point_cloud.ply --accept-3dgs-license`
- **退出码 0**；`output/` 18 个文件；Blender 两次 **退出码 0**（3.8s / 3.7s），声明产物 13 个
- 再导入校验 `source_final3_validate.log`：**13/13 `[CHECK] OK`**（FBX/OBJ/GLB/USDA ×3 + BLEND）
- LOD 面数：LOD0 776 → LOD1 388 → LOD2 194；`.blend1` 数量 0

### 6.5 EXE 全链（work8）

- 重新构建：`PyInstaller 6.22.3`，dist 162,752,999 字节
- 安装包：`installer\UniversalSceneForge_0.9.8_Preview_Setup.exe`（0.9.8 重建）
- EXE 命令与 §5.3 相同，输出 `build\audit\exe_e2e\work8`
- **退出码 0**；18 个产物；Blender 两次退出码 0（3.7s / 3.8s）
- 再导入校验 `build/audit/exe_e2e/validate8.log`：**13/13 `[CHECK] OK`**
- LOD 面数：775 → 387 → 193；`.blend1` 数量 0

### 6.6 回归测试

- 14 个单元/回归测试全部 **exit 0**（`build/audit/tests_blender_final/*.log`）
- 安全自测 **17 PASS / 0 FAIL**；PLY 读写测试 **OK**

### 6.7 剩余限制

1. **UASSET**：仍需 UE5 编辑器 + 自备授权，纯 Blender 路径不适用（日志明确 `uasset 需要 UE5`）。
2. **碎片移除**：已升级为 §7.2 的智能连通块保留策略（主体+近邻/足够大的碎片），不再只保留最大块或整体跳过。
3. **open3d**：仍不随 EXE 打包，需依赖体检补装；本次 EXE 实测使用补装目录。
4. **纹理烘焙**：仅对首个 MESH 对象烘焙；多对象场景未逐一烘焙。

---

## 7. UV 纹理坐标与碎片化网格策略修复（2026-10-22 第二轮回）

### 7.1 纹理坐标缺失的根因与修复

**根因**：`smart_uv` 旧实现只判断 `obj.data.uv_layers` 是否为空。若输入网格自带一个**全零/退化 UV 层**（常见于其他 DCC 导出的 OBJ/FBX），函数会直接 return，导出器随后写出无效的 `vt`/`TEXCOORD_0`，表现为“有 UV 但贴图全黑/拉伸/缺失”。

**修复**（`scripts/blender/mesh_to_fbx.py`）：

- `_uv_stats` / `_uv_usable`（`:274-289`）：统计 UV 循环非零覆盖率，低于 50% 判定为不可用；
- `smart_uv`（`:292-356`）：不可用时删除死图层，新建 `UVMap`，执行 `smart_project`（`correct_aspect=True`、`scale_to_bounds=True`）；仍不可用则回退 `cube_project`；
- 每个导出前 `run_export`（`:142-147`）检查 UV 并在缺失时告警；
- OBJ 显式 `export_uv=True`（`:501-507`）；USD 显式 `export_uvmaps=True`（`:570-573`）；GLB 原有 `export_texcoords=True`。

**实测（已落盘）**：

| 场景 | 结果 | 证据 |
|---|---|---|
| 全零 UV 夹具 `sphere_zero_uv.obj` | 检测为不可用并重建为 **1104/1104 非零**；输出 OBJ 有 400 条真实 `vt` | `build/audit/runs/run8/` |
| 源环境 E2E | `UV 生成完成: usf_scene (2199/2199 非零)` | `build/audit/e2e/source_final4.log` |
| EXE E2E | `UV 生成完成: usf_scene (2196/2196 非零)` | `build/audit/exe_e2e/stdout9.log` |
| 导出文件 UV 复核 | **13/13** 个 FBX/OBJ/GLB/USDA/BLEND 均有 UV 层且非零 | `build/audit/exe_e2e/uv9.log` |

### 7.2 碎片化网格的智能连通块保留

**旧策略问题**：只保留最大连通块，在主体被切成大量小块时会只剩几十个面；上一轮的“最大簇 <50% 就整体跳过”又会让远处飞点进入交付。

**新策略**（`app/core/mesh.py:120-183` `_prune_components`）：

1. 最大块无条件保留，面数前 5 的块也保留；
2. 其余块需满足“面数 ≥ max(4, 0.2%)”且（质心在主包围盒外扩 35% 内 或 自身包围盒对角线 ≥ 主体 5%）；
3. 若策略会删除超过 70% 的面，判定为主体碎片化并整体跳过，保证不把主体删成残片。

**实测效果**：

- 源 E2E：`智能保留 46/90 连通块, 移除 44 个碎片, 面 803→733`（`source_final4.log`）
- EXE E2E：`智能保留 46/90 连通块, 移除 44 个碎片, 面 803→732`（`work9` 日志）
- 对比上一轮：曾出现“移除 90 个碎片，只剩 60 面”的破坏性结果；新策略保留 91% 的面。

### 7.3 最终全链复测

| 环境 | 退出码 | 产物 | 再导入 | LOD | `.blend1` |
|---|---:|---:|---:|---|---:|
| 源 `source_final4` | 0 | 18 | 13/13 OK | 733→366→183 | 0 |
| EXE `work9` | 0 | 18 | 13/13 OK | 732→366→183 | 0 |

- 最终 dist：**162,760,865 字节**（含 §8 的切线/UV 合并）
- 最终安装包：`installer\UniversalSceneForge_0.9.8_Preview_Setup.exe`（0.9.8 重建）
- 回归测试：14 个测试全部 exit 0；安全自测 17/17；PLY OK（`build/audit/tests_tangent_final/`）

### 7.4 新发现/剩余限制

1. **法线/切线**：已在 §8 开启 GLB `export_tangents`，POSITION/NORMAL/TEXCOORD_0/TANGENT 均已实测。
2. **多 UV 层**：已在 §8 合并为单一 `UVMap`（保留 active_render/最高覆盖层，删除其余层）。
3. **碎片策略阈值**：4 面/0.2%/35%/5% 为本机夹具调参结果，超大场景可在 `_prune_components` 中按比例调整。
4. **UASSET**：仍需 UE5，不在 Blender 路径内。

---

## 8. GLB 切线与多 UV 层合并（2026-10-22 第三轮回）

### 8.1 实现

| 能力 | 代码位置 | 行为 |
|---|---|---|
| GLB 切线 | `scripts/blender/mesh_to_fbx.py:605-619` | 探测 `export_tangents` 属性并设为 True；同时显式 `export_normals=True`、`export_texcoords=True` |
| 多 UV 合并 | `scripts/blender/mesh_to_fbx.py:299-359` `merge_uv_layers` | 保留 `active_render` 层 → active 层 → 非零覆盖最高层；删除其余层，统一命名 `UVMap` 并设 active_render |
| 调用点 | `smart_uv` `:362-428` | 无论 UV 是已有还是新建，最终都归一为一个 UVMap |

> 说明：不同 UV 布局在没有材质/纹理映射信息时无法安全 atlas 合并；这里“合并”指交付物只保留一套 canonical UV，避免下游引擎选错 TEXCOORD。

### 8.2 多 UV 夹具实测（run9）

- 夹具：`build/audit/fixtures/sphere_multi_uv.fbx`（含 `UVMain` + `UVSecond` 两层）
- 命令：`blender --background --factory-startup --python scripts/blender/mesh_to_fbx.py -- build/audit/runs/run9/params.json`
- 日志：`UV 合并: USF_Sphere 保留 UVMain, 删除 1 层（UVSecond）→ UVMap`
- GLB 属性（LOD0 与 LOD1 均）：

```
POSITION, NORMAL, TEXCOORD_0, TANGENT
```

- 再导入校验 `run9/validate.log`：**5/5 `[CHECK] OK`**

### 8.3 完整流程复测

| 环境 | 退出码 | 产物 | 再导入 | GLB TANGENT | LOD |
|---|---:|---:|---:|---|---|
| 源 `source_final5` | 0 | 18 | 13/13 OK | 3/3 LOD 均含 | 732→366→183 |
| EXE `work10` | 0 | 18 | 13/13 OK | 3/3 LOD 均含 | 731→365→182 |

- 源 E2E 日志：`build/audit/e2e/source_final5.log`
- EXE E2E 日志：`build/audit/exe_e2e/stdout10.log`
- 再导入校验：`source_final5_validate.log`、`validate10.log`
- 回归测试：14 个测试全部 exit 0；安全自测 17/17；PLY OK（`build/audit/tests_tangent_final/`）

### 8.4 剩余限制

1. **切线质量**：TANGENT 由 Blender 基于 UV + 法线生成；极端退化 UV 可能产生零切线，消费端应回退到屏幕空间切线。
2. **UV 合并不做 atlas**：保留一套 canonical UV；若需要把多套 UV 打包成图集，需要材质/贴图映射信息与重新烘焙。
3. **UASSET**：仍需 UE5。
