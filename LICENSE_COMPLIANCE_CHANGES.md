# 侵权风险隔离与合规改造（LICENSE_COMPLIANCE_CHANGES.md）

- 对象：`universal_scene_forge/` 当前工作树（本地源码为唯一事实来源）
- 时间：2026-10-21
- 原则：**“不可以侵权”为硬约束**；只做“拒绝执行 / 跳过 / 提示 / 隔离”，不绕过、不模拟任何许可证校验。
- 配套：`THIRD_PARTY_LICENSES.md`（清单）、`SECURITY_AUDIT.md`（安全审计）、`BLENDER_PIPELINE_TEST.md`（运行证据）
- 免责：本文是工程合规记录，不构成法律意见；商用/分发前请由法务复核。

---

## 0. 处置矩阵（总览）

| # | 风险组件 | 当前使用方式 | 风险等级 | 核心代码变更 | 用户侧义务 |
|---|---|---|---|---|---|
| 3.1 | gaussian-splatting（Inria/MPII） | **仅子进程**调用 `external/gaussian-splatting/train.py`；不 import、不链接、不修改 | 🔴 高（非商业许可） | `app/core/gaussian.py:87` 强制许可门；`app/utils/licensing.py:108-160`；CLI/GUI 确认与商用阻断 | 仅非商业用途；商用须取得 Inria 书面授权；再分发须附其完整许可 |
| 3.2 | FFmpeg（gyan.dev，GPLv3） | 不打包；用户自备或依赖体检按需下载；子进程调用 | 🔴 高（GPLv3） | `app/dcc/auto_installer.py:34-46,116-124,232-237,257-267`；`licensing.py:166-197` | 分发含 FFmpeg 的整包须履行 GPLv3；否则改用 LGPL 构建或让用户自装 |
| 3.3 | plyfile（GPL-3.0+） | **已从 USF 自有代码移除**；PLY 读写改为自研 MIT `app/utils/ply_io.py` | 🟢 低（替换后） | `requirements.txt:11-15`、`app/utils/ply_io.py:1-17`、`app/core/mesh.py:13`、`app/core/gaussian_filter.py:38`、`pipeline_controller.py:338-339` | 无 GPL 义务；`external/` 训练环境中的 plyfile 属上游独立程序 |
| 3.4 | RizomUV / Metashape / UE5 | 可选；仅检测用户自备授权，检测不到即 `StageSkipped` | 🟡 中（商业 EULA） | `app/utils/licensing.py:36-52,203-251`；调用点 `pipeline_controller.py:395-397,425-427,494-496` | 自备有效许可证；遵守各软件 EULA；不得共用/伪造授权 |
| 3.5 | Blender（GPL-2.0+）/ PySide6（LGPL-3.0） | Blender 仅外部子进程；PySide6 以 wheel 动态链接，未修改 Qt | 🟡 中 | `app/dcc/blender_bridge.py:57-145`；`scripts/blender/mesh_to_fbx.py:27`；`requirements.txt:6`；`THIRD_PARTY_LICENSES.md:14,66-70,76-81` | Blender 不打包则无 GPL 传染；PySide6 分发须附 LGPL 文本/源码获取方式，闭源商业分发建议 Qt 商业许可 |

新增 Python 依赖：**无**。`ply_io.py` 仅使用已存在的 `numpy`（BSD-3-Clause），与 MIT 自有代码兼容。

---

## 3.1 gaussian-splatting（Inria / MPII，仅限非商业）

### 当前使用方式

- 组件位于 `external/gaussian-splatting/`，USF **不 vendor**、不修改其源码。
- 调用方式：`app/core/gaussian.py:100-109` 构造参数列表，`:129-133` `subprocess.Popen`，工作目录 `cwd=external/gaussian-splatting`，stdout/stderr 合并捕获。
- 与 USF 进程的边界：全仓库检索无 `import gaussian_splatting` / `from gaussian...` / 动态 `sys.path` 注入（证据见 §5）；仅执行其 `train.py`。
- 环境隔离：`app/core/gaussian.py:122-125` 使用 `scrubbed_env`，剥离 `PYTHONPATH`/`PYTHONHOME`，只保留 CUDA 可见性。

