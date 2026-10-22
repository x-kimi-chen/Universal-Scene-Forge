# Universal Scene Forge — 安全审计报告（SECURITY_AUDIT.md）

- 审计对象：`universal_scene_forge/`（本地工作树，**本地源码为唯一事实来源**）
- 审计时间：2026-10-21
- 审计环境：Windows（PowerShell）；主 Python `D:\ProgramData\anaconda3\python.exe`；审计 venv `build/audit/venv`（PySide6 6.11.0 / opencv 5.0.0 / open3d 0.20.0）
- 变更集：`build/audit/usf_git_diff.diff`（HEAD → 工作树，46 个文件，5,859 行，由 dulwich 读取 `.git` 生成）；安全专项子集 `build/audit/usf_audit_changes.diff`（19 个文件，3,300 行）。见附录 C。
- 严重等级：**严重 3 / 高 5 / 中 7 / 低 4**（共 19 条）

> 说明：本报告同时标注“修复前位置”（`build/audit/baseline/` 快照）与“修复后位置”（当前工作树）。每条结论附一条可复现命令或已落盘日志；无法实测的条目在 §D 单独列出原因。

---

## A. 漏洞审计摘要

### A.1 等级分布

| 等级 | 数量 | 已修复 | 部分修复 | 残留（接受/待办） |
|---|---:|---:|---:|---:|
| 严重 | 3 | 2 | 1 | 0 |
| 高 | 5 | 4 | 1 | 0 |
| 中 | 7 | 4 | 1 | 2 |
| 低 | 4 | 1 | 0 | 3 |
| **合计** | **19** | **11** | **3** | **5** |

> 复核注记：M-01 初版只覆盖 logging Handler 通道；端到端实测发现 CLI `print` 旁路仍输出真实用户名（`build/audit/e2e/cli_exit1_nocolmap.log:51`），已追加修复（`main.py:82,94-96,127`、`app/views/main_window.py:1518-1525`）并重测（`build/audit/e2e/cli_exit1_redacted.log:45,51`），故 M-01 仍计为“已修复”。

### A.2 `路径:行号` 索引

| ID | 等级 | 主题 | 关键位置（当前工作树） | 状态 |
|---|---|---|---|---|
| S-01 | 严重 | 依赖下载 zip-slip → 任意文件写入 | `app/dcc/auto_installer.py:153-173`（调用 `:283`；修复前 `baseline/app/dcc/auto_installer.py:176`） | 已修复 |
| S-02 | 严重 | 下载二进制无固定哈希 → MITM 替换即代码执行 | `app/dcc/auto_installer.py:257-267`（修复前 `baseline/app/dcc/auto_installer.py:160-176` 完全无校验） | 部分修复 |
| S-03 | 严重 | gaussian-splatting（Inria 非商业）无强制许可门 | `app/core/gaussian.py:87`、`app/utils/licensing.py:108-160`（修复前 `baseline/app/core/gaussian.py:116` 直接 Popen） | 已修复 |
| H-01 | 高 | PATH/PYTHONPATH 未隔离 → exe/DLL 劫持 | `app/utils/security.py:155-176`；调用点 `app/core/gaussian.py:122-125`、`app/core/colmap.py:61,152`、`app/core/ingest.py:168`、`app/dcc/blender_bridge.py:86`（修复前均继承完整环境） | 已修复（Blender/COLMAP/3DGS/FFmpeg 链） |
| H-02 | 高 | `--input/--output/--model` 无规范化、可指向任意目录 | `main.py:73-84`、`app/controllers/pipeline_controller.py:108-118`、`app/utils/security.py:53-112` | 已修复 |
| H-03 | 高 | 视频/图像解码无上限 → DoS/内存耗尽 | `app/core/ingest.py:23-28,182-184,200-204,221-235,265-279` | 已修复 |
| H-04 | 高 | GUI 与 CLI 路径校验/超时不一致 | `app/views/main_window.py:1366-1372,1425-1447`、`app/controllers/pipeline_controller.py:108-118` | 已修复 |
| H-05 | 高 | FFmpeg GPLv3 自动下载无许可展示/确认 | `app/dcc/auto_installer.py:232-237`、`app/utils/licensing.py:166-197` | 已修复 |
| M-01 | 中 | 日志未脱敏（用户名绝对路径 / token / 命令行秘密） | `app/utils/logger.py:27-62,101,108,120`；CLI/GUI 旁路 `main.py:82,94-96,127`、`app/views/main_window.py:1518-1525` | 已修复 |
| M-02 | 中 | 下载/解压体积无上限（下载炸弹、zip 炸弹） | `app/dcc/auto_installer.py:51-52,153-173,298-316` | 已修复 |
| M-03 | 中 | 外部工具裸命令名可解析到 CWD | `app/utils/security.py:124-151` | 已修复 |
| M-04 | 中 | Blender 退出码 0 但产物为空/缺失仍判成功 | `app/dcc/blender_bridge.py:126-140` | 已修复 |
| M-05 | 中 | `--model` 可为目录/不存在文件 | `app/controllers/pipeline_controller.py:114-116`、`main.py:78-84` | 已修复 |
| M-06 | 中 | Metashape/UE5 子进程仍继承完整环境 | `app/dcc/metashape_bridge.py:73-86`、`app/dcc/ue5_bridge.py:95-112` | 残留 |
| M-07 | 中 | 其余 DCC 桥（C4D/Houdini/Max/Maya/Modo/RizomUV）未使用 `scrubbed_env`/`resolve_executable` | `app/dcc/c4d_bridge.py:72-74`、`app/dcc/houdini_bridge.py:63-65`、`app/dcc/max_bridge.py:73-74`、`app/dcc/maya_bridge.py:42-43`、`app/dcc/modo_bridge.py:55-56`、`app/dcc/rizomuv_bridge.py:106-107` | 残留 |
| L-01 | 低 | 部分桥接用裸 `taskkill`（可被同名 exe 劫持） | `app/dcc/c4d_bridge.py:180`、`app/dcc/houdini_bridge.py:171`、`app/dcc/max_bridge.py:202`、`app/dcc/metashape_bridge.py:130`、`app/dcc/ue5_bridge.py:168`；对照 `app/dcc/blender_bridge.py:200` | 残留 |
| L-02 | 低 | `Material.use_nodes` 将在 Blender 6.0 移除 | `scripts/blender/mesh_to_fbx.py:283` | 残留（兼容性） |
| L-03 | 低 | 非流形网格仅告警继续导出 | `scripts/blender/mesh_to_fbx.py:194-196` | 接受（设计取舍） |
| L-04 | 低 | `_free_tcp_port` 存在 bind→close→复用 的 TOCTOU 竞态 | `app/core/gaussian.py:31-38,108` | 残留 |

