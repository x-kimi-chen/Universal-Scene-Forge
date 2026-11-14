# 通用场景锻造系统 开发手册 (Development Manual)

> 适用版本: **1.1** ｜ 更新日期: 2026-11-14
> 面向: 二次开发者、维护者、集成方
> 技术栈: Python 3.12 / PySide6 / Open3D / COLMAP / 3D Gaussian Splatting / Blender

本手册描述项目结构、九阶段流水线、DCC 桥接契约、测试体系与发布流程。
用户操作说明见 `docs/user_manual.html`；版本变更见 `CHANGELOG.md`。

---

## 1. 快速开始

### 1.1 源码运行

```bat
git clone https://github.com/x-kimi-chen/Universal-Scene-Forge.git
cd Universal-Scene-Forge\universal_scene_forge
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
python main.py
```

可选组件：

- `pip install open3d`：网格重建核心（安装版由「依赖体检」补装到用户目录）。
- `pip install "rembg[cpu]"`：主体遮罩。
- nerfstudio / OpenSplat / Postshot：替代训练后端，见 `app/core/trainers.py`。
- 3DGS 训练环境需单独准备，见 README「训练环境」。

### 1.2 命令行

```bat
python main.py --version
python main.py --check-deps
python main.py --cli --input a.mp4 b/ --output D:/work
python main.py --cli --input a.mp4 --output D:/work --model point_cloud.ply
```

| 参数 | 说明 |
|---|---|
| `--cli` | 无界面模式 |
| `--input` | 一个或多个视频/图片目录 |
| `--output` | 工作/输出目录 |
| `--model` | 外部 3DGS PLY，跳过 SFM/TRAIN |
| `--accept-3dgs-license` | 确认 Inria/MPII 非商业许可 |
| `--commercial-use` | 声明商业用途，拒绝执行 3DGS |
| `--check-deps` | 打印依赖体检报告 |

退出码：`0` 全部成功，`1` 有阶段失败，`2` 参数错误，`3` 输入/输出路径被拦截，`130` 用户取消。

---

## 2. 目录结构

```
main.py                        入口: GUI / CLI / --check-deps
app/
  __init__.py                  版本号与显示名
  views/main_window.py         主窗口、依赖体检、路径设置、各线程类
  controllers/
    pipeline_controller.py     流水线编排 + EventBus
  core/
    ingest.py                  抽帧、模糊帧剔除、预处理
    colmap.py                  COLMAP SfM 封装
    gaussian.py                内置 3DGS 训练封装
    trainers.py                训练器协议、注册表与多引擎适配
    gaussian_filter.py         训练后主体高斯过滤
    masking.py                 主体遮罩（rembg 可选）
    mesh.py                    泊松网格重建与清理
    tsdf.py                    3DGS 深度 → TSDF 网格
    phototexture.py            相机位姿投影贴色
    ns_convert.py              COLMAP → nerfstudio transforms.json
    exporter.py                多格式导出与 DCC 降级链
  dcc/                         各宿主无头桥接 + 探测 + 安装器
  models/                      PipelineSettings / ProjectState / Stage
  utils/                       日志、路径、安全、依赖、GPU、PLY、许可
scripts/                       随包分发的宿主内脚本
  blender/mesh_to_fbx.py       Blender 网格后处理（核心）
  gaussian/render_depth.py     3DGS 深度渲染
  metashape/repair_mesh.py     Metashape 修洞
  ue5/import_and_save.py       UE5 导入与保存
  max/houdini/c4d/...          其它 DCC 脚本
installer/                     PyInstaller spec + NSIS + 版本资源
docs/                          用户手册、开发手册、测试报告
tests/                         单元、回归、E2E 脚本
extensions/                    Premiere CEP 扩展
external/                      训练仓库（不随包分发）
```

---

## 3. 九阶段流水线

