# Universal Scene Forge — 架构设计（文字版）

> 一句话定位：以 **3D 高斯泼溅（3D Gaussian Splatting）** 为重建内核、以 **DCC 软件（Blender / Metashape / Maya / UE5）无头桥接** 为后处理管道的桌面端一体化场景建模与导出工具。

---

## 1. 分层总体架构

```
┌───────────────────────────────────────────────────────────────────────────┐
│  L5 表现层  views/ (PySide6)                                               │
│     MainWindow ─┬─ 输入源面板(文件选择+帧缩略图预览)                          │
│                 ├─ 参数面板(抽帧fps/迭代数/LOD/导出格式)                     │
│                 ├─ 重建进度条 + 阶段标签                                    │
│                 ├─ DCC 状态指示灯矩阵(Blender/Maya/Metashape/UE5/CUDA)      │
│                 ├─ 日志控制台(彩色分级, 只读)                                │
│                 └─ 依赖体检对话框(缺什么→一键装)                             │
├────────────────────────────── Qt Signal Bus ──────────────────────────────┤
│  L4 控制层  controllers/PipelineController                                 │
│     • 八阶段流水线编排: INGEST→SFM→TRAIN→MESH→UE5预览→DCC后处理→修复→导出   │
│     • 容错框架 _safe(): 单步失败→红灯+日志→跳过继续, 不阻断主流程           │
│     • 线程模型: 主线程只画 UI, 流水线在 QThread 中执行                      │
├───────────────────────────────────────────────────────────────────────────┤
│  L3 核心域  core/                                                          │
│     ingest.py      视频抽帧(FFmpeg, GPU硬解可选) / 图像序列收集 / 并行预处理 │
│     colmap.py      SfM 位姿求解 (COLMAP CLI 封装)                           │
│     gaussian.py    3DGS 训练 (官方 gaussian-splatting 仓库子进程封装)      │
│     mesh.py        泼溅→网格: SH-DC→顶点色, 泊松重建, 密度裁剪, LOD 减面     │
│     exporter.py    导出分发器: FBX/OBJ/GLB/BLEND/UASSET                     │
├───────────────────────────────────────────────────────────────────────────┤
│  L2 DCC 桥接层  dcc/                                                       │
│     registry_probe.py  注册表+环境变量+常见路径 → 三级探测已装软件           │
│     blender_bridge.py  blender --background --python 桥接(核心)            │
│     metashape_bridge.py  metashape.exe -r 桥接(核心)                       │
│     maya_bridge.py     mayapy.exe 无头桥接(模板)                           │
│     ue5_bridge.py      UnrealEditor-Cmd -run=pythonscript 推流(核心)        │
│     auto_installer.py 缺失组件→官方源下载便携版/MSI静默安装(带UAC提升)      │
├───────────────────────────────────────────────────────────────────────────┤
│  L1 支撑层  models/ + utils/                                               │
│     settings.py(配置) project.py(状态机) logger.py(日志)                   │
│     gpu.py(CUDA探测) deps.py(依赖体检+镜像安装) paths.py(冻结路径兼容)       │
└───────────────────────────────────────────────────────────────────────────┘
        ▲ 子进程边界(UTF-8 JSON 参数文件 + [USF] 标签协议 + 超时看门狗)
        │
┌───────┴──────────────────────────────────────────────────────────────────┐
│ 外部世界: FFmpeg / COLMAP / gaussian-splatting(torch+CUDA) /               │
│           Blender(bpy) / Metashape Pro(需授权) / Maya / UE5 Editor         │
└───────────────────────────────────────────────────────────────────────────┘
```

### 关键设计决策