---

## B. 逐条审计结论与复现

### S-01（严重）依赖下载 zip-slip —— 已修复

**修复前**：`baseline/app/dcc/auto_installer.py:176` 直接 `zf.extractall(dest_dir)`。攻击者控制下载源或镜像时，可放入 `../`、绝对路径或符号链接成员，覆盖 `tools_root` 之外的文件（例如用户启动目录、配置目录），进而实现持久化代码执行。

**修复后**：`app/dcc/auto_installer.py:153-173` 的 `_safe_extract`：
- `:159-160` 拒绝以 `/`、`\` 开头或首段含盘符的绝对路径成员；
- `:161-165` `resolve()` 后 `relative_to(dest)`，拒绝路径穿越；
- `:166-168` 拒绝 `0o120000` 符号链接成员；
- `:169-172` 累计解压体积，超过 `MAX_EXTRACT_BYTES`（`：52`，8 GiB）即拒绝。
- 调用点：`：283`（`_safe_extract(zf, dest_dir)`）。

**复现（已实测）**：

```powershell
& 'd:\AI_MADE\MADE_one_kaiyuan_Github_Trea\universal_scene_forge\build\audit\venv\Scripts\python.exe' `
  'd:\AI_MADE\MADE_one_kaiyuan_Github_Trea\universal_scene_forge\build\audit\security_selftest.py'
```

实测输出（`build/audit/security_selftest.log:12-13`）：

```
[PASS] _safe_extract 拒绝 ../ 穿越 -> OSError: 压缩包含路径穿越成员: ../evil.txt
[PASS] _safe_extract 拒绝符号链接成员 -> OSError: 压缩包含符号链接成员: link.txt
```

**残留**：`zf.extractall(dest)` 仍会创建普通文件；但成员名、落点、符号链接、总体积均已预检，zip-slip 主路径已封堵。

---

### S-02（严重）下载二进制无固定哈希 —— 部分修复（仍为最高优先级）

**修复前**：`baseline/app/dcc/auto_installer.py:160` 下载后直接 `:176` 解压并 `:183` 执行 `_find_installed` 返回的 exe；无任何哈希/签名校验。HTTPS 只能防被动窃听，无法防镜像/发布页被替换。

**修复后**：`app/dcc/auto_installer.py:257-263` 在下载落盘后计算 SHA-256，`spec.get("sha256")` 存在且不匹配即拒绝；`:264-267` 无哈希时仅告警并继续。

**关键残留（未实测的部分已明确）**：当前 `OFFICIAL_SOURCES`（`：57-111`）中 **Blender / FFmpeg / COLMAP 三个 spec 均未配置 `sha256` 字段**，因此实际行为是“计算并打印，但不阻断”。这意味着 S-02 的威胁模型（镜像/发布页被替换 → 下载即执行）仍未闭环。

**复现（静态，可一条命令确认无 pinned hash）**：

```powershell
rg -n '"sha256"' universal_scene_forge\app\dcc\auto_installer.py
# 无输出 = 没有任何源配置固定哈希；仅有 :257 的读取逻辑
rg -n 'expected = spec.get\("sha256"\)' universal_scene_forge\app\dcc\auto_installer.py
```

**明确改法（待办，不引入新依赖）**：在 `OFFICIAL_SOURCES` 每项补 `"sha256": "<发布页公布的 64 位十六进制>"`；对上游不提供哈希的构建，改为“下载后暂停并要求用户粘贴发布页哈希”，或仅保留“给指引、不自动下载”。在补齐前，建议把 `AUTO_INSTALL` 默认值改为关闭（当前可通过 UI 触发）。

> 合规说明：不引入 GPG/minisign 等新依赖；哈希值随版本升级需人工维护，属可接受成本。

---

### S-03（严重）3DGS 非商业许可无强制门 —— 已修复