| # | 阶段 (`Stage`) | 输入 | 主要产物 | 核心实现 | 失败/跳过策略 |
|---|---|---|---|---|---|
| 1 | INGEST 输入采集与抽帧 | 视频/图片 | `frames/` | `app/core/ingest.py` | 失败则终止后续所有阶段 |
| 2 | SFM COLMAP 位姿求解 | 帧 | COLMAP 数据集 | `app/core/colmap.py` | 失败后 TRAIN 跳过 |
| 3 | TRAIN 3DGS 训练 | COLMAP 数据集 | `point_cloud.ply` | `app/core/gaussian.py` + `trainers.py` | 无训练环境回退泊松 |
| 4 | MESH 泼溅网格重建 | PLY / 帧 | `usf_scene.obj` | `app/core/tsdf.py` → `mesh.py` | TSDF 失败回退泊松 |
| 5 | RIZOMUV 自动展 UV | 网格 | 已展 UV 网格 | `app/dcc/rizomuv_bridge.py` | 未安装/未启用则跳过 |
| 6 | UE5_PREVIEW 实时预览 | 网格 | uasset 预览 | `app/dcc/ue5_bridge.py` | 未配置工程则跳过 |
| 7 | DCC_POST Blender 后处理 | 网格 | FBX/GLB/BLEND/USD | `scripts/blender/mesh_to_fbx.py` | Blender 缺失则导出阶段降级 |
| 8 | REPAIR Metashape 洞穴修复 | 网格 | 修复网格 | `app/dcc/metashape_bridge.py` | 默认关闭，需 Professional |
| 9 | EXPORT 多格式导出 | 网格 | 各格式文件 | `app/core/exporter.py` | 单格式失败不影响其它格式 |

### 3.1 容错语义

`PipelineController._safe()` 统一包裹每个阶段：

- 正常完成 → `success`；
- 抛 `StageSkipped` → `skipped`（`ok=null`，不计入失败，不影响退出码）；
- 其它异常 → `failed`，记录到 `failed_stages`，继续执行后续阶段；
- INGEST 失败会级联终止，其余阶段标记「未执行」。

阶段结果写入 `work_dir/project.json`，供外部系统消费。

---

## 4. 控制器与事件总线

`EventBus` 是控制器 → 视图的唯一通道，全部为 Qt Signal：

| 信号 | 参数 | 用途 |
|---|---|---|
| `stage` | 阶段名 | 当前阶段切换 |
| `stage_failed` | 阶段名 | 阶段失败 |
| `stage_skipped` | 阶段名 | 主动跳过 |
| `progress` | `(0-100, msg)` | 总进度 |
| `dcc` | `(key, state)` | 宿主状态: `ok/busy/error/off/skipped` |
| `log_line` | `(level, msg)` | 逐行日志 |
| `artifact` | 路径 | 产物 |
| `preview` | 目录 | 缩略图 |
| `finished` | `(ok, summary)` | 结束 |

GUI 和 CLI 共用同一个控制器。GUI 在 `QThread` 中调用 `execute()`，CLI 用
`QCoreApplication` 驱动；控制器本身不直接操作控件。

---

## 5. 配置与状态

### 5.1 `PipelineSettings`

`app/models/settings.py`，持久化为 `settings.json`：

- 源码模式：项目根目录；
- 冻结模式：`~/.universal_scene_forge/settings.json`。

主要字段分组：

| 分组 | 字段示例 |
|---|---|
| 抽帧 | `frame_fps`、`max_frames`、`preprocess_max_side` |
| SfM/训练 | `colmap_exe`、`gs_repo`、`gs_python`、`gs_iterations` |
| 网格 | `poisson_depth`、`opacity_threshold`、`enable_tsdf`、`texture_size`、`lod_ratios` |
| DCC | `blender_exe`、`metashape_exe`、`ue5_editor_cmd`、`ue5_uproject` |
| 导出 | `export_formats`、`export_dir`、`bake_ao` |
| 依赖/合规 | `pip_mirror`、`tools_root`、`commercial_use`、`license_ack_3dgs` |

### 5.2 路径探测

`app/dcc/registry_probe.py` 按三级顺序探测 DCC：

1. 环境变量（如 `BLENDER_PATH`）；
2. Windows 注册表；
3. Steam 库（`libraryfolders.vdf` → `steamapps/common/<name>`）→ 常见安装路径 → `PATH`。

手动在「项目调用路径设置」填写的值优先级最高，CLI 与 GUI 共用同一份探测结果。

---

## 6. DCC 桥接开发规范

所有桥接遵循同一契约：

