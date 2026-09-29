# 更新日志 (Changelog)

本文件记录 Universal Scene Forge 的版本变更。格式参考 Keep a Changelog；
版本号遵循 语义化版本（预览版以 `-preview` 后缀标识）。

---

## 0.9.7-preview — 2026-09-29

> **预览版（Pre-release）**。主题：**网格质量细化 + 适配完善**。
> 调研纪要见 `docs/RESEARCH_0.9.7.md`（SuGaR/2DGS/MILo 方法演进、
> COLMAP 4.1 CASPAR、SAM 遮罩最佳实践）。

### 新增

- **网格细化管线**（`app/core/mesh.py` `_refine`）: 泊松重建后自动执行
  小连通碎片移除 → 退化三角形/重复顶点/非流形边清理 → Taubin 平滑
  （保形去噪）—— 实测暗色主体网格表面噪点消除、形体干净（0.9.6 遗留 Q-02）。
- **主体遮罩边界收紧**（`app/core/masking.py` `_tighten`）: U2Net 软边界
  外扩 2~5px 会把边界背景高斯误判为主体 —— 自动腐蚀 + 最大连通域保留,
  投影过滤精度直接受益。
- **调研纪要入档**: `docs/RESEARCH_0.9.7.md`。

### 计划中 (0.9.7 后续)

- SuGaR/2DGS 训练期表面对齐（需 external 仓库自用扩展, 评估后推进）;
- COLMAP 4.1 CASPAR 求解器适配（大幅提速, 保持 3.9.1 稳定默认）;
- SAM2 多视图一致分割接入评估;
- 纹理烘焙管线（xatlas UV + 3DGS 观测烘焙）。

---

## 0.9.6-preview — 2026-09-29

> **预览版（Pre-release）**。主题：**重建真实感与清晰度**。
> 主体遮罩 + 高斯过滤 + 泊松深度自适应三重改进显著提升暗色/低纹理主体的
> 重建完整度；训练引擎开放为多选（内置 / nerfstudio / OpenSplat / Postshot）。
> 开发计划与验收标准见 `docs/DEV_PLAN_0.9.6.md`，
> 架构与扩展指南见 `docs/DEV_MANUAL.md`。

### 新增

- **主体遮罩管线**（默认开启 `enable_subject_mask`）：采集后用 rembg(U2Net)
  自动生成主体遮罩（白=主体）。u2net 权重约 176MB, 已支持预下载
  （`%USERPROFILE%/.u2net/`）；未安装 rembg 时自动跳过并提示。
- **训练后主体高斯投影过滤**：训练得到的点云逐高斯投影到各采集视图,
  采样主体遮罩, 仅保留「多数可见视图中位于主体内」的高斯 —— 免修改
  external 训练仓库即可解决暗色主体缺失问题 (0.9.5 遗留 Q-01)。
  实测: 350,685 高斯中识别 33,968 主体高斯; 过滤失败自动回退原始点云。
- **训练器抽象与多引擎注册表**（`settings.trainer`）：
  `builtin`（内置 3DGS）/ `nerfstudio`（splatfacto）/ `opensplat` /
  `postshot` —— 所选引擎不可用时自动回退内置, 流水线其余阶段无感。
- **nerfstudio 引擎完整实装**（环境安装于 `D:/nerfstudio`）：
  torch 2.9.0+cu128 + nerfstudio + gsplat 1.4；COLMAP 数据集自动转换
  （`app/core/ns_convert.py`）；全自动训练链路（数据转换 → vcvars64 包装
  脚本 → 训练 → checkpoint 检测收尾 → ns-export）端到端实测通过, PLY 落盘。
- **RealityScan CLI 桥接骨架**（Epic 生态摄影测量, 需本机授权后联调）。
- **Postshot 检测骨架**（常见安装路径自动探测）。
- **GPU 占用检测**：训练前自动检测其他进程的 GPU 占用并告警
  （修复实测中后台进程抢占导致训练降速 25 倍的问题）。
- **泊松深度自适应**：高斯数量 >15 万自动 +1 级、>50 万 +2 级
  （面数约 4 倍/级），上限 +2 防失控 —— 提升网格细节 (0.9.5 遗留 Q-02)。
- **开发手册** `docs/DEV_MANUAL.md`：架构总览、训练器/DCC 扩展指南、
  nerfstudio Windows 启动配方、环境配置、打包/测试/发布流程。

### 适配（新增）

- **COLMAP 4.2.1 适配**（CASPAR 全局求解器大幅提速）：SfmRunner 按版本
  自动适配 GPU 选项命名（3.x: SiftExtraction/SiftMatching → 4.x:
  FeatureExtraction/FeatureMatching），完整 SfM 实测通过；
  依赖体检自动安装优先下载 4.2.1（3.9.1 保留为回退源）。

### 修复

