# Universal Scene Forge 开发手册 (Development Manual)

> 适用版本: 0.9.6-preview 及之后 ｜ 面向: 二次开发者与维护者
> 架构: PySide6 GUI (MVC) + 子进程桥接各 DCC/引擎, 全部桥接失败均优雅降级

---

## 1. 架构总览

```
main.py                      入口: GUI / CLI / --check-deps
app/
  views/main_window.py       主窗口 (MVC 的 V) + 依赖体检对话框 + 各线程类
  controllers/
    pipeline_controller.py   流水线编排器 (MVC 的 C): 9 阶段 + EventBus 信号
  core/
    ingest.py                采集: 抽帧 / 模糊帧剔除 / 预处理 / 缩略图
    colmap.py                COLMAP SfM 封装 (特征提取→匹配→重建)
    gaussian.py              内置 3DGS 训练封装 (GaussianEngine)
    trainers.py              训练器抽象: 协议 + 注册表 + 多引擎适配器
    ns_convert.py            COLMAP → nerfstudio transforms.json 转换器
    gaussian_filter.py       训练后主体高斯投影过滤 (M1.5)
    masking.py               主体遮罩生成 (rembg 可选依赖)
    mesh.py                  泊松网格重建 (open3d)
    exporter.py              导出分发器 (多格式 + 多 DCC 降级链)
  dcc/                       各 DCC 无头桥接 (同构模式, 见 §4)
  models/                    PipelineSettings / ProjectState / Stage
scripts/                     随包分发的宿主内脚本 (blender/metashape/ue5/...)
installer/                   PyInstaller spec + NSIS + 部署脚本
```

数据流: `EventBus`(Qt 信号) 是控制器→视图唯一通道; 所有子进程输出
逐行回流 `log_line` 信号; 任一阶段异常被 `_safe()` 捕获 → 红灯 + 跳过。

## 2. 关键机制

### 2.1 流水线容错
- 每阶段包在 `_safe()`: 异常 → 失败记录 + 跳过; `StageSkipped` → 蓝色留档;
  INGEST 失败会级联终止后续阶段。
- 优雅降级贯穿全部 DCC 桥接: 未安装 → 跳过, 绝不阻断主流程。

### 2.2 导出完整性保障链
`_best_meshes()`: 3DGS 泊松网格 → RizomUV UV 网格 → Metashape 修复网格 →
导出前自动补跑修复。**新增网格来源时在此登记优先级即可接入全链**。

### 2.3 训练器抽象 (`app/core/trainers.py`)
```
TrainingEngine 协议: train(dataset_dir, model_dir, iterations) -> ply路径
适配器: Builtin3dgsAdapter / OpenSplatAdapter / NerfstudioAdapter /
        PostshotAdapter, 各自实现 detect(settings) -> EngineInfo
create_engine(settings): 按 settings.trainer 选择, 不可用回退 builtin
```
**新增训练引擎三步**: ① 写适配器类 (detect + train); ② 注册进 `_ENGINES`;
③ settings 加 `xxx_exe`/开关字段。GUI 无需改动 (下拉自动生成可选)。

### 2.4 DCC 桥接模式 (同构)
所有桥接遵循同一契约:
- 生成按次脚本 (路径内联, 规避命令行转义/版本差异);
- 子进程 `CREATE_NO_WINDOW` + stdout 逐行回调 + stop_event + 超时;
- 产物存在性校验, 失败抛 `XxxBridgeError` (上层 `_safe()` 兜底);
- 环境变量清洗 (剥掉 QT_*, 防宿主误载插件)。

### 2.5 nerfstudio 启动配方 (Windows, 实测)
gsplat JIT 编译要求 (缺一不可):
1. `vcvars64.bat` 激活 MSVC;
2. `MAX_JOBS=1` (16GB 内存机器防 OOM);
3. `NVCC_APPEND_FLAGS=-Xcompiler /Zc:preprocessor` (CUDA 13.x 头文件要求);
4. `CCCL_IGNORE_MSVC_TRADITIONAL_PREPROCESSOR_WARNING=1`;
5. PATH 含 `.venv/Scripts` (ninja)。
torch 版本: **2.9.0+cu128** (2.10 的头文件与 gsplat 1.4 源码不兼容, 实测)。
数据: COLMAP → `app/core/ns_convert.py` 转 transforms.json;
启动参数: `--pipeline.datamanager.dataparser nerfstudio-data`
(经 wrapper bat, 见 `D:/nerfstudio/test_run.bat` 样例)。

