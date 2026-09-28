# Universal Scene Forge v0.9.6-preview 开发计划

> 目标：**重建真实感与清晰度**。修复 0.9.5 实测暴露的"模型不真实、模糊、
> 细节不足"（测试报告 Q-01/Q-02），同时广泛兼容主流重建引擎与摄影测量工具。
> 本计划由 0.9.5-preview 真实视频实测（91 帧鼠标视频两轮全绿）直接驱动。

---

## 1. 问题诊断（来自 0.9.5 实测，见 TEST_REPORT_0.9.5-preview.md）

| 编号 | 现象 | 根因 | 0.9.6 对策 |
|------|------|------|-----------|
| Q-01 | 暗色主体缺失（背景垫子为主，主体呈孔洞） | 无主体遮罩；高纹理背景在 3DGS 致密化中碾压低纹理主体 | 主体遮罩（rembg/分割）→ 遮罩参与 SfM 与训练；训练后主体高斯投影过滤 |
| Q-02 | 细节不足（3DGS 网格 6.3k 面 vs Metashape 200k 面） | 泊松深度保守 + 无纹理烘焙 | 泊松深度自适应 + 网格细化（孔洞填充/平滑/去噪）+ 纹理烘焙（xatlas UV + 观测烘焙） |
| Q-03 | 清晰度不足 | 失焦帧（已修）、COLMAP 默认参数保守 | 已修模糊剔除；提高 SIFT 上限与引导匹配（可选开关） |
| Q-04 | 单一训练引擎，质量上限受限 | 只接官方 3DGS | 训练器抽象：内置 3DGS / nerfstudio splatfacto / OpenSplat 可选 |
| Q-05 | 摄影测量来源单一（Metashape 商业授权） | 无替代 | RealityScan CLI 桥接（Epic 生态，与 UE5 导出天然衔接） |

## 2. 任务拆解（里程碑）

### M1 主体遮罩管线（Q-01，最高优先级）——直接影响真实感
| 任务 | 内容 | 状态 |
|------|------|------|
| M1.1 | `app/core/masking.py`：rembg(U2Net, CPU 可用) 批量遮罩生成，可选导入优雅跳过 | ✅ 已实现（本提交） |
| M1.2 | 采集接线：模糊剔除后生成 `masks/`，配置 `enable_subject_mask`（0.9.5 默认关） | ✅ 已实现 |
| M1.3 | COLMAP 遮罩消费：masked 图像参与特征提取（提升主体位姿质量） | 0.9.6 开发 |
| M1.4 | 训练消费：per-image mask 损失（需 external 仓库 train.py 配合/轻补丁，许可允许自行修改自用） | 0.9.6 开发 |
| M1.5 | 训练后主体高斯过滤：解析 cameras.bin/images.bin，投影高斯中心，多数视图在遮罩内才保留 | 0.9.6 开发 |
| M1.6 | GUI：遮罩开关 + 遮罩预览缩略图 | 0.9.6 开发 |

### M2 训练器抽象与多引擎（Q-04）
| 任务 | 内容 | 状态 |
|------|------|------|
| M2.1 | `app/core/trainers.py`：TrainingEngine 协议 + 注册表 + 内置 3DGS 适配器（委托现有 GaussianEngine） | ✅ 已实现（本提交） |
| M2.2 | OpenSplat 适配器（单二进制开源 C++，CLI 简单；CPU/CUDA） | ✅ 骨架已实现；⚠ 官方 Releases 无 Windows 预编译版（已实测扫描全部 release），需自编译（本机具备 CUDA+MSVC） |
| M2.3 | nerfstudio splatfacto 适配器 | ✅ **环境已安装（D:/nerfstudio, torch 2.9+cu128）且 splatfacto 训练实测通过（checkpoint 已产出）**；ns-export→PLY 接入导出链为剩余任务 |
| M2.4 | 质量对比基准：同一数据集三引擎出图对比（PSNR/LPIPS + 视觉） | 0.9.6 开发 |
| M2.5 | GUI：训练器下拉选择 + 每引擎参数预设 | 0.9.6 开发 |

### M3 摄影测量多来源（Q-05）
| 任务 | 内容 | 状态 |
|------|------|------|
| M3.1 | RealityScan CLI 桥接骨架（检测 + 命令构建 + 优雅跳过） | ✅ 已实现（本提交） |
| M3.2 | RealityScan 实机验证（需 Epic 账号与软件授权，**需用户确认**） | 待确认 |
| M3.3 | Metashape/RealityScan 网格统一接入导出兜底链 | 0.9.6 开发 |

### M4 网格细化与纹理（Q-02）
| 任务 | 内容 | 状态 |
|------|------|------|
| M4.1 | 网格后处理增强：孔洞填充 / 拉普拉斯平滑 / 顶点去噪（open3d） | 0.9.6 开发 |
| M4.2 | 纹理烘焙管线：xatlas UV 展开 + 3DGS 观测烘焙 → 带贴图模型（替代纯顶点色） | 0.9.6 开发 |
| M4.3 | 泊松深度自适应（按高斯数量/显存自动档位） | 0.9.6 开发 |

