# universal_scene_forge
Universal Scene Forge：桌面端一体化 3D 场景重建与导出工具。视频/图像 → 3DGS 重建 → Blender/Metashape/Maya/UE5 后处理 → FBX/OBJ/GLB/.blend/.uasset。项目仍在完善，欢迎二次开发。 / Desktop all-in-one 3D scene reconstruction & export. Video/images → 3DGS → DCC (Blender/Metashape/Maya/UE5) → FBX/OBJ/GLB/.blend/.uasset. WIP; forks and contributions welcome.

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