- **COLMAP→nerfstudio 转换器**：cameras.bin 计数字段修正（uint64）、
  逐帧内参（fl_x/fl_y/cx/cy/w/h）、camera_angle_x 作用域修正。
- **模糊帧剔除**：剔除逻辑与日志完善（0.9.5 遗留项收尾）。

### 变更

- 流水线扩为 **9 阶段**（新增 RizomUV 自动展 UV 环节, 未安装自动跳过）；
- 训练阶段切换为训练器抽象（内置引擎行为不变, 支持 data_device=cuda 与
  B-01 解释器守卫）；
- 导出产物登记只统计真实落盘的文件。

### 已知限制

- 3DGS 训练环境（torch + CUDA）不随安装包分发；nerfstudio 独立环境
  约 10GB 亦需自行安装（两者均提供配方与引导）；
- 主体遮罩质量依赖 U2Net 通用分割模型, 遮罩边界可调（majority 参数）；
- OpenSplat 官方无 Windows 预编译版, 需自编译（D:/OpenSplat/README.txt
  有指引）；
- UE5 无头导入的异步收尾仍不稳定（引擎层限制, 阶段失败优雅降级）；
- 安装包未做 Authenticode 数字签名。

---

## 0.9.5-preview — 2026-09-27

> 预览版（Pre-release）。包含大量修复与导出可靠性重构，建议升级；
> 反馈问题请附 `~/.universal_scene_forge/logs/` 下最新日志文件。
>
> **真实视频实测**：91 帧鼠标实拍视频完整走通 9 阶段流水线
> （两轮，全部阶段通过，产物 117/80 个，已交付桌面验证）。

### 新增

- **模糊帧自动剔除**（采集阶段）：视频对焦/运动模糊的帧会拖垮 SfM 与 3DGS
  质量 —— 采集后自动计算 Laplacian 清晰度, 剔除低于中位数 80% 的模糊帧
  （实测 91 帧真实视频剔除 37 张失焦帧, 精确命中视频失焦段）。

- **一键自动配置全部环境**（依赖体检对话框主按钮）：单次确认后串行自动完成
  安装缺失 Python 库 → 工具便携版（FFmpeg/COLMAP, 按显卡厂商选版本）→
  DCC 宿主扩展 → 训练环境（可选, 已就绪自动跳过）→ DCC 路径写入设置；
  单项失败不阻断后续, 实时输出过程, 结束给出分项汇总。

- **导出位置**：重建参数区新增导出目录设置（浏览选择，留空 = 自动使用输入源旁的
  `usf_work`）。设置后 fbx/obj/glb/blend 等成品直接落入该目录；重建完成弹窗与
  状态栏直接显示导出位置。
- **CUDA 加速开关**（默认开启）：控制 COLMAP 特征提取/匹配是否走 GPU
  （依赖体检安装的便携版即 CUDA 版）；取消勾选回退 CPU。3DGS 训练本身始终使用 GPU。
- **导出完整性保障链**：3DGS 泊松网格 → Metashape 摄影测量修复网格 →
  导出前自动补跑一次 Metashape 修复 —— 任一环节可用即可交付完整模型，不再交白卷。
- **OBJ 保底导出材质随行**：无 Blender 时导出的 OBJ 现在连同 `.mtl` 与纹理文件
  一并复制，不再出现"模型能打开但没有材质"。
- **运行日志落盘**：每次运行自动写入 `logs/usf_*.log`（源码模式在项目目录，
  安装版在 `~/.universal_scene_forge/logs/`），UTF-8 带 BOM，记事本/Excel 直读无乱码。
- **关于对话框**补充版权、MIT 许可证与第三方组件声明。
- **导出格式全空拦截**：未勾选任何导出格式时启动前直接提示，避免白跑全程。
- **uasset 明确提示**：勾选 uasset 但未配置 UE5 `.uproject` 工程时，日志给出
  配置指引，其余格式不受影响。

### 适配与优化

- **显卡厂商适配**（NVIDIA / AMD / Intel / 核显）：
  - GPU 探测按厂商分级：NVIDIA 全链路 GPU 加速；AMD/Intel 由 Metashape 经
    Vulkan/OpenCL 照常加速，COLMAP 自动回退 CPU SIFT（结果一致，速度较慢），
    安装器自动下载对应的无 CUDA 版本；
  - 3DGS 训练依赖 NVIDIA CUDA（上游 torch 栈限制），非 NVIDIA 机器的模型
    产出自动走 Metashape 兜底链，界面与日志明示各阶段加速状态。
- **Blender 多版本**：桥接脚本兼容 2.8x–5.x API（OBJ/PLY 导入分发、引擎名
  特性探测、烘焙引擎还原）；已在 Blender **4.5 / 5.0 / 5.2** 三版本实测
  后处理全链路通过。