### 风险等级

🔴 **高**：其许可证为自定义的 Gaussian-Splatting License，仅允许研究/评估等非商业用途；商用必须取得 Inria 书面授权。

### 代码变更（硬门）

1. **执行前强制许可门**：`app/core/gaussian.py:87`
   ```python
   licensing.require_3dgs_license(context="gaussian-splatting 训练")
   ```
   该门在任何 `Popen` 之前执行；未确认或声明商用均抛 `LicenseBlocked`，不会启动训练进程。

2. **商业用途阻断**：`app/utils/licensing.py:118-127`
   - 环境变量 `USF_COMMERCIAL_USE=1/true/yes/on`（`:25,62-67`）或 `settings.commercial_use=true`；
   - 命中后给出 Inria 商业授权链接与替代引擎建议，并 `raise LicenseBlocked`。
   - CLI：`main.py:35-36`（`--commercial-use`）→ `:100-103` 写盘并记日志 → `:160` 传入。
   - GUI：`app/views/main_window.py:1006-1013`（复选框）→ `:1380-1398` 启动即阻断并弹窗。

3. **不可跳过的非商业确认**：
   - GUI 每次启动重置为未勾选：`main_window.py:996-998`；未勾选直接拒绝启动：`:1399-1408`；通过后写 `settings.license_ack_3dgs=True` 并记日志：`:1423-1436`。
   - CLI 必须显式 `--accept-3dgs-license`（`main.py:33-34`），处理与写盘见 `:104-111`。
   - 环境变量 `USF_ACCEPT_3DGS_LICENSE=1`（`licensing.py:26,86-93`）。
   - 非交互且未确认 → 直接拒绝（`licensing.py:154-160`），不做默认放行。

4. **外部点云合规旁路**：`--model` 注入已有点云时，不执行 3DGS（`pipeline_controller.py` 的 `--model` 分支）；E2E 日志明确记录：
   ```
   build/audit/e2e/cli_stdout_final2.log:40-41
   [LICENSE] 外部点云路径不执行 gaussian-splatting: ...point_cloud.ply
   [SKIP] 3D 高斯泼溅训练 跳过: 使用 --model 注入点云, 未执行 3DGS 训练
   ```

### 实测证据

`build/audit/security_selftest.log`：

```
[PASS] licensing 商业用途阻断 3DGS -> LicenseBlocked: 拒绝执行 3DGS: 已声明商业用途（来源: 环境变量 USF_COMMERCIAL_USE=1）...
```

E2E 正常非商业路径（`build/audit/e2e/cli_exit1_redacted.log:41-42`）：

```
[LICENSE] 训练器 builtin → builtin 回退: 已记录非商业用途确认 (settings.license_ack_3dgs=True, env=)
[LICENSE] gaussian-splatting 训练: 已记录非商业用途确认 (settings.license_ack_3dgs=True, env=)
```

### 用户侧义务

- 仅将 gaussian-splatting 用于研究/评估等非商业目的；商用前取得 Inria 书面授权（`licensing.py:32-33` 给出链接）。
- 若再分发其代码/模型/训练产物，附带其完整许可证并保留声明；不得移除版权与许可文本。
- USF 不提供、也不代替用户判断“是否商用”；用户勾选/参数即声明，记录写入 `logs/usf_*.log` 与 `settings.json`。

---

## 3.2 FFmpeg（gyan.dev 构建，GPLv3）

### 当前使用方式

- 仓库与安装包**不含** FFmpeg 二进制（`requirements.txt` 无；安装脚本未打包）。
- 两种来源：用户自备 `settings.ffmpeg_exe`；或「依赖体检」按需下载到 `tools_root`。
- 调用方式：`app/core/ingest.py:163-184` 列表形式 `Popen`，`shell=False`，`scrubbed_env`，超时兜底。