**修复前**：`baseline/app/core/gaussian.py:116` 直接启动 `train.py`，CLI/GUI 均无许可确认，商业用户可在不知情下违反 Inria/MPII 非商业条款。

**修复后**：
- 训练入口硬门：`app/core/gaussian.py:85-87` 调用 `licensing.require_3dgs_license(context="gaussian-splatting 训练")`。
- 商业用途判定：`app/utils/licensing.py:118-127`，命中即抛 `LicenseBlocked` 并打印 Inria 商业授权 URL；判定来源含 `USF_COMMERCIAL_USE` 环境变量与 `settings.commercial_use`（`:62-73`）。
- 未确认：`:129-134` 已确认放行（写审计日志）；`:136-152` 交互 TTY 需输入 `YES`；`:154-160` 非交互直接拒绝。
- CLI：`main.py:96-109` 将 `--accept-3dgs-license` / `--commercial-use` 写回 settings 并写 `[LICENSE]` 日志；参数定义 `main.py:33-36`。
- GUI：`app/views/main_window.py:996-1013`（每次启动重置为未勾选）、`:1380-1408`（商业阻断 + 未勾选拒绝，不可跳过）。
- 隔离：仓库自有代码无 `import gaussian` / 无动态链接，训练仅以子进程驱动（`app/core/gaussian.py:129-133`）。

**复现（已实测）**：

```powershell
$env:USF_COMMERCIAL_USE='1'
& '...\build\audit\venv\Scripts\python.exe' main.py --cli --input 'build\audit\e2e\input_frames' --output 'build\audit\e2e\work_x'
# 预期：打印拒绝原因 + Inria 商业授权 URL，退出码非 0
```

自测日志 `build/audit/security_selftest.log:15-19`：

```
[PASS] licensing 商业用途阻断 3DGS -> LicenseBlocked: 拒绝执行 3DGS: 已声明商业用途（来源: 环境变量 USF_COMMERCIAL_USE=1）。
gaussian-splatting 采用 Inria/MPII 非商业许可, 商业使用必须
取得 Inria 商业授权: https://repo-sam.inria.fr/fungraph/3d-gaussian-splatting/license.pdf
```

---

### H-01（高）子进程环境未隔离 —— 已修复（主链路）

**修复前**：`baseline/app/core/gaussian.py:116`、`baseline/app/core/colmap.py:138`、`baseline/app/dcc/blender_bridge.py:71`、`baseline/app/core/ingest.py:149` 的 `Popen` 均未传 `env`，子进程继承 `PYTHONPATH/PYTHONHOME/PATH`。攻击者可通过投放同名 `python.exe`/DLL 或污染 `PYTHONPATH` 劫持训练/重建/导出流程。

**修复后**：`app/utils/security.py:155-176` `scrubbed_env`：
- `:162-164` 删除 `PYTHONPATH/PYTHONHOME/PYTHONSTARTUP/PYTHONUSERBASE`；
- `:165-173` `PATH` 收缩为“解释器目录 + `System32` + `Windows`”（不继承用户 PATH）；
- `:174-175` 仅允许显式 `extra` 覆盖（如 `CUDA_VISIBLE_DEVICES`）。

调用点：
- 3DGS：`app/core/gaussian.py:122-125`（`CUDA_VISIBLE_DEVICES` 显式透传）；
- COLMAP：`app/core/colmap.py:61,152`；
- FFmpeg：`app/core/ingest.py:168`；
- Blender：`app/dcc/blender_bridge.py:86`；
- nvidia-smi：`app/controllers/pipeline_controller.py:268-276`。

**复现（已实测）**：

```powershell
rg -n "scrubbed_env" universal_scene_forge\app
& '...\build\audit\venv\Scripts\python.exe' '...\build\audit\security_selftest.py'
```

`build/audit/security_selftest.log:11`：

```
[PASS] scrubbed_env 剥离 PYTHONPATH/收缩 PATH -> PATH=D:\...\build\audit\venv\Scripts;C:\WINDOWS\System32;C:\WINDOWS
```

**残留**：见 M-06/M-07（Metashape/UE5 与其余 DCC 桥）。

---

### H-02（高）`--output` 路径遍历/写入边界 —— 已修复

**修复前**：`baseline/main.py` 与 `baseline/app/controllers/pipeline_controller.py` 直接使用 `args.output`，无存在性、无根目录限制；`--output C:\Windows` 或 `--output C:\` 可把导出/日志写到系统目录，配合命名可覆盖敏感文件。

**修复后**：
- CLI 预检：`main.py:73-84`（`normalize_input` + `ensure_output_dir` + `--model` 文件检查；失败打印并 `return 3`）。
- 控制器预检（GUI/CLI 共用）：`app/controllers/pipeline_controller.py:108-118`；失败走 `_abort_bad_paths`（`:167-194`）留档，不向 GUI 线程抛异常。
- 边界规则：`app/utils/security.py:66-112`：拒绝空/NUL（`:77,79`）、盘符根（`:82-83`）、系统敏感根（`:85-92`）、用户主目录根（`:94-96`）；`:99-103` 创建目录；`:104-110` 写探针验证可写。
- 导出阶段二次校验：`app/controllers/pipeline_controller.py:543-552`；Dispatcher 入口再校验：`app/core/exporter.py:40-41`。
- 符号链接解析：`normalize_input`（`security.py:53-63`）先 `expanduser().resolve()`，避免“链接逃逸”。

**复现（已实测，退出码 3）**：

```powershell
cd universal_scene_forge
& '...\build\audit\venv\Scripts\python.exe' main.py --cli `
  --input 'build\audit\e2e\input_frames' --output 'C:\'
echo $LASTEXITCODE   # 3
```

