# Universal Scene Forge 0.9.9-preview

**预发布版本（Pre-release）**。主题：**重建外观质量**——让导出的 FBX /
.blend 接近原视频，而不是只剩背景薄片。

## 主要变化

### 网格重建

- 新增 **3DGS 深度 → TSDF 网格** 后端：渲染每个视角的 alpha 归一化深度，
  用 Open3D `ScalableTSDFVolume` 融合成完整表面；缺少训练环境/相机位姿
  时自动回退泊松重建。
- 不透明度阈值 `0.35 → 0.10`：旧值在黑色鼠标上只保留 13% 高斯（几乎全是
  背景），新值保留 59%。
- 自适应法向半径（按最近邻间距），替代固定 0.6；泊松默认深度 10；
  Taubin 平滑 10 次 → 2 次。
- 实测真机视频：TSDF 826,680 面 → 碎片清理 744,434 → 平滑/减面
  149,999 面，鼠标主体完整。

### 纹理与外观

- 新增**影像投影贴色**：用相机位姿把原始影像投影到网格顶点（射线遮挡 +
  多视角加权 + 未覆盖顶点最近色补齐），修正 3DGS `f_dc` 对黑色高光物体
  偏灰的问题。
- 烘焙贴图默认 **4096×4096**（失败回退 2048）。
- 修正 **sRGB 编码**：Cycles 线性缓冲写入 8-bit PNG 导致整体偏暗。
- 新增**逐面 atlas 兜底**：smart UV 覆盖率 < 8% 时按三角面分配 UV 单元
  （实测覆盖率 38.5%，不再出现稀疏黑块）。

### 输入与默认值

- 视频抽帧默认 `2.0 → 8.0 fps`；质量预设同步补充不透明度/密度/贴图分辨率。

## 下载资产

| 文件 | 大小 | SHA-256 |
|---|---:|---|
| `UniversalSceneForge_0.9.9_Preview_Setup.exe` | 47,835,889 字节 | `5BD71224DC78A639A9D0D93C0BC7F6067FDC8D24C32124AD08D14CBB22017828` |
| `UniversalSceneForge_0.9.9_Preview_portable.zip` | 67,757,839 字节 | `0D4BF36462F6EAA5516E51EBAA8C9200FEEA048EE8545394CEC291AAF336220D` |

## 验证

- 真实手机视频（1280×720）全链：抽帧 146 帧 → COLMAP 注册 81 视角 →
  30000 轮 3DGS（PSNR 37.70）→ TSDF → 4096 烘焙 → 14 个导出文件，
  `FULL_EXIT=0`。
- 源相机视角渲染可见黑色鼠标、滚轮与编织垫纹理；对比图见
  `build/audit/quality_test/compare_source_vs_recon.png`。
- 单元/回归测试：13 个中 12 个通过；`unit_096_prep` 因 rembg 模型下载
  超时失败（网络问题，与本次改动无关）。
- 打包 EXE（Python 3.14 + Open3D 0.20）实测：`--cli --model` 注入同一
  3DGS 模型 → TSDF 回退 UniformTSDFVolume（502,047 面）→ 减面 150,000 →
  影像贴色 85.3% → 14 个导出文件，`EXIT=0`。
- 导出：FBX 30.9 MB（嵌纹理）、.blend 13.0 MB、GLB 52.1 MB、OBJ 22.6 MB、
  USD 42.0 MB。

## 已知限制

- **鼠标侧壁略扁**：TSDF 深度是 alpha 加权近似，融合后侧壁比原视频扁；
  这是深度网格路线的固有折中。
- 鼠标与垫子接触处的薄缝在极端视角可能仍有少量孔洞（已用 3.5× 截断 +
  2 次 Taubin 缓解）。
- 150k 面 + 4096 贴图使 FBX/GLB 体积明显增大；需要小体积请用 LOD1/LOD2
  或降低 `texture_size`。
- TSDF 需要用户本地 Inria 仓库与训练 venv；缺失时回退泊松重建。
- UASSET 需要 UE5 编辑器与自备授权。
- open3d 不随 EXE 打包，需依赖体检补装。
- 安装包未做 Authenticode 数字签名。

完整变更见 [CHANGELOG.md](CHANGELOG.md)，测试细节见
[BLENDER_PIPELINE_TEST.md](BLENDER_PIPELINE_TEST.md)，安全与合规见
[SECURITY_AUDIT.md](SECURITY_AUDIT.md) 与
[LICENSE_COMPLIANCE_CHANGES.md](LICENSE_COMPLIANCE_CHANGES.md)。
