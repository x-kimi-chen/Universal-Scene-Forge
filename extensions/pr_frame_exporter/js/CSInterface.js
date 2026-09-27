/* CSInterface.js — 轻量封装（仅本面板用到的能力）。
 * CEP 运行时向每个面板注入 window.__adobe_cep__, evalScript(script, cb)
 * 把 ExtendScript 结果以字符串回调。完整版 Adobe CSInterface 库约千行,
 * 本项目只用 evalScript, 故内置 30 行等价实现, 避免整库随包。 */
function CSInterface() {}

CSInterface.prototype.evalScript = function (script, callback) {
    callback = callback || function () {};
    if (window.__adobe_cep__ && window.__adobe_cep__.evalScript) {
        window.__adobe_cep__.evalScript(script, callback);
    } else {
        // 非 CEP 环境(浏览器直接打开 html 调试): 返回错误占位
        callback("ERR|面板未运行在 Premiere 中");
    }
};

CSInterface.prototype.getHostEnvironment = function () {
    try {
        return JSON.parse(window.__adobe_cep__.getHostEnvironment());
    } catch (e) {
        return null;
    }
};