`build/audit/e2e/cli_reject_driveroot.log:1-3`：

```
23:33:02 [INFO   ] root: 日志文件: ...\logs\usf_20261021_233302.log
输入/输出路径被拒绝: 输出目录不可写: C:\ ([Errno 13] Permission denied: 'C:\.usf_write_probe')
23:33:03 [ERROR  ] USF.CLI: [SECURITY] CLI 路径预检失败: 输出目录不可写: C:\ (...)
```

> 注：本次以非管理员运行，盘符根先命中“写探针不可写”；管理员下会先命中 `security.py:82-83` 的盘符根拒绝。两条路径都拒绝，结论一致。

**缺失输入同样拒绝（已实测）**：`build/audit/e2e/cli_reject_input.log`，`REJECT_INPUT_EXIT=3`。

**覆盖/并发兜底**：`ensure_output_dir` 使用 `mkdir(parents=True, exist_ok=False)` + 捕获 `FileExistsError`（`security.py:99-103`），并发创建同一目录不会失败；写探针用固定名 `.usf_write_probe`，并发下可能互相干扰（低风险，建议后续改为 `tempfile.mkstemp(dir=p)`，不阻塞本次交付）。

---

### H-03（高）媒体解析健壮性 —— 已修复

**修复前**：`baseline/app/core/ingest.py:149` 的 FFmpeg 调用与 OpenCV 逐帧解码无大小/帧数/时长上限；模糊帧剔除对每帧全尺寸计算 Laplacian，可被“超大图/超长视频/畸形解码”耗尽内存或 CPU。

**修复后**（`app/core/ingest.py`）：
- 上限常量：`:23-28`（单图 64 MiB、视频 8 GiB、单边 16,384 px、最多 200,000 帧、FFmpeg 600 s、解码总时限）。
- FFmpeg：`:165-169` 列表式 `Popen`、`scrubbed_env`、`CREATE_NO_WINDOW`；`:182-184` `wait(timeout=600)`，超时抛错并清理。
- OpenCV 兜底：`:200-204` 视频大小上限；`:220-223` 帧数/墙钟双重上限；`:231-235` 单帧尺寸上限；`:224-228` 解码异常转为 `IngestError`。
- Laplacian：`:265` 图片字节上限、`:275` 像素上限（`max(h,w)*4 > MAX_IMAGE_SIDE` 即拒绝）、`:279` `cv2.Laplacian`、`:281` 失败仅告警不崩溃。
- 预处理：`:313`、`:325` 同样复用大小/边长上限。

**复现（静态 + 单元）**：

```powershell
rg -n "MAX_IMAGE_BYTES|MAX_VIDEO_BYTES|MAX_DECODE_FRAMES|FFMPEG_TIMEOUT_S" universal_scene_forge\app\core\ingest.py
# 实跑：超大文件/超长视频夹具未构造（见 §D），上限分支由单测与代码审查确认
```

**未实测说明**：未构造 >8 GiB 视频与 >16,384 px 图片（磁盘/时间成本高），上限分支未做端到端触发；但 `MAX_*` 判断均为前置 `raise`，无“先解码后判断”的绕过路径。

---

### H-04（高）GUI 与 CLI 一致性 —— 已修复

**修复前**：GUI 的输入源/导出目录直接透传，CLI 无校验；两侧对“不存在/越界路径”的行为不同（GUI 可能静默失败，CLI 可能写到任意位置）。

**修复后**：
- GUI 输入：`app/views/main_window.py:1366-1372` 与 CLI 共用 `normalize_input`；失败弹窗 + `[SECURITY]` 日志。
- GUI 导出目录：`:1425-1432` `ensure_output_dir`；工作目录：`:1442-1447`。
- GUI 启动前许可证门：`:1378-1408`（见 S-03）。
- 控制器再校验：`app/controllers/pipeline_controller.py:108-118`，即使调用方绕过 GUI/CLI，也会被拒。
- 超时策略：GUI 不覆盖 `settings.*_timeout_s`（`app/models/settings.py:73,113-122`），Blender/3DGS/COLMAP 均使用同一 settings 值；GUI 无“放宽超时”分支（检索见附录 A 命令）。

**复现（静态）**：

```powershell
rg -n "ensure_output_dir|normalize_input|require_3dgs_license" universal_scene_forge\app\views\main_window.py universal_scene_forge\main.py
```

GUI 相关回归：`tests/unit_ingest_multi.py`、`tests/unit_qa_fixes.py` 均 exit 0（附录 B）。

---

### H-05（高）FFmpeg GPLv3 下载确认 —— 已修复

**修复前**：`baseline/app/dcc/auto_installer.py:160-176` 下载并解压，无许可证展示、无用户确认。