## 3. 环境配置

### 3.1 开发环境 (源码)
```bat
git clone <repo> && cd universal_scene_forge
py -3.12 -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```
可选组件: `pip install "rembg[cpu]"` (主体遮罩), nerfstudio (见 §2.5)。

### 3.2 打包
```bat
.venv\Scripts\python.exe -m PyInstaller installer\UniversalSceneForge.spec --noconfirm --clean
robocopy external\gaussian-splatting dist\UniversalSceneForge\external\gaussian-splatting /E /XD .venv build input
cd installer && makensis usf_setup.nsi
```
注意: spec **排除** torch/open3d/rembg (体积) —— 运行期由依赖体检补装
open3d 到 `~/.universal_scene_forge/python_pkgs` (main.py 预载进 sys.path)。

### 3.3 测试
```bat
for %t in (tests\unit_*.py) do .venv\Scripts\python.exe %t
```
- `unit_blender_versions.py` / `unit_ue5_autoproject.py` 需对应软件;
- `unit_scenario_fallback.py` 为慢速场景测试 (真实 Metashape, 约 5 分钟);
- CLI 端到端: `python main.py --cli --input <视频> --output <目录>`。

### 3.4 版本发布
1. 同步三处版本号: `app/__init__.py` / `installer/version_info.txt` /
   `installer/usf_setup.nsi` (tests/unit_qa_fixes.py 有一致性校验);
2. 更新 `CHANGELOG.md`;
3. PyInstaller → robocopy external → makensis;
4. 打 tag `v<版本>-preview` 并推送。

## 4. 设计约定

- **隐私**: 入库前扫描密钥; 本机路径的辅助脚本进 `.gitignore`
  (如 deploy_to_program_files.bat);
- **降级优先**: 任何可选依赖缺失 → 跳过 + 明确日志, 不抛出阻断;
- **编码**: 所有新写文件 UTF-8; 日志 utf-8-sig; 冻结版 stdout 走 GBK
  时用 iconv/`-X utf8` 处理;
- **许可**: gaussian-splatting (Inria 非商业) 不入库不分发; 新增第三方
  依赖须登记 THIRD_PARTY_LICENSES.md。

## 5. 已知引擎/工具适配速查

| 工具 | 桥接 | 检测 | 状态 |
|------|------|------|------|
| Blender 2.8x–5.x | scripts/blender (API 分发) | 注册表/路径/Steam | ✅ 三版本实测 |
| Metashape 2.x | app/dcc/metashape_bridge + scripts/metashape | 路径 | ✅ 2.3.1 实测 |
| UE5 | app/dcc/ue5_bridge + scripts/ue5 + 自动工程 | 路径 | ✅/⚠ 异步收尾 |
| COLMAP | app/core/colmap (CUDA/CPU 双版本) | tools_root | ✅ |
| nerfstudio | app/core/trainers (NerfstudioAdapter) | D:/nerfstudio | ✅ 训练实测 |
| OpenSplat | app/core/trainers (OpenSplatAdapter) | D:/OpenSplat | 骨架 (官方无 win 预编译) |
| Postshot | app/core/trainers (PostshotAdapter) | 常见路径 | 骨架 (需装机联调) |
| RealityScan | app/dcc/realityscan_bridge | settings.realityscan_exe | 骨架 (需装机联调) |
| RizomUV | app/dcc/rizomuv_bridge (Lua 生成) | 注册表/路径 | 骨架 (需装机联调) |
| Modo | app/dcc/modo_bridge (Python 生成) | 注册表/路径 | 骨架 (需装机联调) |
| Premiere | extensions/pr_frame_exporter (CEP) | 路径 | ✅ |
