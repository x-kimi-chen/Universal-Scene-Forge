# universal_scene_forge
Universal Scene Forge：桌面端一体化 3D 场景重建与导出工具。

**最新版本：`0.9.9-preview`** ｜ [更新日志](CHANGELOG.md) ｜ [发布说明](RELEASE_NOTES_0.9.9.md) ｜ [安全审计](SECURITY_AUDIT.md) ｜ [第三方许可](THIRD_PARTY_LICENSES.md)

**流水线（9 阶段，每阶段独立容错与超时兜底）**：
视频/图像 → 3DGS 重建 → **TSDF 深度融合网格**（无训练环境时回退泊松）→ 影像投影贴色 → RizomUV 自动展 UV（可选）→ UE5 实时预览（可选）→ Blender 后处理 → Metashape 修复（可选）→ 多格式导出（**FBX / OBJ / GLB / BLEND / USD / UASSET**，含 LOD 与导出目录设置）。

**主要特性**：
- **接近原视频的外观**：3DGS 深度 → TSDF 网格 + 原始影像投影贴色 + 4096 烘焙；黑色高光主体不再丢失
- 导出完整性保障链：3DGS 网格 → Metashape 修复网格 → 自动补跑修复，保证产出模型
- 模糊帧自动剔除（Laplacian 清晰度）、CUDA 加速开关、显卡厂商自适应（NVIDIA/AMD/Intel）
- Blender 4.x/5.x 多版本、Metashape 2.x、UE5 预览工程自动生成
- 依赖体检 + 🚀 一键自动配置全部环境（Python 库/工具/扩展/训练环境/DCC 路径）
- 运行日志落盘（logs/usf_*.log）、导出位置自定义、模糊帧剔除

Desktop all-in-one 3D reconstruction & export: video/images → 3DGS → mesh →
(optional RizomUV UV / UE5 preview / Metashape repair) → FBX/OBJ/GLB/BLEND/USD/UASSET.
Project WIP; forks and contributions welcome. 项目仍在完善，欢迎二次开发。

## 快速开始

### 方式一：Windows 安装包（推荐）

从 GitHub Releases 下载 `UniversalSceneForge_0.9.9_Preview_Setup.exe`，安装后直接启动。
绿色版下载 `UniversalSceneForge_0.9.9_Preview_portable.zip`，解压后运行 `UniversalSceneForge.exe`。
发布资产校验见 `SHA256SUMS.txt`。

### 方式二：源码运行

```bat
python -m pip install -r requirements.txt
python main.py
```

首次使用 3DGS 训练前，请按下方「训练环境」自行克隆 `external/gaussian-splatting`。
Blender / COLMAP / FFmpeg 可在 GUI「依赖体检」中探测或安装。

## 命令行 / CLI

```bat
python main.py --version
python main.py --check-deps
python main.py --cli --input a.mp4 b/ --output D:/work
```

退出码语义：`0` 全部成功；`1` 任意阶段失败；`2` 参数错误；`3` 输入源/环境阻断（未进入流水线）。

## 代码签名（Windows Authenticode，可选）

发布前可对主程序与安装包签名并校验：

```powershell
# 干跑：确认目标与可用签名方式（无需证书）
powershell -NoProfile -ExecutionPolicy Bypass -File installer\sign_windows.ps1 -DryRun

# PFX 证书（密码交互输入）
powershell -NoProfile -ExecutionPolicy Bypass -File installer\sign_windows.ps1 -PfxPath D:\certs\usf-codesign.pfx

# 证书存储指纹（EV 硬件令牌）
powershell -NoProfile -ExecutionPolicy Bypass -File installer\sign_windows.ps1 -Thumbprint <SHA1指纹>
```

脚本优先使用 Windows SDK `signtool.exe`（SHA-256 + RFC3161 时间戳），
未安装 SDK 时回退到 PowerShell `Set-AuthenticodeSignature`。
ZIP 为压缩包，Authenticode 仅适用于 PE 文件（exe/dll）。

## 几个设计取舍

写在这里的多半是踩过坑之后改的，不是一开始就这么设计。

- **宿主集成走子进程，不接 SDK**：Blender、UE5、Metashape 的 Python 版本互相打架，接 SDK 等于把用户机器上的所有宿主绑到同一个解释器。改成子进程加中间文件交换后，代价是每次导出多几秒启动与磁盘往返，收益是某个宿主没装、版本不对都只影响它自己那一段。
- **网格重建默认 TSDF，泊松兜底**：泊松把高斯中心当点云，遇到半透明或高反光主体容易只剩背景薄片；TSDF 走深度融合，表面更封闭。但 TSDF 要求相机位姿与训练环境齐备，缺一项就自动退回泊松，宁可外观差一点也不要整条流程失败。
- **模糊评分按 1/4 尺寸解码**：全尺寸解码 4K 帧要慢一个数量级，而判定阈值本来就是按中位数比例取的，相对排序不受影响。配合线程池后，300 帧的评分从秒级降到百毫秒级。

## 开源许可 / License

本仓库自有代码以 [MIT License](LICENSE) 发布（Copyright (c) 2026 CYM）。

本仓库**不包含**以下第三方组件的代码，它们由最终用户自行获取并受其自身许可约束（详见 [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)）：

- **gaussian-splatting**（Inria/MPII）：**仅限研究/评估等非商业用途**。本工具仅以子进程方式调用，不打包其源码；商用前须获得 Inria 授权。
- **COLMAP**（BSD-3-Clause）、**FFmpeg**（gyan.dev 构建，GPLv3）：运行时由「依赖体检」从官方发布页下载。
- Python 依赖（PySide6 为 LGPL-3.0 等）：经 pip 安装，清单见 [requirements.txt](requirements.txt)。
  **plyfile（GPL-3.0+）不是 USF 的依赖**，仅可能出现在用户自建的 `external/gaussian-splatting` 训练环境中，不随本工具打包或分发。

## 训练环境（gaussian-splatting）

首次使用 3DGS 训练前，在本仓库 `external/` 下自行克隆官方仓库（含子模块）并创建独立 venv：

```bat
git clone https://github.com/graphdeco-inria/gaussian-splatting --recursive external/gaussian-splatting
cd external/gaussian-splatting
python -m venv .venv && .venv\Scripts\activate
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
pip install submodules/diff-gaussian-rasterization submodules/simple-knn
pip install plyfile tqdm opencv-python joblib
```

也可在 GUI 内通过「依赖体检」引导完成。克隆与使用 gaussian-splatting 即表示你接受其非商业研究许可条款。
## 开发方式与质量记录

- 本项目在开发中使用了 AI 编程助手，参与范围、人工控制措施与不涉及项见 [AI_ASSISTANCE.md](AI_ASSISTANCE.md)。
- 1.0 定稿前的重构、缺陷修复与性能优化记录见 [docs/CODE_REVIEW_1.0.md](docs/CODE_REVIEW_1.0.md)。