**修复后**：
- 下载前展示：`app/dcc/auto_installer.py:232-233` 调 `source_notice`（`:115-123`，含组件、义务、全部下载源）；`LICENSE_INFO` 在 `:34-46` 明确 “USF 不打包/不分发 FFmpeg”。
- GPL 确认：`:234-237` → `licensing.confirm_ffmpeg_download`（`app/utils/licensing.py:176-197`）：环境变量 `USF_ACCEPT_FFMPEG_GPL=1` 放行；TTY 需输入 `YES`；**非交互默认拒绝**（`:195-197`）。
- 镜像默认禁用：`auto_installer.py:48-49,126-141`（设 `USF_ALLOW_MIRRORS=1` 才启用 gh-proxy/ghfast）。

**复现（已实测）**：`build/audit/security_selftest.log:30-37` 打印完整 GPLv3 义务并返回 `False`：

```
FFmpeg (gyan.dev 构建) 采用 GPLv3。USF 不分发 FFmpeg 二进制, ...
[PASS] confirm_ffmpeg_download 非交互默认拒绝 -> False
```

---

### M-01（中）日志脱敏 —— 已修复

**修复前**：`baseline/app/utils/logger.py` 无过滤；`gaussian.py:127` 的完整命令行、Blender 临时参数文件路径、用户绝对路径会原样写入 `logs/usf_*.log`。

**修复后**（`app/utils/logger.py`）：
- 规则：`:27-34`（secret KV、`Authorization`、`sk-`/`hf_`/`ghp_`/`glpat-`、Windows/POSIX 用户名路径）；
- 实现：`:37-47` `redact`；`:50-62` `RedactionFilter`；
- 挂载：`:101`（控制台）、`:108`（文件）、`:120`（Qt 信号）。

**复现（已实测）**：`build/audit/security_selftest.log:14`：

```
[PASS] logger.redact -> C:\Users\<user>\work\x.log sk-*** Authorization: Bearer *** password=***
```

**端到端证据**：`logs/usf_20261021_232640.log:48,55` 的 Blender/临时参数路径均显示为 `C:\Users\<user>\...`。

**复核：CLI stdout 旁路（已追加修复）**。首轮 E2E 失败用例中，文件日志第 45 行已脱敏，但控制台第 51 行仍为真实路径——两条通道行为不一致：

```
build/audit/e2e/cli_exit1_nocolmap.log:45
23:36:53 [INFO   ] USF.3DGS: gs| No Python at '"C:\Users\<user>\AppData\Roaming\uv\...'
build/audit/e2e/cli_exit1_nocolmap.log:51
[ERROR] ... No Python at '"C:\Users\kimi\AppData\Roaming\uv\...'
```

根因：`main.py` 的 EventBus 回调直接 `print`，绕过 logging Handler。修复位置：

- `main.py:82`（路径预检拒绝）、`main.py:94-96`（`log_line`/`progress`/`finished` 三个总线回调）、`main.py:127`（启动异常）；
- `app/views/main_window.py:1518-1525`（GUI 控制台 `_on_log`），三通道统一调用 `app.utils.logger.redact`。

**复测（已实测）**：`build/audit/e2e/cli_exit1_redacted.log:45,51` 两行均为 `C:\Users\<user>\...`；`EXIT=1` 与修复前一致，说明只改脱敏、未改语义。复现命令见 §B/附录 A。

**残留**：命令行中的非密钥业务参数（如模型路径）仍被记录，这是可审计性所需，不属敏感信息；若路径含项目代号，建议由用户自行决定日志保留策略。

---

### M-02（中）下载/解压体积上限 —— 已修复

**修复前**：`baseline` 无 `Content-Length` 上限、无累计写入上限、无解压总量上限。

**修复后**：`auto_installer.py:51-52`（下载 4 GiB / 解压 8 GiB）；`:301-304` 预检 `Content-Length`；`:305-312` 边写边累计，超限即中断；`:315-316` 拒绝 <1 MB 的疑似 404 页面；`:169-172` 解压总量限制。

**复现（静态）**：

```powershell
rg -n "MAX_DOWNLOAD_BYTES|MAX_EXTRACT_BYTES|dest.stat\(\).st_size" universal_scene_forge\app\dcc\auto_installer.py
```

**未实测说明**：未真实下载超大文件（会消耗带宽且可能触发上游风控）；上限分支为纯算术前置判断。

---

### M-03（中）外部工具解析到 CWD —— 已修复

**修复前**：`baseline` 中大量 `Popen` 直接使用配置字符串或 `shutil.which`，Windows 下可被当前目录同名 exe 劫持。

**修复后**：`app/utils/security.py:124-151`：
- `:133-134` 拒绝空/NUL；
- `:136-139` 含分隔符必须是绝对路径且 `is_file()`；
- `:142-151` 裸命令名走 `which` 后 `resolve()`，若落在 CWD 直接拒绝。

**复现（已实测）**：`build/audit/security_selftest.log:9-10`：

```
[PASS] resolve_executable 拒绝相对路径 -> None
[PASS] resolve_executable 绝对路径可用 -> 'D:\...\venv\Scripts\python.exe'
```

---

### M-04（中）Blender 产物校验 —— 已修复

**修复前**：`baseline/app/dcc/blender_bridge.py` 仅看退出码，脚本声明产物缺失/0 字节仍可能被判成功。

