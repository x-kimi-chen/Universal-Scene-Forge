# Universal Scene Forge 0.9.8-preview

**预发布版本（Pre-release）**。主题：安全加固、开源合规、Blender 交付质量。

## 主要变化

### 安全

- CLI/GUI 统一输入/输出路径校验，拒绝盘符根、系统根、用户主目录根。
- 子进程环境隔离（`scrubbed_env`）与可执行文件解析，主链 `shell=True` 为 0。
- 下载链增加 zip-slip/符号链接防护、体积/超时上限、下载前许可证展示。
- 媒体解析增加尺寸/帧数/时长上限与解码异常兜底。
- 文件日志、CLI stdout、GUI 日志统一脱敏密钥与用户名绝对路径。

### 合规

- 移除 GPL-3.0+ `plyfile`，改用 MIT `app/utils/ply_io.py`。
- gaussian-splatting 商用阻断 + 不可跳过的非商业确认；仅子进程调用，不随包分发。
- FFmpeg 不打包；下载前要求确认 GPLv3 义务。
- RizomUV / Metashape / UE5 授权检测不到即跳过并记录原因。
- 构建断言禁止 `external/` 与 `plyfile` 进入安装包。

### Blender / 导出

- 修复空/全零 UV，自动重建 `UVMap`。
- 多 UV 层合并为单一 canonical `UVMap`。
- GLB 导出 `POSITION / NORMAL / TEXCOORD_0 / TANGENT`。
- 修复小网格 LOD 不减面；LOD0 不再重复；关闭 `.blend1` 备份。
- 碎片化网格智能连通块保留。
- 支持 USD/USDA/USDC 输入与输出。

## 下载资产

| 文件 | 大小 | SHA-256 |
|---|---:|---|
| `UniversalSceneForge_0.9.8_Preview_Setup.exe` | 47,797,190 字节 | `F814A7AF8BC8F3FC060A8B03266F4CA1B9D46E1FC5B4AE6E73B3F65A1F92434D` |
| `UniversalSceneForge_0.9.8_Preview_portable.zip` | 67,693,799 字节 | `7305C0C2CE6C30CEDF66B5A2FB0154FD8AF216BE6EB02DC9E384A73C9457F202` |

## 验证

- 14 个单元/回归测试全部通过；安全自测 17/17；PLY 测试通过。
- 源环境与 EXE 端到端均 `exit 0`，18 个产物，13/13 再导入校验通过。
- 3 个 LOD 的 GLB 均含 `TEXCOORD_0` 与 `TANGENT`。

## 已知限制

- UASSET 需要 UE5 编辑器与自备授权。
- 3DGS 训练环境不随包分发；本机未执行真实训练。
- Blender/FFmpeg/COLMAP 下载源尚未固定 SHA-256（当前告警不阻断）。
- open3d 不随 EXE 打包，需依赖体检补装。
- 安装包未做 Authenticode 数字签名。

完整变更见 [CHANGELOG.md](CHANGELOG.md)，安全与合规细节见
[SECURITY_AUDIT.md](SECURITY_AUDIT.md) 与
[LICENSE_COMPLIANCE_CHANGES.md](LICENSE_COMPLIANCE_CHANGES.md)。