1. 通过临时 JSON 传参，规避命令行引号/中文转义问题；
2. 子进程使用 `CREATE_NO_WINDOW`，stdout 逐行解析；
3. 支持 `stop_event` 与超时，超时杀进程树；
4. 产物必须存在且非零字节，否则视为失败；
5. 错误抛 `XxxBridgeError`，由 `_safe()` 兜底；
6. 子进程环境经 `scrubbed_env()` 清洗，可执行文件经 `resolve_executable()` 解析。

### 6.1 新增一个 DCC 桥接

1. 在 `app/dcc/` 新建 `xxx_bridge.py`，实现 `run_script()` 与业务方法；
2. 在 `app/dcc/registry_probe.py` 的 `SPECS` 注册探测规则（exe 名、注册表、Steam 目录）；
3. 在 `DCC_PATH_FIELDS` 增加 settings 字段映射；
4. 在 `app/models/settings.py` 增加 `xxx_exe` 与超时字段；
5. 在 `app/core/exporter.py` 注册到对应格式的降级链；
6. 在 `scripts/xxx/` 提供宿主内脚本；
7. 更新 `tests/regression_nextgen.py` 与本文档的适配速查表。

---

## 7. Blender 适配层（3.x–5.3，含 Steam）

### 7.1 调用契约

```powershell
blender --background --factory-startup --python scripts\blender\mesh_to_fbx.py -- params.json
```

`params.json`：

```json
{
  "input": "D:/work/usf_scene.obj",
  "out_dir": "D:/output",
  "base_name": "usf_scene",
  "formats": ["fbx", "glb", "blend", "usd"],
  "lod_ratios": [1.0, 0.5, 0.25],
  "bake_ao": false,
  "bake_texture": true,
  "texture_size": 4096
}
```

输出协议：

```
[USF] PROGRESS <0-100> <msg>
[USF] FILE <abs path>
[USF] DONE
[USF] FATAL <error>
```

### 7.2 版本适配点

桥接脚本不按版本号硬编码，统一用 RNA 特性探测：

- **导入**：`wm.obj_import` / `wm.ply_import` / `wm.stl_import`，旧版回退
  `import_mesh.*`；FBX/GLB/USD 走 `import_scene.*` / `wm.usd_import`。
- **OBJ 导出**：4.0+ `wm.obj_export`，3.x 回退 `export_scene.obj`。
- **USD 导出**：顶点色参数 4.x 为 `export_colors`，5.3 为
  `export_mesh_colors`；按 RNA 探测两代名称。
- **glTF 顶点色**：5.x 的 `export_vertex_color` 为枚举。`'MATERIAL'` 只在
  材质真正使用顶点色时写 `COLOR_0`；烘焙贴图接管 Base Color 后会丢失，
  因此有颜色属性时使用 `'ACTIVE'`。
- **材质节点**：5.3 起 `Material.use_nodes` 已弃用（6.0 移除），读写都会
  触发 `DeprecationWarning`，`_ensure_node_tree()` 仅在缺少节点树时回退赋值。
- **烘焙**：仅 Cycles 支持；4096 失败自动回退 2048；线性缓冲写 PNG 前做
  sRGB 编码。
- **UV**：已有 UV 校验非零覆盖率；不可用时重建；覆盖率过低时使用逐面
  atlas；多 UV 层合并为单一 `UVMap`。

### 7.3 Steam 版

- 探测：`registry_probe._steam_lookup()` 读取 `SteamPath` 与
  `libraryfolders.vdf`，扫描所有 Steam 库的 `steamapps/common/Blender`。
- 本机验证：Steam 版 Blender 5.3.0，全格式 19 个产物再导入 19/19 通过；
  证据见 `BLENDER_PIPELINE_TEST.md` 第 10 节。
- 测试脚本：`tests/unit_blender_versions.py` 自动读取 Steam 版实际版本号，
  不依赖 PySide6。

---

## 8. 导出链路

`app/core/exporter.py` 按格式分发：

| 格式 | 主路径 | 降级链 |
|---|---|---|
| FBX | Blender | 3ds Max → Maya → Houdini → Cinema 4D → Modo |
| OBJ | Blender | Open3D 直写（MTL/纹理随行） |
| GLB / GLTF | Blender | — |
| BLEND | Blender | — |
| USD / USDA / USDC | Blender | — |
| UASSET | UE5 编辑器 | 无（需 UE5 + .uproject） |

通用规则：