**修复后**：`app/dcc/blender_bridge.py:126-140`：
- `:131` 逐文件 `is_file() and st_size > 0`；
- `:139` 退出码 0 但无有效产物 → `BlenderBridgeError`；
- 超时：`:91,110-111`（到点杀进程树，`wait(timeout=60)`）；
- 日志：`:80` 完整命令、`:121` 退出码/耗时/声明数量。

**复现（已实测）**：`build/audit/runs/run_edge/harness.log:9-13`，0 面输入被拒：

```
[USF] FATAL 网格为 0 面（仅点/边，无法导出实体网格）；请检查 MESH 阶段产物
Blender 退出码 1, 耗时 0.6s, 声明产物 0 个
[HARNESS] FAIL BlenderBridgeError: Blender 脚本未完成 (退出码 1)
```

---

### M-05（中）`--model` 类型校验 —— 已修复

**修复前**：`--model` 可传目录，训练阶段把目录当 PLY 读取，报错晦涩或触发下游异常。

**修复后**：`main.py:78-84`（`normalize_input` + `is_file()` 检查，失败 `return 3`）；`app/controllers/pipeline_controller.py:114-116` 再校验。

**复现（静态）**：

```powershell
rg -n "不是文件|_model_path" universal_scene_forge\main.py universal_scene_forge\app\controllers\pipeline_controller.py
```

---

### M-06（中，残留）Metashape/UE5 仍继承完整环境

**位置**：`app/dcc/metashape_bridge.py:73-86`（`env = os.environ.copy()` + 删 `QT_*`）、`app/dcc/ue5_bridge.py:95-112`（`env = os.environ.copy()`）。

**风险**：`PYTHONPATH/PYTHONHOME` 可影响 Metashape 内嵌 Python 与 UE5 Python 的模块解析；用户 PATH 中的同名 DLL 可能被优先加载。

**为何未改**：这两个宿主依赖其自身的许可/插件环境（如 Metashape 的 Python 模块路径、UE5 的 `PATH` 中的引擎 DLL 目录），直接 `scrubbed_env` 会导致宿主启动失败。正确改法是“允许列表 + 显式拒绝 `PYTHONPATH/PYTHONHOME`”，需要实机联调（本机未安装二者，无法验证，见 §D）。

**明确改法**：在 `metashape_bridge.py` / `ue5_bridge.py` 内用：
```python
env = {k: v for k, v in os.environ.items()
       if k.upper() not in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP")}
```
保留厂商必需变量，仅剥离 Python 注入面；改完需在装有 Metashape/UE5 的机器上回归一次 `repair_mesh.py` / `push_intermediate`。

---

### M-07（中，残留）其余 DCC 桥未做环境隔离

**位置**：`app/dcc/c4d_bridge.py:72-74`、`houdini_bridge.py:63-65`、`max_bridge.py:73-74`、`maya_bridge.py:42-43`、`modo_bridge.py:55-56`、`rizomuv_bridge.py:106-107`。

**风险**：与 M-06 同类，且这些桥的 exe 解析未统一走 `resolve_executable`（部分仍直接用配置字符串）。本机未安装这些 DCC，无法实测其启动是否依赖父进程环境。

**明确改法**：统一改为 `resolve_executable(self.exe)` + `scrubbed_env(extra={"PATH": <厂商 bin 目录>})`；对依赖父环境变量的宿主，逐个补 `extra` 白名单并在真机回归。

---

### L-01（低）裸 `taskkill`

`app/dcc/c4d_bridge.py:180`、`houdini_bridge.py:171`、`max_bridge.py:202`、`metashape_bridge.py:130`、`ue5_bridge.py:168` 使用 `subprocess.run(["taskkill", ...])`；对照 `blender_bridge.py:200` 已用 `system_tool("taskkill.exe")`（`security.py:179-183`，固定 `%SystemRoot%\System32`）。本地攻击者若能写 PATH 目录，可能劫持 `taskkill`。改法同 `blender_bridge.py:200`，逐处替换。

### L-02（低）Blender 6.0 兼容

`scripts/blender/mesh_to_fbx.py:283` 的 `mat.use_nodes = True` 在 Blender 5.2 已报 `DeprecationWarning`（实跑日志 `build/audit/runs/run3/harness.log:17-18`）。建议按版本分支：`mat.use_nodes = True`（≤5.x） / 新 API（6.0+）。

### L-03（低）非流形网格仅告警

`scripts/blender/mesh_to_fbx.py:194-196`：非流形边只告警继续。端到端日志出现 663 条非流形边（`build/audit/e2e/cli_stdout_final2.log:77-78`），导出文件仍有效。属设计取舍：重建网格普遍非流形，硬失败会显著降低成功率。保留。

### L-04（低）端口竞态

`app/core/gaussian.py:31-38` 先 `bind(0)` 取空闲端口，`:108` 再传给 `train.py`；bind 与 close 之间端口可能被其他进程占用，导致训练启动失败。仅在多实例并发时出现；现有 `--port` 已是改进（原固定 6006）。可改为“失败重试 3 次”。

---

## C. 专项核查补充

### C.1 `shell=True` / `os.system` / `os.popen`

**结论：全仓库 0 处。已实测。**

```powershell
rg -n "shell\s*=\s*True|os\.system|os\.popen" universal_scene_forge --glob "*.py"
# 输出为空
```