- **渲染效率**：训练阶段 `data_device=cuda` 实测 2500 迭代提速约 **8.7%**
  （图像常驻显存，免去逐迭代主机-显存拷贝），随 CUDA 加速开关启用；
  帧数超过 250 时自动回退默认模式防止显存溢出。
- **UE5 预览环节可用化**：没有 `.uproject` 时自动生成最小预览工程
  （含 PythonScriptPlugin，引擎版本从安装路径解析），引擎无头打开与脚本
  执行已实测打通；导入脚本改用 UE5 现代 Interchange API 并修复
  `AssetImportTask.name` 兼容问题。
  （已知限制：Commandlet 生命周期与异步导入的收尾配合仍不稳定，
  预览推送可能不落盘，属引擎层问题，后续版本跟进。）

- **RizomUV 自动展 UV 阶段**（第 5 环节, 流水线扩为 9 阶段）：安装了
  RizomUV VS/RS 的机器上网格重建后自动无头展 UV + 打包（Lua 桥接,
  各版本方法名逐一兼容），产物优先用于后续贴图；未安装自动跳过,
  由 Blender 智能UV 兜底。
- **Modo 导出回退**：FBX 导出降级链扩展为
  Blender → 3ds Max → Maya → Houdini → Cinema 4D → **Modo**。
- **USD 导出格式**：新增 USD (ASCII .usda) 选项, 保留顶点色;
  导出参数按 Blender 版本 RNA 探测自动适配（4.x/5.x 属性名差异）。
- **各程序功能自动化配置**：启动时三级探测命中的 DCC 路径自动写入
  设置中的空字段（只填空, 绝不覆盖用户显式配置）, 并在日志中报告。

### 修复

- **Metashape 2.x API 兼容**（已在 2.3.1 实测）：2.x 移除了顶层
  accuracy/quality 枚举与 `buildDenseCloud`，且对未知/None 参数**静默取空**
  （导致建模后报 "Null model"）。修复脚本重写为按方法签名探测自动选择
  新旧两套调用，并显式传 `model=[chunk.model]` 绕过资产注册问题。
- **3DGS 训练路径**：数据集/模型路径转绝对路径（子进程工作目录在训练仓库内，
  相对路径必然报 "Could not recognize scene type!"）。
- **训练端口冲突**：train.py 的 GUI 监听端口固定 6006，并行两个重建或残留
  训练进程时崩溃（WinError 10048）。改为每次分配随机空闲端口。
- **冻结版 open3d 导入**：流水线线程内首次导入可能因 DLL 初始化失败；
  改为启动时主线程预载，流水线线程复用缓存。
- **COLMAP 诊断信息**：失败时带出 COLMAP 输出尾部（此前只显示退出码，
  无法定位原因），且过程日志全量进文件。
- **UE5 预览工程路径**：自动生成的 `.uproject` 改用绝对路径
  （相对路径下引擎报 "Project file not found"）。
- **导出产物虚增**：产物登记只统计真实落盘的文件；Blender 导出脚本
  单格式失败不再截断其余格式（互相隔离）。

### 安装器

- **覆盖升级路径检测**（Critical 修复）：依次读取 64 位/32 位注册表主键与
  卸载表 `InstallLocation`，命中后目录页默认切到旧路径，实现**原地升级**，
  避免 C:/D: 两处安装并存。
- **用户数据保护**：升级不再覆盖已存在的 `external/`（保护用户自建训练
  venv）与安装目录内遗留 `settings.json`。
- **写入锁预防**：安装前自动结束占用安装文件的旧版进程，减少
  "无法打开要写入的文件" 弹窗。
- **版本资源统一**：PE 文件版本与界面/关于版本一致（0.9.5）。

### 已知限制

- 3DGS 训练环境（torch + CUDA，约 3–4 GB）不随安装包分发（体积考量）：
  「依赖体检」提供创建命令；高级用户可在设置中把 `gs_python` 指向已有训练环境。
- 安装包与主程序未做 Authenticode 数字签名，SmartScreen 首次运行可能提示
  （选择"仍要运行"）。
- uasset 导出需要本机 UE5 与 `.uproject` 工程。
- 网格阶段存在 1000 有效高斯的质量门槛：迭代数过低时会主动拒绝并提示
  加大迭代，此时导出自动回退 Metashape 网格。

---

## 0.9.4 — 2026-09-19

- 首个开源发布（MIT License）。
- 核心链路：多源输入（视频/图片混合）→ COLMAP SfM → 3DGS 训练 →
  泊松网格 → UE5 预览（可选）→ Blender 后处理 → Metashape 修复（可选）→
  多格式导出（FBX/OBJ/GLB/BLEND/UASSET）。
- 依赖体检与一键安装（COLMAP / FFmpeg 便携版）、13+ 款 DCC 软件三级探测
  （注册表 / 路径 / Steam）、调用路径设置、Premiere Pro 抽帧扩展。
- 安装器体积优化：open3d 等大件由依赖体检按需补装。
