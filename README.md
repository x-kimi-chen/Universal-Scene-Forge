# 通用场景锻造系统（Universal Scene Forge）

把一段视频或一批照片，做成能直接进 DCC 的模型。这个工具把散落在 COLMAP、3DGS 训练脚本、
Blender、Metashape 之间的手工步骤串成一条流水线，中间不用你来回切窗口、手动改配置。

当前版本 **1.0**，九段流程：抽帧 → SfM 求位姿 → 3DGS 训练 → TSDF 建网格 → 影像投影贴色 →
展 UV 或 UE5 预览（可选）→ Blender 后处理 → Metashape 修补（可选）→ 导出
FBX / OBJ / GLB / BLEND / USD / UASSET。每段都能单独失败，失败点不影响后面的降级链——
训练环境没装就走泊松，UE5 没装就跳过预览，最后至少给你一个能打开的模型。

[更新日志](CHANGELOG.md) ｜ [1.0 发布说明](RELEASE_NOTES_1.0.md) ｜ [安全审计](SECURITY_AUDIT.md) ｜ [第三方许可](THIRD_PARTY_LICENSES.md)

## 跑起来

装包最快。到 GitHub Releases 下 `UniversalSceneForge_1.0_Setup.exe`，装完直接开。
不想装就用绿色版 `UniversalSceneForge_1.0_portable.zip`，解压运行 `UniversalSceneForge.exe`。
下载完可以拿 `installer/SHA256SUMS.txt` 对一下哈希。

从源码跑也行：

```bat
python -m pip install -r requirements.txt
python main.py
```

第一次打开建议先点「依赖体检」。Python 库、FFmpeg、COLMAP、Blender 路径、训练环境它都会扫一遍，
缺什么会告诉你缺在哪、怎么补，也能一键装。3DGS 训练环境要单独准备，见文末「训练环境」。

## 命令行

```bat
python main.py --version
python main.py --check-deps
python main.py --cli --input a.mp4 b/ --output D:/work
```

退出码：0 全成功，1 有阶段失败，2 参数写错，3 还没进流水线就被环境或路径拦下了，
130 是你按了 Ctrl+C。脚本里判断结果用退出码比抓日志靠谱。

`--check-deps` 在打包版里同样能用：双击启动没有控制台时，程序会自己开一个窗口；
开不出来就把报告写到 `~/.universal_scene_forge/logs/`，并弹框告诉你路径。

## 几个当时纠结的点

**宿主集成为什么不接 SDK**：Blender、UE5、Metashape 各自绑定不同版本的 Python，
接 SDK 等于要求用户机器上只有一个解释器，装了这个就用不了那个。现在的做法是起子进程、
用中间文件交换，代价是每次导出多几秒启动和磁盘往返，换来的是某个宿主版本不对只影响它自己那一段。

**网格默认 TSDF，泊松兜底**：一开始直接拿高斯中心做泊松，结果黑色鼠标这种高反光主体会被
不透明度阈值滤掉，只剩下背景一层薄片。改成先用 3DGS 深度做 TSDF 融合、再拿原始影像投影贴色，
表面才封闭得住。但 TSDF 要求位姿和训练环境都在，少一样就退回泊松——宁可外观差一点，也别整条流程失败。

**模糊评分降采样到 1/4**：全尺寸解码 4K 帧太慢，而判定阈值本来就是取中位数的一个比例，
相对排序不受影响。配上线程池之后，300 帧的评分从秒级掉到百毫秒级，前端等待感差别很明显。

## 代码签名（可选）

发布前给主程序和安装包签名：

```powershell
# 干跑，确认目标与可用签名方式，不需要证书
powershell -NoProfile -ExecutionPolicy Bypass -File installer\sign_windows.ps1 -DryRun

# PFX 证书，密码交互输入
powershell -NoProfile -ExecutionPolicy Bypass -File installer\sign_windows.ps1 -PfxPath D:\certs\usf-codesign.pfx

# 证书存储指纹，适用于 EV 硬件令牌
powershell -NoProfile -ExecutionPolicy Bypass -File installer\sign_windows.ps1 -Thumbprint <SHA1指纹>
```

优先用 Windows SDK 的 `signtool.exe`（SHA-256 + RFC3161 时间戳），没装 SDK 就退回
PowerShell 的 `Set-AuthenticodeSignature`。ZIP 是压缩包，Authenticode 只认 PE 文件，别在 zip 上试。

## 许可

本仓库自有代码走 [MIT](LICENSE)，Copyright (c) 2026 CYM。

第三方组件不在本仓库里，由用户自行获取，各自受自己的许可约束，清单见
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)。要留意的有两个：

- **gaussian-splatting**（Inria/MPII）只允许研究、评估等非商业用途。本工具只用子进程调它，
  不打包源码；商用之前先找 Inria 拿授权。
- **COLMAP**（BSD-3-Clause）和 **FFmpeg**（gyan.dev 构建，GPLv3）由「依赖体检」从官方发布页下载。
  Python 依赖（PySide6 为 LGPL-3.0 等）走 pip，见 `requirements.txt`。

`plyfile`（GPL-3.0+）不是本工具的依赖，它只会出现在你自己克隆的 `external/gaussian-splatting`
训练环境里，不随安装包分发。

## 训练环境

3DGS 训练要单独准备环境，和 GUI 壳分开，免得 torch 版本冲突把整个程序拖垮：

```bat
git clone https://github.com/graphdeco-inria/gaussian-splatting --recursive external/gaussian-splatting
cd external/gaussian-splatting
python -m venv .venv && .venv\Scripts\activate
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
pip install submodules/diff-gaussian-rasterization submodules/simple-knn
pip install plyfile tqdm opencv-python joblib
```

GUI 的「依赖体检」也能引导你走完这套流程。克隆并使用即表示接受其非商业研究许可。

## 质量记录

1.0 定稿前做过一轮重构、缺陷修复和性能优化，每条都记了改动原因与验证方式，见 [docs/CODE_REVIEW_1.0.md](docs/CODE_REVIEW_1.0.md)。