### 风险等级

🔴 **高**：gyan.dev essentials 构建按 **GPLv3** 授权；若随包分发，整包触发 GPLv3 义务。

### 代码变更

1. **下载前展示许可证与下载源**：`app/dcc/auto_installer.py:232-237`
   ```python
   log.info("[LICENSE] 准备下载 %s\n%s", key, source_notice(key))
   if key == "ffmpeg" and not licensing.confirm_ffmpeg_download(...):
       log.warning("FFmpeg GPLv3 未确认, 取消下载")
       return None
   ```
   `source_notice`（`:116-124`）输出组件名、许可证、用户义务、全部下载 URL；`LICENSE_INFO`（`:34-46`）明确“USF 不打包/不分发；商业闭源分发请改用 LGPL 构建”。

2. **GPLv3 确认不可静默**：`app/utils/licensing.py:166-197`
   - `ffmpeg_gpl_notice()`（`:166-173`）给出 GPLv3 义务摘要；
   - `confirm_ffmpeg_download()`（`:176-197`）：`USF_ACCEPT_FFMPEG_GPL=1` 放行；TTY 需输入 `YES`；**非交互默认拒绝**（`:195-197`）。
   - GUI 安装线程把 `consent_cb` 接到确认对话框；CLI/无头场景不会自动同意。

3. **完整性校验**：下载后计算 SHA-256（`auto_installer.py:257-267`）。当前三个官方源均未配置固定哈希，因此**仅告警不阻断**（S-02，仍是最高优先级待办）；已禁用第三方转发镜像（`:48-49,126-141`）。

4. **“调用”与“分发”区分**：`THIRD_PARTY_LICENSES.md:56-64` 明确当前为“调用”（独立程序聚合，不传染 USF MIT 代码）；若未来打包，须整包 GPLv3 合规或改用 LGPL 构建。

### 实测证据

`build/audit/security_selftest.log`：

```
[PASS] confirm_ffmpeg_download 非交互默认拒绝 -> False
```

静态复现：

```powershell
rg -n "confirm_ffmpeg_download|ffmpeg_gpl_notice|source_notice|sha256" universal_scene_forge\app\dcc\auto_installer.py universal_scene_forge\app\utils\licensing.py
```

### 用户侧义务

- 自行获取 FFmpeg 时遵守其构建的许可证（gyan.dev essentials 为 GPLv3）。
- 若把 FFmpeg 放入对外分发的安装包：提供对应源码/许可文本、允许再分发，或改用 LGPL 构建。
- 不将 USF 的 MIT 代码与 GPLv3 二进制静态链接（当前为子进程调用，不触发）。

---

## 3.3 plyfile（GPL-3.0+）

### 当前使用方式

- **USF 自有代码不再 import plyfile**（全仓库检索 0 命中，见 §5）。
- PLY 读写由自研 MIT 模块 `app/utils/ply_io.py` 承担，支持 ascii/binary LE/BE、标量/列表属性、3DGS 常见布局，并设置 `MAX_ELEMENT_COUNT`/`MAX_PLY_BYTES` 边界（`:25-26`）。
- 使用点：`app/core/mesh.py:13`、`app/core/gaussian_filter.py:38`、`app/controllers/pipeline_controller.py:338-339`。
- `external/gaussian-splatting` 训练环境仍可能需要上游 `plyfile`，但它在**独立 venv/子进程**中，属其上游依赖，不进入 USF 进程、不随 GUI 壳分发。

### 风险等级

🟢 **低（替换后）**；替换前为 🔴 GPL-3.0+。

### 代码变更

1. `requirements.txt:11-15`：删除 `plyfile`，并注明原因与替代实现。
2. `app/utils/ply_io.py:1-17`：文件头明确“USF 自研，MIT；替代 GPL-3.0+ 的 plyfile”。
3. 调用点改用 `PlyData`（MIT）：`app/core/mesh.py:13,36`、`app/core/gaussian_filter.py:38,66,133`、`pipeline_controller.py:338-339`。
4. `THIRD_PARTY_LICENSES.md:17`：标注“已从 USF 自有代码移除；仅 external 训练环境使用”。

