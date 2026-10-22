# 第三方组件与许可证清单（THIRD_PARTY_LICENSES.md）

本文档列出 Universal Scene Forge 涉及的第三方组件及其许可证信息，供开源合规审查与二进制分发时使用。
许可证信息均以组件自身的 LICENSE/COPYING 文件或包元数据（dist-info/METADATA）为准。

**风险标注说明**：🔴 高风险（须人工确认/限制分发）　🟡 中风险（分发时须附带声明）　🟢 低风险（宽松许可，保留声明即可）

---

## 1. GUI 壳 Python 依赖（requirements.txt，随 PyInstaller 安装包分发）

| 依赖 | 锁定版本（本机 venv） | 许可证（来源：包 METADATA） | 兼容性 | 风险 | 义务 |
|---|---|---|---|---|---|
| PySide6 / shiboken6 | 6.11.0（本机 `build/audit/pyside6_evidence.log` 实测；清单旧值 6.11.2） | LGPL-3.0-only OR GPL-2.0/3.0 | 与 MIT 兼容（动态链接/pip 依赖） | 🟡 | LGPL：分发时须提供组件许可文本与获取源码的方式；不得静态闭源链接 Qt 修改版 |
| numpy | 2.5.3 | BSD-3-Clause（含 0BSD/MIT/Zlib/CC0 子项） | 兼容 | 🟢 | 保留版权与许可声明 |
| opencv-python-headless | 5.0.0.93 | Apache-2.0 | 兼容 | 🟢 | 保留 NOTICE/许可声明 |
| ~~plyfile~~（已从 USF 自有代码移除） | — | GPL-3.0+（包内 COPYING 为 GPLv3 全文；PyPI 1.0.3 起分类器即为 GPLv3+） | **已替换**：USF 自有代码与 requirements.txt 均不再依赖 plyfile，PLY 读写改由 `app/utils/ply_io.py`（本项目原创，MIT，与仓库同许可）实现 | 🟢（替换后） | 无 GPL 义务。仅 `external/gaussian-splatting` 训练环境仍需 plyfile（其上游依赖），属独立程序子进程环境，不随 GUI 壳分发 |
| requests | 2.34.2 | Apache-2.0 | 兼容 | 🟢 | 保留许可声明 |
| tqdm | 4.70.0 | MPL-2.0 AND MIT | 兼容（文件级 copyleft，仅改其源文件才触发） | 🟢 | 保留声明 |
| open3d（可选，运行时补装） | 0.19.0 | MIT | 兼容 | 🟢 | 保留声明 |
| pillow（训练环境） | 12.3.0 | MIT-CMU | 兼容 | 🟢 | 保留声明 |
| torch / torchvision（训练环境，不随壳分发） | 2.14.0+cu130 / 0.29.0+cu130 | BSD 风格混合（Apache-2.0/BSD-2/BSD-3/BSL-1.0/MIT 及 LLVM 例外） | 兼容 | 🟢 | 保留声明；注意 torch 附带 CUDA 运行库，NVIDIA 允许再分发但须保留其声明 |

## 2. 构建工具（不随产物分发）

| 组件 | 许可证 | 风险 | 说明 |
|---|---|---|---|
| PyInstaller | GPL-2.0-or-later **with bootloader exception** | 🟢 | 该例外明确允许用 PyInstaller 打包并分发非自由/商业程序，无需开源宿主程序 |
| NSIS | zlib/libpng 许可 | 🟢 | — |
| Python（运行时） | PSF-2.0 | 🟢 | — |

## 3. 外部引擎/仓库（由最终用户自行获取，本仓库不分发）

### 3.1 gaussian-splatting（Inria / Max Planck Institut für Informatik）🔴

- **许可证**：Gaussian-Splatting License（自定义），**仅限研究/评估等非商业用途**，无再许可权；再分发必须附带其完整许可证并保留全部声明；商用须事先获得 Inria 明确书面同意（联系方式见其 LICENSE.md 第 5 节）。
- **证据**：`external/gaussian-splatting/LICENSE.md`（本地副本）及官方仓库 https://github.com/graphdeco-inria/gaussian-splatting
- **与本项目的关系**：本仓库代码**仅以子进程方式**调用其 `train.py`，不 import、不链接、不修改其源码，构成"独立程序的聚合"。
- **合规要求**：
  - 本仓库**不再 vendor 其源码**；由用户自行执行 `git clone --recursive` 获取，获取行为适用 Inria 条款。
  - 任何**商用/闭源分发/对外提供含该组件的安装包**之前，必须获得 Inria 授权 —— **需人工确认/咨询律师**。
- **其子组件**（随官方仓库 `--recursive` 一并获取，同样不在本仓库分发）：

| 子组件 | 许可证 | 风险 | 说明 |
|---|---|---|---|
| diff-gaussian-rasterization | Gaussian-Splatting License（Inria 非商业） | 🔴 | 同上 |
| **simple-knn**（gitlab.inria.fr/bkerbl/simple-knn） | **无许可证文件** | 🔴 | 默认"保留所有权利"；其使用事实上的授权来自 Inria 对 gaussian-splatting 工作流的公开说明，但法律状态不明 —— **需人工确认** |
| fused-ssim | MIT（Copyright (c) 2024 Rahul Goel） | 🟢 | — |