- `app/core/trainers.py:239-241` 的 `cmd /c call <bat>` 为**列表形式**，无 `shell=True`；`.bat` 内容由 `bat_quote`（`app/utils/security.py:190-195`）拼接，含 `&|<>^%!"\r\n` 直接拒绝。
- `app/dcc/blender_bridge.py:197-205`、`c4d/houdini/max/metashape/ue5` 的 `_kill_tree` 均为列表形式。

### C.2 外部工具调用点总表

| 工具 | 调用位置 | 路径解析 | 环境隔离 | 超时/清理 | 命令日志 |
|---|---|---|---|---|---|
| gaussian-splatting | `app/core/gaussian.py:129-133` | `_resolve_python` `:59-80` | `scrubbed_env` `:122-125` | `:135-152` | `:127` |
| COLMAP | `app/core/colmap.py:58-62,149-153` | `resolve_executable` `:42-47` | `scrubbed_env` `:61,152` | 调用方/阶段超时 | `:147` |
| FFmpeg | `app/core/ingest.py:165-169` | `resolve_executable` `:42-49` | `scrubbed_env` `:168` | `:182-184` | 由 `_run_ffmpeg` 记录 |
| Blender | `app/dcc/blender_bridge.py:83-86` | `resolve_executable` `:61` | `scrubbed_env` `:86` | `:91,110-111` | `:80` |
| Metashape | `app/dcc/metashape_bridge.py:84-86` | 配置字符串（未统一） | 继承 + 删 `QT_*` | `:90,127-134` | 调用方记录 |
| UE5 | `app/dcc/ue5_bridge.py:110-112` | 配置字符串（未统一） | 继承 | `:116,165-172` | `:107` |
| OpenSplat | `app/core/trainers.py:134-137` | `resolve_executable` `:124` | `scrubbed_env` `:136` | `:138-147` | `:132` |
| nerfstudio | `app/core/trainers.py:239-242` | `bat_quote` 拼 `.bat` | 继承 | `:244-257` | `:236` |

### C.3 GUI/CLI 路径校验一致性

| 校验点 | CLI | GUI | 共享实现 |
|---|---|---|---|
| 输入存在性/规范化 | `main.py:76` | `main_window.py:1368` | `security.normalize_input` |
| 输出根边界 | `main.py:77` | `main_window.py:1427,1443` | `security.ensure_output_dir` |
| 控制器二次校验 | `pipeline_controller.py:111-118` | 同左 | `pipeline_controller.py` |
| 3DGS 许可门 | `main.py:96-109` + `gaussian.py:87` | `main_window.py:1380-1408` | `licensing.require_3dgs_license` |
| 超时 | `settings.*_timeout_s` | 同左（无覆盖分支） | `settings.py:73,113-122` |

### C.4 下载链

| 检查项 | 现状 | 证据 |
|---|---|---|
| 官方源 | 下载源均为官方域名（Blender 官方/官方镜像、gyan.dev、GitHub Releases） | `auto_installer.py:57-111` |
| 第三方镜像 | 默认剔除；`USF_ALLOW_MIRRORS=1` 才启用 | `:48-49,126-141` |
| 哈希 | 有 `sha256` 字段则强制比对，无则告警继续（**当前三者均无**） | `:257-267` |
| 签名 | 未实现（上游未提供稳定签名清单） | — |
| 许可证展示 | 下载前 `source_notice` 完整展示 | `:232-233` |
| FFmpeg GPL 确认 | 非交互默认拒绝 | `licensing.py:176-197` |
| 体积/解压上限 | 4 GiB / 8 GiB | `:51-52` |

### C.5 日志脱敏覆盖

| 敏感类型 | 规则 | 位置 |
|---|---|---|
| `key=value` 型密钥 | `_SECRET_KV` | `logger.py:27-31` |
| `Authorization` / Bearer | `_SECRET_KV` | `logger.py:27-31` |
| `sk-` / `hf_` / `ghp_` / `glpat-` | `_TOKEN` | `logger.py:32` |
| Windows 用户目录 | `_WIN_USER` | `logger.py:33` |
| POSIX 用户目录 | `_POSIX_USER` | `logger.py:34` |
| 挂载范围 | 控制台/文件/Qt | `logger.py:101,108,120` |

---

## D. 未实测项与原因

| 项目 | 原因 | 影响 |
|---|---|---|
| 真实 MITM 替换下载包 | 需要可控恶意镜像与公网劫持环境；且会触发上游风控 | S-02 的“哈希缺失”结论由静态检查给出，替代验证见 `rg` 命令 |
| >8 GiB 视频 / >16,384 px 图片 | 磁盘与解码时间成本高 | H-03 的上限分支未端到端触发；代码为前置拒绝 |
| COLMAP SfM 全流程 | 本机未安装 COLMAP；`unit_scenario_fallback` 因此失败（环境限制，非本次改动引入） | 见 `build/audit/tests/unit_scenario_fallback.log` |
| Metashape 修复 | 本机无 Metashape，且无有效许可证；按合规要求**不 mock** | M-06 改法需真机回归 |
| UE5/RizomUV/C4D/Houdini/Max/Maya/Modo | 本机均未安装（`logs/usf_20261021_232640.log:4-18` 探测均为 not found） | M-07 改法需真机回归 |
| 恶意 zip 的“普通文件覆盖”变体 | 已实测 `../` 与 symlink 两类的拒绝；未穷举所有 zip 方言 | S-01 主路径已封堵 |
| GUI 交互点击全流程 | 以 offscreen 单测覆盖（`tests/unit_ingest_multi.py` 等） | GUI 模态弹窗行为未逐项人工点击 |