### M5 稳定性（S-01/S-02）
| 任务 | 内容 | 状态 |
|------|------|------|
| M5.1 | 训练启动前 GPU 占用检测与提示 | 0.9.6 开发 |
| M5.2 | UE5 Interchange 同步导入方案调研（Alternative: Interchange watch-folder 策略 B） | 0.9.6 调研 |

## 3. 兼容适配矩阵（0.9.6 目标）

| 引擎/工具 | 类型 | 接入方式 | 加速 | 授权 | 0.9.6 状态 |
|-----------|------|----------|------|------|-----------|
| gaussian-splatting（内置） | 3DGS 训练 | 子进程 train.py | CUDA | 非商业研究 | ✅ 现状 |
| nerfstudio splatfacto | 3DGS 训练 | 独立环境 + ns-train | CUDA | Apache-2.0 | 骨架就绪，环境安装需确认 |
| OpenSplat | 3DGS 训练 | 单二进制 CLI | CUDA/CPU | GPL-3.0（调用不传染，分发行二进制需遵守） | 骨架就绪 |
| Postshot (Jawset) | 3DGS 训练 | CLI（Windows） | CUDA | 专有 | 评估（M2.4 后决定） |
| Metashape Pro | 摄影测量 | 无头 Python | Vulkan/OpenCL | 专有（用户授权） | ✅ 现状 |
| RealityScan | 摄影测量 | CLI | CUDA/OpenCL | 专有（Epic 账号） | 骨架就绪，实机待确认 |
| Blender | 后处理/导出 | 无头脚本 | — | GPL | ✅ 2.8x–5.x 实测 |
| COLMAP | SfM | 子进程 | CUDA/CPU | BSD | ✅ 现状（厂商自适应） |

## 4. 技术方案要点

1. **训练器抽象**：`TrainingEngine` 协议（detect()/prepare()/train()/parse_progress()），
   注册表按 settings.trainer 选择；内置引擎零改动委托 GaussianEngine，
   新引擎以适配器接入 —— 流水线其余阶段不感知引擎差异。
2. **遮罩管线**：rembg(U2Net) CPU 可跑；产物 `masks/<frame>.png`（白=主体）。
   SfM 侧喂 masked 图；训练侧 0.9.6 提供 train.py 补丁说明（自用修改，不再分发）
   + 训练后投影过滤双保险。
3. **RealityScan CLI**：RealityCapture/RealityScan 命令行
   `-set <变量> … -calculate* -exportModel`；桥接生成命令脚本，
   与 Metashape 桥接同构，失败优雅跳过。
4. **纹理烘焙**：xatlas（pip 可装）展开 UV → Blender Cycles 从 3DGS 渲染观测
   投影烘焙 → 8K 贴图；模型从顶点色升级为贴图，真实感核心提升。

## 5. 依赖与风险

| 项 | 依赖/成本 | 风险 | 缓解 |
|----|-----------|------|------|
| nerfstudio 环境 | ✅ 已安装 (D:/nerfstudio, uv venv + 阿里云 cu128 wheel + 清华 PyPI) | torch 2.10 过新导致 gsplat JIT 不兼容（实测，已降级 2.9 解决）；Commandlet 异步导入收尾 | 环境变量配方已固化（MAX_JOBS=1 / NVCC_APPEND_FLAGS / CCCL_IGNORE） |
| rembg 模型 | u2net.onnx ~170MB 首次下载 | 遮罩不准 | 阈值可调 + GUI 预览确认；仅影响主体质量 |
| RealityScan | 需 Epic 授权与安装 | 无授权则不可用 | 优雅跳过；Metashape 路径不受影响 |
| OpenSplat | 需下载对应 CUDA 构建二进制 | 版本迭代快 | 固定已验证版本号 + 校验 |
| train.py 遮罩补丁 | external 仓库自行修改（许可允许自用修改） | 升级仓库时需重打 | 补丁脚本化（一键应用） |

## 6. 验收标准（0.9.6-preview 出口条件）

1. 同一鼠标视频重跑：**主体（鼠标）在 3DGS 网格中完整存在**（无孔洞），
   网格面数 ≥ 50k，带烘焙贴图；
2. 训练器下拉三选一可用：内置 / nerfstudio splatfacto / OpenSplat，
   同数据集各自完成训练并出网格；
3. RealityScan 桥接在装有该软件的机器上完成一次无头重建（或明确阻塞原因）；
4. 回归：0.9.5 全部单元测试与端到端用例不回退；
5. 模糊帧剔除、遮罩预览、训练器选择均有 GUI 入口与日志留痕。

## 7. 已随本提交直接落地（0.9.6 前置代码）

- `app/core/masking.py`：主体遮罩生成（rembg 可选导入，优雅跳过）
- `app/core/trainers.py`：训练器协议 + 注册表 + 内置/OpenSplat/nerfstudio 适配骨架
- `app/dcc/realityscan_bridge.py`：RealityScan CLI 桥接骨架
- `settings`：`enable_subject_mask`（默认关）/ `trainer`（默认 builtin）/ `realityscan_exe`
- 测试：`tests/unit_096_prep.py`（遮罩跳过路径 / 注册表选择 / 检测配置）