- 每个格式独立容错，单路失败不阻断其它格式；
- 导出产物必须存在且非零字节；
- LOD 由 `lod_ratios` 控制，LOD0 复用原对象，LOD1+ 使用 Decimate；
- 纹理烘焙默认 4096，按 `texture_size` 可调；
- `.blend` 保存时关闭 `.blend1` 自动备份。

---

## 9. 测试体系

### 9.1 运行方式

```bat
:: 全部单元测试（需要对应软件/环境的会自行 SKIP 或给出可读错误）
for %t in (tests\unit_*.py) do .venv\Scripts\python.exe %t

:: Blender 多版本桥接（建议单独跑）
.venv\Scripts\python.exe tests\unit_blender_versions.py

:: 回归
.venv\Scripts\python.exe tests\regression_nextgen.py
```

### 9.2 测试清单

| 脚本 | 覆盖内容 | 依赖 |
|---|---|---|
| `unit_qa_fixes.py` | 历史 QA 缺陷锁定 + 版本号一致性 | 无 |
| `unit_test_report_fixes.py` | 第二轮测试报告缺陷 + 手册版本 | 无 |
| `unit_deps_sources.py` | 多下载源、训练环境体检、便携版探测 | 无 |
| `unit_deps_open3d.py` | open3d 误报与补装路径 | 无 |
| `unit_ingest_multi.py` | 多源输入、抽帧、阶段信号 | OpenCV |
| `unit_export_fallback.py` | 导出降级链与产物完整性 | 无（可用假桥） |
| `unit_ffmpeg_persist.py` | FFmpeg 路径持久化 | 无 |
| `unit_pr_bridge.py` | Premiere CEP 抽帧桥接 | PySide6 |
| `unit_train_autosetup.py` | 训练环境一键安装 | PySide6 |
| `unit_096_prep.py` | 训练器注册表 / RealityScan / 遮罩 | 无 |
| `regression_nextgen.py` | DCC 探测、字段一致性、HTML 合法性 | PySide6 |
| `unit_blender_versions.py` | Blender 多版本网格后处理 | Blender |
| `unit_ue5_autoproject.py` | UE5 自动工程与推流 | UE5 |
| `unit_scenario_fallback.py` | MESH 失败 → Metashape 修复兜底 | Metashape（慢） |

### 9.3 Blender 专项矩阵

`BLENDER_PIPELINE_TEST.md` 记录了逐轮实测。新增 Blender 版本或修改桥接后，
至少重跑：

1. 全格式：`fbx,obj,glb,gltf,blend,usd,usdc` × 3 LOD；
2. 零 UV / 多 UV / 0 面边界；
3. USD / GLB / FBX 输入回环；
4. 再导入校验（独立 Blender 进程）；
5. `tests/unit_blender_versions.py`（GLB 属性断言）。

---

## 10. 打包与发布

### 10.1 构建

```bat
.venv\Scripts\python.exe -m PyInstaller installer\UniversalSceneForge.spec --noconfirm --clean
robocopy external\gaussian-splatting dist\UniversalSceneForge\external\gaussian-splatting /E /XD .venv build input
cd installer
makensis usf_setup.nsi
```

说明：

- PyInstaller 产物为 `dist/UniversalSceneForge/`（onedir）；
- `open3d`、`torch`、`rembg` 不随壳打包，运行期由「依赖体检」补装；
- `plyfile` 因 GPL-3.0+ 被显式排除，PLY 读写走自研 `app/utils/ply_io.py`；
- NSIS 脚本不会安装 `external/`。

### 10.2 版本号同步清单

版本号变更时逐项更新：

1. `app/__init__.py` → `__version__`
2. `installer/version_info.txt` → `filevers/prodvers` + `FileVersion/ProductVersion`
3. `installer/usf_setup.nsi` → `APPVERSION`、`OutFile`、`VIProductVersion`、
   `FileVersion`、`ProductVersion`
4. `README.md` → 当前版本与安装包文件名
5. `docs/user_manual.html` → 标题、页眉、页脚版本
6. `docs/DEV_MANUAL.md` → 适用版本
7. `CHANGELOG.md` → 新版本段落

检查命令：

```bat
.venv\Scripts\python.exe tests\unit_qa_fixes.py
.venv\Scripts\python.exe tests\unit_test_report_fixes.py
```