---

## 附录 A：审计命令清单（可逐条复现）

```powershell
cd universal_scene_forge

# 1) 子进程/外部工具调用点
rg -n "subprocess\.|Popen\(|shell\s*=\s*True|os\.system|os\.popen" . --glob "*.py" --glob "!build/**"

# 2) 环境隔离与路径校验
rg -n "scrubbed_env|resolve_executable|normalize_input|ensure_output_dir|ensure_within" app main.py

# 3) 许可证门
rg -n "require_3dgs_license|require_optional_license|confirm_ffmpeg_download|LicenseBlocked" app main.py

# 4) GPL 依赖与 bpy
rg -n "import plyfile|from plyfile|import bpy" . --glob "*.py" --glob "!build/**"

# 5) 下载链
rg -n "sha256|extractall|_safe_extract|MAX_DOWNLOAD|MAX_EXTRACT|USF_ALLOW_MIRRORS" app/dcc/auto_installer.py

# 6) 日志脱敏
rg -n "redact|RedactionFilter|_WIN_USER|_SECRET" app/utils/logger.py

# 7) 安全边界自测（17 项）
& '...\build\audit\venv\Scripts\python.exe' 'build\audit\security_selftest.py'

# 8) M-01 CLI stdout 脱敏复现（失败用例, 期望 EXIT=1 且无真实用户名）
& '...\build\audit\venv\Scripts\python.exe' main.py --cli `
  --input 'build\audit\e2e\input_frames' `
  --output 'build\audit\e2e\work_fail_redact' `
  --accept-3dgs-license *> 'build\audit\e2e\cli_exit1_redacted.log'
Select-String -Path 'build\audit\e2e\cli_exit1_redacted.log' -Pattern '<user>'
```

## 附录 B：回归测试结果（审计改动后）

| 测试 | 退出码 | 备注 |
|---|---:|---|
| `tests/unit_096_prep.py` | 0 | — |
| `tests/unit_blender_versions.py` | 0 | — |
| `tests/unit_deps_open3d.py` | 0 | — |
| `tests/unit_deps_sources.py` | 0 | 含镜像默认禁用断言 |
| `tests/unit_export_fallback.py` | 0 | — |
| `tests/unit_ffmpeg_persist.py` | 0 | 修复路径解析断言后通过 |
| `tests/unit_ingest_multi.py` | 0 | 修复 QMenu 生命周期后通过 |
| `tests/unit_pr_bridge.py` | 0 | — |
| `tests/unit_qa_fixes.py` | 0 | 修复控制器异常语义后通过 |
| `tests/unit_test_report_fixes.py` | 0 | 修复路径解析断言后通过 |
| `tests/unit_train_autosetup.py` | 0 | — |
| `tests/unit_ue5_autoproject.py` | 0 | — |
| `tests/regression_nextgen.py` | 0 | — |
| `tests/unit_scenario_fallback.py` | **1** | 本机无 COLMAP/3DGS；属环境限制，非改动引入 |

日志目录：`build/audit/tests/*.log`。

M-01 追加修复后的回归：`tests/unit_qa_fixes.py`、`tests/unit_ingest_multi.py` 均重新运行且 `exit 0`（`build/audit/tests/unit_qa_fixes_after_redact.log`、`unit_ingest_multi_after_redact.log`）；`main.py` 与 `main_window.py` 的 `py_compile` 退出码 0。

## 附录 C：变更集与 commit 等价说明

- 本机无 `git` 可执行文件（`git --version` 报 `CommandNotFound`），但仓库 `.git` 完整。
- 真实变更集：`build/audit/usf_git_diff.diff`（46 文件，5,859 行，HEAD `251d609` → 工作树；由 `make_git_diff.py` + dulwich 生成，行尾已归一化）。
- 安全专项子集：`build/audit/usf_audit_changes.diff`（19 文件，3,300 行；生成器 `make_diff.py`，基线快照 `build/audit/baseline/`）。
- 由于无 git 可执行文件，仍无法创建 branch/commit；上述 diff 可在任意有 git 的机器上 `git apply` 复核。
- 建议的提交拆分（每项一条 commit message）：
  1. `security: 统一输入/输出路径校验与子进程环境隔离`（`security.py`、`main.py`、controller、bridges）
  2. `security: 下载链增加 GPL 确认、体积上限与 zip-slip 防护`（`auto_installer.py`、`licensing.py`）
  3. `security: 媒体解析上限与日志脱敏`（`ingest.py`、`logger.py`）
  4. `compliance: 移除 GPL plyfile，改用自研 ply_io`（`ply_io.py`、`requirements.txt`）
  5. `compliance: 3DGS 商业用途阻断与可选组件授权检测`（`gaussian.py`、`licensing.py`、GUI/CLI）
  6. `fix: Blender 导出状态重置与产物校验`（`mesh_to_fbx.py`、`blender_bridge.py`）