### 实测证据

```powershell
& build\audit\venv\Scripts\python.exe build\audit\test_ply_io.py
PLY_TEST_EXIT=0
test_bin.ply: OK size=68470 text=False
test_ascii.ply: OK size=194413 text=True
filtered.ply: OK 334 vertices
PLY_IO_TEST_OK
```

日志：`build/audit/test_ply_io.log`。

### 用户侧义务

- USF 自有代码无 GPL 义务。
- 若用户自行在 `external/gaussian-splatting` 环境安装 plyfile，该环境中的 GPL 义务由用户/上游承担；不要把该环境与 USF 壳一起闭源分发。

---

## 3.4 RizomUV / Metashape / UE5（商业、可选）

### 当前使用方式

- 三者均为**可选阶段**；USF 不提供、不内置、不模拟其许可证。
- 授权检测：`app/utils/licensing.py:203-251`
  - `USF_<COMPONENT>_LICENSE=<授权文件路径>` 或 `USF_LICENSE_DIR=<目录>` 下放 `rizomuv.lic` / `metashape.lic` / `ue5.lic`（`:36-52`）；
  - `detect_optional_license`（`:218-238`）仅检查文件存在与可读性，**不解析、不伪造**授权；
  - `require_optional_license`（`:241-251`）未检测到即 `raise LicenseBlocked`。
- 调用点：
  - RizomUV：`pipeline_controller.py:395-397`；
  - UE5：`pipeline_controller.py:425-427`；
  - Metashape：`pipeline_controller.py:494-496`。
- `LicenseBlocked` 被转换为 `StageSkipped`，流水线继续并记录原因（非失败、不亮红灯）。

### 风险等级

🟡 **中**：商业软件 EULA/授权限制；未授权使用可能构成侵权。

### 实测证据（跳过而非 mock）

`build/audit/e2e/cli_stdout_final2.log:55-59`：

```
[LICENSE] 跳过 rizomuv: RizomUV VS/RS: 未提供授权信息。请设置 USF_RIZOMUV_LICENSE=...
[SKIP] RizomUV 自动展UV 跳过: RizomUV 授权未检测到, 已跳过...
[LICENSE] 跳过 ue5: Unreal Engine 5: 未提供授权信息...
[SKIP] UE5 实时预览推送 跳过...
build/audit/e2e/cli_stdout_final2.log:190
[SKIP] Metashape 洞穴修复 跳过: 未启用 Metashape 洞穴修复
```

`build/audit/security_selftest.log`：

```
[PASS] require_optional_license 无授权拒绝 -> LicenseBlocked: ...
```

### 用户侧义务

- 自备有效许可证，并按各软件 EULA 使用；不得共享、转借、伪造授权文件。
- 未授权时接受 USF 的跳过行为，不得要求 USF 绕过校验。

---

## 3.5 Blender（GPL-2.0+）与 PySide6（LGPL-3.0）

### 当前使用方式

- **Blender**：仅通过 `blender --background --python <script.py>` 启动独立子进程；USF 自有代码不 `import bpy`、不链接 Blender 模块。
  - 桥接：`app/dcc/blender_bridge.py:57-145`（`resolve_executable`、参数 JSON、列表命令、`scrubbed_env`、超时、退出码、产物校验）。
  - 脚本：`scripts/blender/mesh_to_fbx.py`，其中唯一 `import bpy` 位于 `:27`，由 Blender 内嵌解释器执行。
  - 全仓库检索 `import bpy` 仅此 1 处（证据见 §5）。
- **PySide6**：以 pip wheel 安装（本机 6.11.0，`build/audit/pyside6_evidence.log`），动态链接 Qt，未修改 Qt 源码；`requirements.txt:6` 仅声明 `PySide6>=6.5.0`。

### 风险等级

🟡 **中**：Blender 为 GPL-2.0+；PySide6 为 LGPL-3.0（或 GPL）。

