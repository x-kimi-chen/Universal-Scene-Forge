"""Universal Scene Forge — 端到端场景重建与导出。

以 3D 高斯泼溅为重建内核，自动桥接 Blender / Metashape / Maya / UE5
完成网格后处理，最终导出 FBX / OBJ / GLTF-GLB / .blend / .uasset。
"""

__version__ = "1.0"
APP_NAME = "通用场景锻造系统"

# --------------------------------------------------------------------- #
# 开发方式声明（对应 AI_ASSISTANCE.md）
# 本项目在开发过程中使用了 AI 编程助手：代码补全、重构与测试草稿、文档草稿。
# 所有 AI 参与产出的代码均经人工审阅、修改、联调与回归验证后提交，最终由
# 著作权人承担全部责任。程序不含运行时调用 AI 服务的逻辑。
# --------------------------------------------------------------------- #
AI_ASSISTED_DEVELOPMENT = True
AI_ASSISTED_SCOPE = ("代码补全", "重构建议", "测试草稿", "文档草稿")
AI_RUNTIME_DEPENDENCY = False
