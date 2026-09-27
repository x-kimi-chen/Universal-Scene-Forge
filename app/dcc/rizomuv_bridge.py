# -*- coding: utf-8 -*-
"""RizomUV 无头桥接: 自动展 UV + 打包。

调用方式:
    rizomuv.exe -lua <生成的脚本.lua>
脚本由本桥接按次生成(输入/输出路径直接内联, 规避参数传递差异)。

注意: RizomUV 的 Lua API 各版本命名存在差异, 脚本对展 UV/打包方法
逐一 pcall 兼容; 未装 RizomUV 时该环节整体跳过, 不影响主流程。
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from app.utils.logger import get_logger

log = get_logger("RIZOMUV")

LogCb = Callable[[str], None]


class RizomuvBridgeError(RuntimeError):
    pass


_LUA_TEMPLATE = """\
-- Universal Scene Forge — RizomUV 自动展 UV (由桥接生成)
local IN  = [[{input}]]
local OUT = [[{output}]]

local function log(m) print("[RZV] " .. tostring(m)) end

local doc, err = rizom.load_document(IN)
if not doc then
    log("加载失败: " .. tostring(err))
    os.exit(1)
end
log("已加载: " .. IN)

-- 展 UV: 各版本方法名不同, 逐一兼容
local unwrapped = false
for _, m in ipairs({{"uv_unwrap", "unwrap", "auto_unwrap"}}) do
    local ok, e = pcall(function() doc[m](doc) end)
    if ok then
        log("展 UV 完成 via " .. m)
        unwrapped = true
        break
    else
        log("方法 " .. m .. " 不可用: " .. tostring(e))
    end
end
if not unwrapped then
    log("无可用展 UV 方法, 退出")
    os.exit(1)
end

-- 打包 UV 壳
for _, m in ipairs({{"pack", "uv_pack", "autofit"}}) do
    local ok = pcall(function() doc[m](doc) end)
    if ok then
        log("打包完成 via " .. m)
        break
    end
end

local saved = rizom.save_document(doc, OUT)
if not saved then
    log("保存失败: " .. OUT)
    os.exit(1)
end
log("已保存: " .. OUT)
print("[RZV] DONE")
"""


class RizomuvBridge:
    def __init__(self, exe: str, timeout_s: int = 1800,
                 log_cb: Optional[LogCb] = None,
                 stop_event: Optional[threading.Event] = None):
        self.exe = str(exe)
        self.timeout_s = timeout_s
        self.log_cb = log_cb
        self.stop_event = stop_event or threading.Event()

    # ================================================================== #
    def unwrap(self, mesh_in: Path, mesh_out: Path) -> Path:
        """加载网格 → 自动展 UV + 打包 → 保存新网格。返回输出路径。"""
        mesh_in, mesh_out = Path(mesh_in), Path(mesh_out)
        mesh_out.parent.mkdir(parents=True, exist_ok=True)

        lua = mesh_out.parent / f"usf_unwrap_{mesh_out.stem}.lua"
        lua.write_text(
            _LUA_TEMPLATE.format(input=mesh_in.as_posix(),
                                 output=mesh_out.as_posix()),
            encoding="utf-8")

        creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        cmd = [self.exe, "-lua", str(lua)]
        log.info("RizomUV 无头启动: %s → %s", mesh_in.name, mesh_out.name)

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=creation)
        lines = []
        deadline = time.time() + self.timeout_s
        try:
            for line in proc.stdout or []:
                text = line.rstrip()
                lines.append(text)
                if self.log_cb:
                    self.log_cb(text)
                if self.stop_event.is_set():
                    proc.kill()
                    raise RizomuvBridgeError("用户取消 RizomUV 展 UV")
                if time.time() > deadline:
                    proc.kill()
                    raise RizomuvBridgeError(
                        f"RizomUV 超时 ({self.timeout_s}s), 已终止")
            code = proc.wait(timeout=30)
        finally:
            if proc.poll() is None:
                proc.kill()
            lua.unlink(missing_ok=True)

        if code != 0 or not mesh_out.exists():
            tail = "\n".join(lines[-8:])
            raise RizomuvBridgeError(
                f"RizomUV 展 UV 失败 (退出码 {code})。"
                f"确认安装 RizomUV VS/RS 且版本支持 -lua。\n{tail}")
        log.info("RizomUV 完成: %s", mesh_out)
        return mesh_out
