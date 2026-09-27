# universal_scene_forge
Universal Scene Forge：桌面端一体化 3D 场景重建与导出工具。

**流水线（9 阶段，每阶段独立容错与超时兜底）**：
视频/图像 → 3DGS 重建 → 泊松网格 → RizomUV 自动展 UV（可选）→ UE5 实时预览（可选）→ Blender 后处理 → Metashape 修复（可选）→ 多格式导出（**FBX / OBJ / GLB / BLEND / USD / UASSET**，含 LOD 与导出目录设置）。

**主要特性**：
- 导出完整性保障链：3DGS 网格 → Metashape 修复网格 → 自动补跑修复，保证产出模型
- 模糊帧自动剔除（Laplacian 清晰度）、CUDA 加速开关、显卡厂商自适应（NVIDIA/AMD/Intel）
- Blender 4.x/5.x 多版本、Metashape 2.x、UE5 预览工程自动生成
- 依赖体检 + 🚀 一键自动配置全部环境（Python 库/工具/扩展/训练环境/DCC 路径）
- 运行日志落盘（logs/usf_*.log）、导出位置自定义、模糊帧剔除

Desktop all-in-one 3D reconstruction & export: video/images → 3DGS → mesh →
(optional RizomUV UV / UE5 preview / Metashape repair) → FBX/OBJ/GLB/BLEND/USD/UASSET.
Project WIP; forks and contributions welcome. 项目仍在完善，欢迎二次开发。

## 命令行 / CLI

```bat
python main.py --version
python main.py --check-deps
python main.py --cli --input a.mp4 b/ --output D:/work
```

退出码语义：`0` 全部成功；`1` 任意阶段失败；`2` 参数错误；`3` 输入源/环境阻断（未进入流水线）。

## 开源许可 / License

本仓库自有代码以 [MIT License](LICENSE) 发布（Copyright (c) 2026 CYM）。

本仓库**不包含**以下第三方组件的代码，它们由最终用户自行获取并受其自身许可约束（详见 [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)）：

- **gaussian-splatting**（Inria/MPII）：**仅限研究/评估等非商业用途**。本工具仅以子进程方式调用，不打包其源码；商用前须获得 Inria 授权。
- **COLMAP**（BSD-3-Clause）、**FFmpeg**（gyan.dev 构建，GPLv3）：运行时由「依赖体检」从官方发布页下载。
- Python 依赖（PySide6 为 LGPL-3.0、**plyfile 为 GPL-3.0+** 等）：经 pip 安装，清单见 [requirements.txt](requirements.txt)。

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