| # | 决策 | 理由 |
|---|------|------|
| D1 | **3DGS 训练放在独立 Python 环境子进程中**，不打包进 exe | torch+CUDA 体积 5GB+ 且随显卡变化；GUI 壳保持 <300MB，训练环境由依赖体检器自动创建 |
| D2 | **DCC 调用统一为"JSON 参数文件 + 标签协议"** | 规避 Windows 命令行引号转义地狱；`[USF] PROGRESS n msg` / `[USF] FILE path` 两类标签即可完成进度回传与产物回传 |
| D3 | **三级软件探测**: 环境变量 → 注册表(winreg) → 常见安装路径 glob | 覆盖便携版/官方安装版/绿色解压版；全部 try/except 包裹，探测失败≠崩溃 |
| D4 | **容错即一等公民**: 每个 DCC 步骤包在 `_safe()` 中 | Metashape 未授权、Blender 无响应等场景只亮红灯并写日志，主流程照常出 OBJ/GLB |
| D5 | UE5 采用 **Editor-Cmd + pythonscript 命令行子进程** 推流，Live Link 作为扩展点 | 命令行方式不依赖项目配置；中间结果(.fbx/.obj)重建完成后即时推送 |
| D6 | 自动安装默认**征得用户同意**（对话框确认），`allow_silent_install=True` 才静默 | 安全合规；Blender/FFmpeg/COLMAP 用官方便携 zip 免管理员，MSI 才走 UAC |

---

## 2. 数据流水线（时序）

```
用户输入(MP4/MOV/JPG序列)
   │
   ▼ [1 INGEST]  FFmpeg: -vf fps=N 抽帧(多线程) ──→ frames/*.jpg
   │              并行预处理(ThreadPoolExecutor + OpenCV 缩放)
   ▼ [2 SFM]     COLMAP: feature_extractor → exhaustive_matcher → mapper
   │              ──→ dataset/{images/, sparse/0/{cameras,images,points}.bin}
   ▼ [3 TRAIN]   gaussian-splatting train.py -s dataset -m model
   │              ──→ model/point_cloud/iteration_N/point_cloud.ply   (含SH系数)
   ▼ [4 MESH]    mesh.py: 读PLY → SH-DC→RGB顶点色 → 不透明度过滤
   │              → Open3D 泊松重建(depth=9) → 密度分位裁剪 → LOD 减面
   │              ──→ usf_scene.obj (+ _lod1.obj / _lod2.obj)
   ▼ [5 UE5预览] ue5_bridge: 中间 OBJ/FBX 推送至 UE5 (pythonscript 导入,
   │              /Game/USF 目录, 自动保存 .uasset)
   ▼ [6 DCC后处理] blender_bridge: 清理(去重/删除松散/重算法线)
   │              → 智能UV → 顶点色材质 → LOD 导出 → FBX(嵌纹理)/GLB/BLEND
   ▼ [7 修复(可选)] metashape_bridge: 对齐照片→深度图→稠密云→建模
   │              → 空洞修复 → 修复版 OBJ 回写
   ▼ [8 EXPORT]  exporter.py 分发: FBX/OBJ/GLTF(GLB)/.blend/.uasset
   │
   └──→ 输出目录 output/ (所有产物 + project.json 状态档案)
```

进度条映射：抽帧 0–10% → SfM 10–20% → 3DGS 训练 20–55% → 网格 55–65% → UE5 预览 65–70% → Blender 后处理 70–85% → 导出 85–100%。

---

## 3. MVC 职责划分

| 层 | 文件 | 职责 | 禁止事项 |
|----|------|------|----------|
| Model | `models/settings.py` | dataclass 配置 + JSON 持久化 | 不 import Qt |
| Model | `models/project.py` | 阶段状态机、产物登记、可恢复快照 | 不 import Qt |
| View | `views/main_window.py` | 全部控件、布局、信号订阅 | 不写业务逻辑 |
| Controller | `controllers/pipeline_controller.py` | 阶段编排、容错、进度上报 | 不直接操作控件 |
| 通信总线 | `EventBus(QObject)` | stage/progress/dcc/log/artifact/done 六信号 | 跨线程自动队列 |

---