### 代码/文档变更

1. 维持子进程模式：`app/dcc/blender_bridge.py:57-145`；未把 Blender 代码复制进 USF。
2. 禁止 `import bpy`：USF 应用代码 0 处；仅 `scripts/blender/mesh_to_fbx.py:27`（该文件只在 Blender 进程内运行）。
3. `THIRD_PARTY_LICENSES.md:66-70`：说明 Blender 调用边界与打包义务。
4. `THIRD_PARTY_LICENSES.md:14,76-81`：PySide6 版本实测值、LGPL 义务、商业闭源分发建议购买 Qt 商业许可；确认未修改 Qt。

### 实测证据

```
build/audit/pyside6_evidence.log
PySide6 6.11.0
file D:\ProgramData\anaconda3\Lib\site-packages\PySide6\__init__.py
Version: 6.11.0
```

Blender 子进程证据见 `BLENDER_PIPELINE_TEST.md`（版本、命令、退出码、耗时、产物校验）。

### 用户侧义务

- 不把 Blender 本体打入 USF 安装包；若必须打包，整包满足 GPL-2.0+（提供源码/许可）。
- 分发 PySide6 时附 LGPL 文本与 Qt/PySide6 源码获取方式；允许用户替换 LGPL 库。
- 若商业闭源分发且不愿承担 LGPL 义务，向 Qt 公司购买商业许可。

---

## 4. 新增依赖合规确认

| 变更 | 许可证 | 与 MIT 兼容 | 说明 |
|---|---|---|---|
| `app/utils/ply_io.py`（自研） | MIT（与仓库 `LICENSE` 一致） | 是 | 无新增第三方代码；仅用 numpy |
| 无新增 pip 依赖 | — | — | `requirements.txt` 未新增包 |

`LICENSE` 为 MIT（`LICENSE:1-3`，Copyright (c) 2026 CYM）。

---

## 5. 静态复核命令与结果

```powershell
# 1) 自有代码不得 import plyfile / bpy
rg -n "^\s*(import plyfile|from plyfile)" universal_scene_forge --glob "*.py"
# 结果: 无匹配

rg -n "^\s*(import bpy|from bpy)" universal_scene_forge --glob "*.py"
# 结果: scripts/blender/mesh_to_fbx.py:27: import bpy（仅脚本，Blender 进程内）

# 2) 不得 import / 动态加载 gaussian-splatting 代码
rg -n "(import|from)\s+(gaussian|diff_gaussian|simple_knn)|sys\.path.*gaussian" universal_scene_forge --glob "*.py"
# 结果: 无匹配

# 3) 许可证门调用点
rg -n "require_3dgs_license|require_optional_license|confirm_ffmpeg_download" universal_scene_forge\app
```

---

## 6. 已知限制 / 未实测项

1. **Metashape 未真实执行**：本机未安装且无授权；按合规要求跳过，未用 mock 冒充（`BLENDER_PIPELINE_TEST.md` §4）。
2. **RizomUV / UE5 未真实执行**：同上，仅验证“检测不到授权 → 跳过 + 记录原因”。
3. **3DGS 训练未真实执行**：本机 `external/gaussian-splatting/.venv` 缺少可用解释器（E2E 失败用例退出码 103），且为避免非商业许可争议，E2E 使用 `--model` 注入点云。许可证门本身已由 `security_selftest` 实测。
4. **FFmpeg 下载未真实触发**：为避免真实下载与上游风控，仅做静态审计与“非交互拒绝”单测；GPL 确认逻辑已实测。
5. **PySide6 许可证文本未随仓库分发**：安装包需在发布流程中附加 LGPL 文本与源码获取方式（当前仓库仅文档说明）。
6. **无 git 可执行文件**：本机 `git` 不可用，无法提供 branch/commit；但 `.git` 完整，已用 dulwich 生成 HEAD → 工作树的真实 diff `build/audit/usf_git_diff.diff`（46 文件，5,859 行），另有安全专项子集 `usf_audit_changes.diff`（19 文件，3,300 行）。拆分建议见 `SECURITY_AUDIT.md` 附录 C。