### 10.3 发布

1. 确认版本一致性测试通过；
2. 构建 EXE 与安装包；
3. 生成 `installer/SHA256SUMS.txt`；
4. 打 tag（如 `v1.1`）并推送；
5. 在 GitHub Releases 上传安装包、绿色版与校验和。

---

## 11. 编码与安全约定

- **子进程**：禁止 `shell=True`；exe 经 `resolve_executable()` 解析；
  环境经 `scrubbed_env()` 清洗，剥离 `PYTHONPATH`/`PYTHONHOME`。
- **路径**：输入/输出经 `normalize_input()` / `ensure_output_dir()` 校验，
  拒绝盘符根、系统目录与越界路径。
- **日志**：含用户名/本机路径的日志经 `redact()`；文件日志 UTF-8 with BOM。
- **编码**：新文件统一 UTF-8；Windows 控制台使用 ASCII 标记避免 GBK 崩溃。
- **降级优先**：可选依赖缺失 → 明确日志 + 跳过，不阻断主流程。
- **许可**：3DGS 上游为 Inria/MPII 非商业许可，不随包分发；PySide6 动态链接；
  FFmpeg 由用户自行下载；新增依赖须登记 `THIRD_PARTY_LICENSES.md`。
- **隐私**：不要把本机路径、Token、用户名写入提交文件；`settings.json` 已
  `.gitignore`。

---

## 12. 故障排查

### 12.1 Blender 未被识别

1. 运行 `python main.py --check-deps`；
2. 确认 Steam 库路径存在：`steamapps/common/Blender/blender.exe`；
3. 在「项目调用路径设置」手动指定 `blender.exe`；
4. 确认 `--factory-startup` 下 Blender 能正常启动。

### 12.2 Blender 导出失败

- 查看 `[USF]` 日志中的格式名与异常；
- 0 面网格会按设计失败，检查 MESH 阶段产物；
- USD/GLB 参数在 4.x/5.3 名称不同，优先用 RNA 探测而非硬编码；
- `.blend` 保存时的 `.thumbnails` 写入失败不影响产物。

### 12.3 open3d 缺失

- 源码：`pip install open3d`；
- 安装版：在「依赖体检」中一键补装，落到
  `~/.universal_scene_forge/python_pkgs`；
- MESH 阶段会给出可读错误，不会崩溃。

### 12.4 控制台中文/符号乱码

- 日志文件为 UTF-8 with BOM；
- 控制台标记使用 `[OK]` / `[X]` / `[!]`，避免 GBK 下的 Unicode 崩溃。

### 12.5 UE5 预览不落盘

- 确认 `ue5_editor_cmd` 与 `.uproject`；
- 查看引擎日志与 `scripts/ue5/import_and_save.py` 输出；
- 已知限制：Commandlet 生命周期与异步导入收尾可能不稳定。

---

## 13. 扩展点

| 需求 | 修改位置 |
|---|---|
| 新增训练引擎 | `app/core/trainers.py`（适配器 + `_ENGINES` + settings） |
| 新增 DCC 桥接 | `app/dcc/xxx_bridge.py` + `registry_probe` + `exporter` |
| 新增导出格式 | `app/core/exporter.py` + `scripts/blender/mesh_to_fbx.py` |
| 新增网格后端 | `app/core/mesh.py` / `tsdf.py`，并在 `_best_meshes()` 登记优先级 |
| 新增抽帧来源 | `app/core/ingest.py` 或 `app/dcc/premiere_bridge.py` |
| 新增质量预设 | `app/views/main_window.py` 的预设表 + `settings.py` |

---

## 14. 已知限制

1. **UASSET** 依赖 UE5 编辑器与自备授权，纯 Blender 路径不适用。
2. **TSDF 侧壁偏扁**：深度为 alpha 加权近似，半透明高光表面有折中。
3. **Metashape 修洞** 需 Professional 版脚本接口，默认关闭。
4. **UE5 预览** 的异步导入收尾仍不稳定，可能不落盘。
5. **大场景性能**：150k 面 + 4096 贴图体积较大，可用 LOD 或降低贴图分辨率。
6. **测试脚本** 中 `unit_ue5_autoproject.py`、`unit_scenario_fallback.py`
   需要对应商业软件，未安装时无法执行。
