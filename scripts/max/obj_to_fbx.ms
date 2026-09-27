-- Universal Scene Forge — 3ds Max 无头桥接模板
-- 占位符由 MaxBridge(Python) 替换后落盘执行:
--   __USF_INPUT__   输入网格 (OBJ)
--   __USF_OUTPUT__  输出 FBX
--   __USF_RESULT__  协议结果文件（3dsmaxbatch 不回传 format 到 stdout）
-- 协议: [USF] PROGRESS <0-100> <msg> / [USF] FILE <path> / [USF] ERROR <msg> / [USF] DONE
(
    global usfResFile = createFile "__USF_RESULT__"

    fn usfWrite msg = format "%\n" msg to:usfResFile

    local objPath = "__USF_INPUT__"
    local fbxPath = "__USF_OUTPUT__"

    try (
        usfWrite "[USF] PROGRESS 10 importing OBJ"
        importFile objPath #noPrompt

        usfWrite "[USF] PROGRESS 40 exporting FBX"
        pluginManager.loadClass FBXEXP
        exportFile fbxPath #noPrompt using:FBXEXP

        usfWrite ("[USF] FILE " + fbxPath)
        usfWrite "[USF] DONE"
    )
    catch (
        usfWrite ("[USF] ERROR " + (getCurrentException()))
    )
    close usfResFile
)