---

## 7. EXE 打包合规处置与实测（2026-10-22）

### 7.1 打包前发现的争议代码（已修改）

| 位置 | 争议点 | 处置 |
|---|---|---|
| `installer/UniversalSceneForge.spec:34`（修复前） | `hiddenimports` 含 `"plyfile"`，会强制把 **GPL-3.0+** 依赖打进 EXE | 删除该 hiddenimport；并在 `excludes` 增加 `plyfile`/`plyfile.plyfile`/`plyfile.plytypes`（`:86-88`） |
| `installer/build_windows.bat:37-42`（修复前） | 构建后 `robocopy external\gaussian-splatting` 到 dist，等于**分发 Inria 非商业源码** | 删除同步逻辑；新增 `[3.5/4]` 合规断言：dist 出现 `external\` 或 `plyfile` 即构建失败（`:37-51`） |
| `installer/usf_setup.nsi:64-67`（修复前） | 全新安装时 `File /r "${DISTDIR}\external"`，把 Inria 源码装进用户机器 | 删除该分支；主拷贝继续 `/x external`，注释明确“永不安装 external”（`:61-65`） |
| `NOTICE:15`（修复前） | 把 plyfile 列为 USF 运行时依赖 | 改为“plyfile 不是 USF 依赖、不打包，仅可能存在于用户自建的 external 训练环境”（`:15-18`） |
| `README.md:36`（修复前） | 把 plyfile 列为 Python 依赖 | 同上，明确不随工具打包（`:36-37`） |
| `docs/ARCHITECTURE.md:153`、`docs/user_manual.html:1771`（修复前） | 宣称 plyfile 内置于 EXE | 改为“不打包/不随 exe 分发” |

### 7.2 打包增强（合规与可启动性）

- **许可证随包**：`installer/UniversalSceneForge.spec:52-55` 将 `LICENSE`、`THIRD_PARTY_LICENSES.md`、`NOTICE` 加入 `datas`，绿色目录内直接可见（`dist/.../_internal/`）。
- **PySide6/Shiboken**：`spec:61-64` 显式收集 `shiboken6`/`shiboken6.Shiboken`；`spec:17-19` 把 conda 的 `Library\bin` 前置到构建 `PATH`，否则 Qt6/Shiboken DLL 不会被依赖扫描收集。
- **open3d 运行期补装**：`spec:65-72` 为 open3d 的启动链补齐 stdlib 模块（`uuid`、`zoneinfo` 等），避免冻结后 `No module named ...`。open3d 本体仍按既有设计不打包，由「依赖体检」补装（MIT，可分发）。
- **诊断可读性**：`app/core/mesh.py:71-74` 的异常现在携带 open3d 导入根因；`main.py:139-143` 预载失败写 WARNING，不再静默吞掉。

### 7.3 实测结果

| 项目 | 结果 | 证据 |
|---|---|---|
| PyInstaller | 6.22.3 / Python 3.14.6 | `build/audit/package_build.log` |
| onedir 产物 | `dist/UniversalSceneForge/`，162,760,865 字节 | 同上 |
| 单文件安装包 | `installer/UniversalSceneForge_0.9.8_Preview_Setup.exe`（0.9.8 重建） | `build/audit/package_nsis.log` |
| 禁止项扫描 | dist 内无 `external/`、无 `plyfile/`；`COLLECT/PKG/EXE-00.toc` 均 0 命中 | §7.4 命令 |
| EXE 启动 | `--version` 退出码 0；GUI 进程持续运行（6s 后人工终止） | `build/audit/exe_version_stdout.log` |
| EXE 端到端 | `--cli` 退出码 **0**，Blender 两次退出码 0，18 个产物（含 USDA ×3、GLB TANGENT ×3） | `build/audit/exe_e2e/work10/output` |
| EXE 产物再导入 | FBX/OBJ/GLB/USDA/BLEND 共 13 个文件全部 `[CHECK] OK` | `build/audit/exe_e2e/validate10.log` |

EXE 端到端命令（隔离 `USERPROFILE`，避免沙箱拦截用户目录写入）：

```powershell
$env:USERPROFILE = '<repo>\build\audit\exe_home'
& 'dist\UniversalSceneForge\UniversalSceneForge.exe' --cli `
  --input 'build\audit\e2e\input_frames' `
  --output 'build\audit\exe_e2e\work10' `
  --model 'build\audit\e2e\model\point_cloud\iteration_7000\point_cloud.ply' `
  --accept-3dgs-license