## 4. 线程与进程模型

```
[GUI 主线程]                        [流水线 QThread]                 [外部子进程]
MainWindow ──start──▶ PipelineThread.execute()
  ▲                     │  subprocess.Popen ────────▶ FFmpeg / COLMAP / Blender /
  │  Qt Queued Signal   │  (stdout 流式解析进度)       Metashape / UE5-Cmd /
  └── EventBus ◀────────┘  超时看门狗(时间戳轮询)      python train.py(3DGS)
                           stop_event → taskkill /T /F
```

- 每个子进程都有 `timeout_s` 看门狗；超时/取消时 `taskkill /F /T /PID` 杀进程树。
- 抽帧预处理用 `ThreadPoolExecutor`（CPU 密集但轻量）；3DGS 训练独占 GPU（子进程内 `CUDA_VISIBLE_DEVICES` 控制）。

---

## 5. DCC 桥接协议（以 Blender 为例）

```
宿主进程                          Blender 子进程
─────────                        ─────────────
写 params.json  ─────────────▶  blender --background --factory-startup
blender.exe --background               --python mesh_to_fbx.py -- params.json
--python mesh_to_fbx.py
-- params.json
                                  解析 JSON → 导入OBJ → 清理 → UV/材质
stdout 流式读取 ◀──── [USF] PROGRESS 40 清理完成
                                  LOD 减面 → 导出
stdout 流式读取 ◀──── [USF] FILE D:/out/usf_scene.fbx
产物收集/超时看门狗               [USF] DONE
```

Metashape 同理（`metashape.exe -r repair_mesh.py`，参数经环境变量 `USF_METASHAPE_PARAMS` 传入 JSON 路径，规避引号问题）；UE5 用 `UnrealEditor-Cmd.exe proj.uproject -run=pythonscript -script=...`，文件清单经 `USF_UE5_IMPORT_LIST` 传入。

---

## 6. 部署与打包流程

```
开发者构建:
  build_windows.bat
    ├─ python -m venv .venv && pip install -r requirements.txt (失败自动切清华镜像)
    ├─ pyinstaller installer/UniversalSceneForge.spec   → dist/UniversalSceneForge/ (onedir)
    └─ makensis installer/usf_setup.nsi                 → dist/UniversalSceneForge_Setup.exe

用户首次运行:
  Setup.exe (NSIS, 管理员权限, 含 VC Redist 检测)
    → 双击 UniversalSceneForge.exe
        ├─ 依赖体检: PySide6/numpy/cv2/plyfile ⊂ exe; ffmpeg/colmap/cuda → 探测
        ├─ 缺 FFmpeg/COLMAP/Blender → 对话框确认 → auto_installer 官方便携 zip
        ├─ 缺 torch(CUDA) → 提示创建独立训练环境 external/gaussian-splatting/.venv
        └─ 一切就绪 → 输入视频 → 一键重建 → output/ 收产物
```

---

## 7. 容错矩阵

| 故障 | 表现 | 处置 | 对主流程影响 |
|------|------|------|--------------|
| Blender 未安装 | 探测失败 | 红灯 + 引导安装便携版 | FBX/GLB/BLEND 导出降级，OBJ/GLB 仍可出 |
| Blender 超时无响应 | 看门狗触发 | 杀进程树 + 日志 + 跳过 | 同上 |
| Metashape 无授权 | `-r` 退出非零 | 红灯 + 日志说明需 Pro | 修复步骤跳过 |
| UE5 未装/无工程 | 探测失败 | 灰灯 + 引导 | 实时预览跳过 |
| CUDA 不可用 | nvidia-smi 缺失 | 警告 + CPU 模式提示(3DGS 慢) | 训练仍可跑 |
| COLMAP 失败(纹理弱) | mapper 非零退出 | 跳到点云直出分支 | 保底产物 |
| 单帧损坏 | 预处理捕获异常 | 丢弃该帧并计数 | 无 |
