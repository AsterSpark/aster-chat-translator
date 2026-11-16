/* 六个窗口共用的小工具：本地端点 / $ / invoke 包装
 *
 * 原来 API、WS_URL、$、invoke 在每个页面里各抄一份（端点字面量最多 10 处），
 * 改端口要满仓库改。这个文件必须在各页面自己的脚本之前加载。
 */
/* 端点：外壳里页面跑在 tauri.localhost，走固定端口；
   浏览器里调试时按当前地址推导（HTTP 端口 +1 就是 WS），只改 WOWTR_PORT 也能对上。 */
const _viaHttp = (location.hostname === "127.0.0.1" || location.hostname === "localhost");
const API = _viaHttp ? location.origin : "http://127.0.0.1:17650";
const WS_URL = _viaHttp
  ? `ws://${location.hostname}:${Number(location.port || 17650) + 1}`
  : "ws://127.0.0.1:17651";

const $ = (id) => document.getElementById(id);

/* 窗口高度最多占屏幕可用高的比例。
   原来散在三个地方写成 0.94 / 0.95 / 0.96，同一个界面三套上限，统一到这里。 */
const SCREEN_USE_RATIO = 0.94;

function invoke(cmd, args) {
  let p;
  try {
    if (window.__TAURI_INTERNALS__) {
      p = window.__TAURI_INTERNALS__.invoke(cmd, args || {});
    }
  } catch (e) { /* 浏览器里直接开页面时退化成 no-op */ }
  if (!p || !p.catch) return Promise.resolve(null);
  // 统一兜住失败：绝大多数调用是"发出去就不管结果"的（fit_window / show_popup …），
  // 不加这一步，命令报错只会变成 DevTools 里一条 unhandled rejection，用户毫无感知。
  // 需要感知错误的调用方（比如 set_autostart）直接用 __TAURI_INTERNALS__ 自己 try/catch。
  return p.catch((e) => {
    console.warn("[invoke]", cmd, e);
    return null;
  });
}
