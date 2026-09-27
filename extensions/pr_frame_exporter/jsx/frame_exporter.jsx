// frame_exporter.jsx — USF Premiere Pro 抽帧导出（ExtendScript 端）
// 由 CEP 面板通过 evalScript 分块调用: 保持 PR 界面不长时间冻结。
// 通信协议: 参数以函数实参传入, 返回 "OK|..." 或 "ERR|消息"（ExtendScript
// 无原生 JSON, 不在 JSX 内解析任务文件; 面板侧用 window.cep.fs + JSON）。

var USF_TICKS_PER_SEC = 254016000000;   // PR 内部: 1 秒 = 254016000000 ticks
var USF_STATE = null;                    // 当前导出会话(帧游标等)

// ------------------------------------------------------------------ //
//  工具函数
// ------------------------------------------------------------------ //
function usfPad(n, width) {
    var s = n.toString();
    while (s.length < width) s = "0" + s;
    return s;
}

function usfNormPath(p) {
    // 统一为正斜杠 + 小写, 便于与 getMediaPath() 比较
    return String(p).replace(/\\/g, "/").toLowerCase();
}

// 绝对 ticks → "hh;mm;ss;ff"（与序列监视器显示一致, 含 zeroPoint 偏移）
function usfTimecode(ticks) {
    var frame = Math.round(ticks / USF_STATE.timebase);
    var fps = Math.round(USF_TICKS_PER_SEC / USF_STATE.timebase);
    if (fps < 1) fps = 25;
    var ff = frame % fps;
    var t = Math.floor(frame / fps);
    var ss = t % 60;
    var mm = Math.floor(t / 60) % 60;
    var hh = Math.floor(t / 3600);
    return usfPad(hh, 2) + ";" + usfPad(mm, 2) + ";" +
           usfPad(ss, 2) + ";" + usfPad(ff, 2);
}

// 深度遍历项目树查找素材（跳过 bin）
function usfFindItem(videoPath) {
    var target = usfNormPath(videoPath);
    var stack = [app.project.rootItem];
    while (stack.length > 0) {
        var node = stack.pop();
        for (var i = 0; i < node.children.numItems; i++) {
            var ch = node.children[i];
            if (ch.type === 4) {                     // ProjectItemType.CLIP
                try {
                    if (usfNormPath(ch.getMediaPath()) === target) return ch;
                } catch (e) {}
            } else if (ch.type === 2 && ch.children) {  // ProjectItemType.BIN
                stack.push(ch);
            }
        }
    }
    return null;
}

// ------------------------------------------------------------------ //
//  面板探活
// ------------------------------------------------------------------ //
function usfPing() {
    try {
        return "OK|" + app.version;
    } catch (e) {
        return "ERR|" + e.toString();
    }
}

// ------------------------------------------------------------------ //
//  ① 准备素材: 导入视频并创建序列（成为活动序列）
// ------------------------------------------------------------------ //
function usfPrepare(videoPath) {
    try {
        var path = String(videoPath).replace(/\\/g, "/");
        var item = usfFindItem(path);
        if (!item) {
            var ok = app.project.importFiles([path], true, null, 0);
            if (!ok) return "ERR|导入素材失败（文件不存在或格式不受支持）";
            item = usfFindItem(path);
        }
        if (!item) return "ERR|导入后未在项目面板找到素材";
        var seq = null;
        try {
            seq = item.createSequence("USF_" + item.name);  // 新序列自动激活
        } catch (e2) {
            seq = null;
        }
        if (!seq) return "ERR|创建序列失败（请手动将素材拖入时间线）";
        return "OK|" + seq.name;
    } catch (e) {
        return "ERR|" + e.toString();
    }
}

// ------------------------------------------------------------------ //
//  ② 开始: 依据活动序列计算采样帧位（返回总帧数与序列帧率）
//    rangeMode: "full" 全序列 / "inout" 入出点
// ------------------------------------------------------------------ //
function usfStart(outDir, fps, maxFrames, prefix, fmt, rangeMode) {
    try {
        var seq = app.project.activeSequence;
        if (!seq) return "ERR|没有活动序列（请先打开时间线）";
        var timebase = Number(seq.timebase);          // ticks / 帧
        if (!(timebase > 0)) return "ERR|序列 timebase 无效";

        var startT = seq.zeroPoint, endT = seq.end;
        if (rangeMode === "inout") {
            var ip = Number(seq.inPoint.ticks), op = Number(seq.outPoint.ticks);
            if (op > ip) { startT = seq.inPoint; endT = seq.outPoint; }
        }
        var startTicks = Number(startT.ticks);
        var endTicks = Number(endT.ticks);
        if (endTicks <= startTicks) return "ERR|序列区间为空";

        var step = Math.max(1, Math.round(USF_TICKS_PER_SEC / fps));
        var total = Math.floor((endTicks - startTicks) / step) + 1;
        if (maxFrames > 0 && total > maxFrames) total = maxFrames;

        USF_STATE = {
            seq: seq, timebase: timebase,
            startTicks: startTicks, step: step,
            total: total, nextIndex: 0, exported: 0,
            outDir: String(outDir).replace(/\\/g, "/"),
            prefix: String(prefix), fmt: String(fmt)
        };
        var seqFps = USF_TICKS_PER_SEC / timebase;
        return "OK|" + total + "|" + seqFps.toFixed(3) + "|" + timebase;
    } catch (e) {
        return "ERR|" + e.toString();
    }
}

// ------------------------------------------------------------------ //
//  ③ 分块导出: 每次最多 count 帧, 返回 "OK|已导出|总数|是否完成"
// ------------------------------------------------------------------ //
function usfChunk(count) {
    try {
        if (!USF_STATE) return "ERR|尚未开始（先调用 usfStart）";
        var st = USF_STATE;
        var folder = new Folder(st.outDir);
        if (!folder.exists) folder.create();

        var done = 0;
        while (st.nextIndex < st.total && done < count) {
            var ticks = st.startTicks + st.nextIndex * st.step;
            var tc = usfTimecode(ticks);
            var file = st.outDir + "/" + st.prefix +
                       usfPad(st.nextIndex, 5) + "." + st.fmt;
            var ret;
            if (st.fmt === "png") ret = st.seq.exportFramePNG(tc, file);
            else                  ret = st.seq.exportFrameJPEG(tc, file);
            if (!ret) return "ERR|帧导出失败 #" + st.nextIndex +
                            "（时间码 " + tc + "）";
            st.nextIndex++;
            st.exported++;
            done++;
        }
        var finished = (st.nextIndex >= st.total) ? 1 : 0;
        return "OK|" + st.exported + "|" + st.total + "|" + finished;
    } catch (e) {
        return "ERR|" + e.toString();
    }
}

// ------------------------------------------------------------------ //
//  取消: 清空会话（面板停止 pump 后调用）
// ------------------------------------------------------------------ //
function usfCancel() {
    USF_STATE = null;
    return "OK|";
}