### 3.2 COLMAP（University of Tübingen 等）🟢→🟡

- **许可证**：BSD-3-Clause（官方仓库 LICENSE；注意其**预编译二进制**捆绑 Qt/Ceres/CUDA 等第三方组件，各有独立许可）。
- **获取方式**：运行时由「依赖体检」从官方 GitHub Releases 下载 `COLMAP-3.9.1-windows-cuda.zip`（支持镜像加速），本仓库不 vendor、不再分发。
- **义务**：若未来把 COLMAP 打入安装包，必须附带其 BSD-3-Clause 声明及捆绑组件的许可清单 —— 当前方案下无需。

### 3.3 FFmpeg 🟡

- **许可证**：gyan.dev 的 **essentials 构建（含 GPL 组件）按 GPLv3 授权**（来源：gyan.dev builds 页面声明 "All builds are 64-bit, static and licensed as GPLv3"）。
- **获取方式**：运行时由「依赖体检」从 gyan.dev / GitHub Releases 下载 `ffmpeg-release-essentials.zip`，本仓库不分发。
- **"调用" vs "分发" 的界线（硬约束）**：
  - **调用**（本项目当前形态）：USF 仅以子进程执行用户自行获取的 ffmpeg.exe，属独立程序聚合，USF 自有 MIT 代码不因此受 GPL 传染；仓库与安装包内**不含**任何 FFmpeg 二进制。
  - **分发**（若未来打包 FFmpeg）：整机/整包须满足 GPLv3（提供对应源码、许可文本、允许再分发等），并建议改用 LGPL 构建。
- **自动下载的合规门**：`app/dcc/auto_installer.py` 在下载前展示 GPLv3 义务并要求确认（CLI 交互输入 YES / 环境变量 `USF_ACCEPT_FFMPEG_GPL=1`），非交互环境默认拒绝；下载后计算 SHA-256，源配置提供 `sha256` 时强制比对（`app/dcc/auto_installer.py:257-267`）。**当前 Blender/FFmpeg/COLMAP 三个源均未配置固定哈希**，仅告警不阻断，详见 `SECURITY_AUDIT.md` 的 S-02。
- **义务**：本仓库不分发则无传染问题；若未来随机打包 FFmpeg，则整机需按 GPL-3.0 合规。

### 3.4 Blender（GPL-2.0-or-later）与 DCC 宿主

- **Blender**：以 `blender --background --python <script.py>` **外部子进程**调用；USF 自有代码**不 `import bpy`**、不链接 Blender 的 Python 模块、不修改其源码。脚本 `scripts/blender/mesh_to_fbx.py` 由 Blender 内嵌解释器执行，属 Blender 进程的一部分，不进入 USF 进程。
- **义务**：USF 仓库与安装包可维持 MIT 自有代码；若把 Blender 本体打入安装包，则整包须满足 GPL-2.0+（或改用用户自装 Blender）。
- **其他 DCC 宿主**（Maya / 3ds Max / Metashape / Cinema 4D / Houdini / UE5 / RizomUV / Premiere Pro / ZBrush 等专有商业软件）：本项目仅通过其**官方脚本 API/命令行接口**驱动，不复制、不分发、不嵌入其代码。经外部进程调用专有软件 API 并不被视为衍生作品（通说），但各软件 EULA 对自动化使用另有约定的以其为准。这些组件均为**可选**：未检测到用户自备授权时 USF 跳过并记录原因（`app/utils/licensing.py`）。

### 3.5 Adobe CEP 扩展（extensions/pr_frame_exporter）🟢

- 面板 JS 为**本项目原创的 30 行轻量封装**（直接调用 Premiere 注入的 `window.__adobe_cep__` 运行时 API），**未复制 Adobe CSInterface 库代码**，无 Adobe 许可义务。

## 4. 分发义务速查（发布安装包时）

1. 安装包内附 `LICENSE`（MIT）与本文件（已由 `usf_setup.nsi` 写入安装目录并在向导展示 MIT 许可页）。
2. plyfile 已从 USF 自有代码与 requirements.txt 移除（PLY IO 由 MIT 的 `app/utils/ply_io.py` 实现），安装包不再承担其 GPL-3.0 义务。
3. PySide6 为 LGPL-3.0：附其许可文本；提供 Qt/PySide6 官方源码获取链接。**若你希望商业闭源分发且不愿承担 LGPL 动态链接/替换义务，须向 Qt 公司购买商业许可**（https://www.qt.io/licensing/）。USF 仅经 pip wheel 动态链接 Qt，未修改 Qt 源码。
4. 不得在安装包中附带 gaussian-splatting/COLMAP/FFmpeg，除非另行满足上述 §3 的相应义务；Blender 亦同（GPL-2.0+）。