# EXIT=0
```

### 7.4 复核命令

```powershell
# 1) 打包清单不得含 GPL/非商业组件
Select-String -Path 'build\UniversalSceneForge\COLLECT-00.toc','build\UniversalSceneForge\PKG-00.toc','build\UniversalSceneForge\EXE-00.toc' -Pattern 'plyfile|gaussian-splatting'
# 结果: 0 命中

# 2) 产物目录不得含 external / plyfile
Test-Path 'dist\UniversalSceneForge\external'              # False
Test-Path 'dist\UniversalSceneForge\_internal\plyfile'     # False

# 3) EXE 启动
& 'dist\UniversalSceneForge\UniversalSceneForge.exe' --version   # exit 0
```

---

## 8. 无法修改 / 不可处置项清单（已标注）

以下内容属于第三方上游，USF **不能修改其代码或许可证**；处置方式只能是“隔离、不打包、检测授权、提示义务”。

| # | 对象 | 为什么不能修改 | 当前处置 | 风险 |
|---|---|---|---|---|
| 1 | `external/gaussian-splatting/`（含 `LICENSE.md`、`train.py`、`scene/` 等） | 版权归 Inria/MPII，许可证仅授予非商业研究/评估用途，且禁止再许可 | 保留上游原样；**不打包**；仅子进程调用；商用阻断；用户自行克隆 | 🔴 高 |
| 2 | `external/gaussian-splatting/submodules/simple-knn/` | 上游**无 LICENSE 文件**，默认“保留所有权利”，法律状态不明 | 不修改、不打包；随上游 `--recursive` 由用户自行获取；见 `THIRD_PARTY_LICENSES.md:47` | 🔴 高（需人工法务确认） |
| 3 | `tools/colmap/COLMAP-3.9.1-windows-cuda/` | 用户自备的第三方二进制（BSD-3-Clause，但捆绑 Qt/Ceres/CUDA 等） | **不打包**；运行期探测；若再分发须附完整声明 | 🟡 中 |
| 4 | FFmpeg（gyan.dev GPLv3） | 上游二进制，GPLv3 | 不打包；下载前 GPL 确认；用户自备或 LGPL 构建 | 🔴 高 |
| 5 | Blender（GPL-2.0+） | 上游程序，GPL | 仅 `--background` 子进程；不 import bpy、不打包本体 | 🟡 中 |
| 6 | Metashape / RizomUV / UE5 | 商业软件，EULA/授权限制 | 仅调用官方 API/CLI；授权检测桩；无授权即 `StageSkipped` | 🟡 中 |
| 7 | PySide6 / Qt（LGPL-3.0） | Qt 公司版权，不能改许可 | pip wheel 动态链接、未修改 Qt；随包附 LGPL 文本与源码获取方式；商业闭源建议购买 Qt 商业许可 | 🟡 中 |
| 8 | `installer/usf.ico` | 项目自带资产，源码树未标注来源 | 保留；**发布前需人工确认图标版权**（低风险待办） | 🟢 低（待确认） |

> 说明：第 1/2 项即使“不修改”也仍有分发风险，因此本次已从构建脚本、NSIS 脚本、PyInstaller 清单三处同时切断；旧版 `0.9.5/0.9.6/0.9.7` 安装包因包含 external 内容已删除，仅保留重新构建的 `0.9.8`。
