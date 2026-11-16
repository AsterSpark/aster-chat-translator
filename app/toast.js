/* 独立提示条窗口：显示一句话，然后自己消失
   注意：Tauri 侧命令叫 close_self（之前写成 hide_self，导致窗口永远不消失）
*/

const pill = document.getElementById("pill");
const textEl = document.getElementById("toastText");
const prog = document.getElementById("prog");


let timer = null;

function hideSelf() {
  pill.hidden = true;
  pill.classList.remove("timed");
  invoke("close_self");
}

function show(text, ms, tone) {
  if (!text) {
    hideSelf();
    return;
  }
  textEl.textContent = text;
  pill.dataset.tone = tone || "";
  pill.hidden = false;
  invoke("show_toast");

  if (timer) { clearTimeout(timer); timer = null; }

  if (ms > 0) {
    pill.classList.add("timed");
    prog.style.animationDuration = Math.max(300, ms) + "ms";
    // 重启动画
    prog.style.animation = "none";
    void prog.offsetWidth;
    prog.style.animation = "";
    timer = setTimeout(() => { hideSelf(); timer = null; }, ms);
  } else {
    pill.classList.remove("timed");
  }
}

/* 窗口尺寸对齐 420×84（乘界面缩放，否则放大后文字会被裁） */
let uiScale = 1;

function fitToast() {
  if (!window.__TAURI_INTERNALS__) return;
  invoke("fit_window", {
    width: 420 * uiScale, height: 84 * uiScale,
    dpr: window.devicePixelRatio || 1,
  });
}

function applyScale(cfg) {
  const s = Math.min(1.5, Math.max(0.8, Number((cfg || {}).ui_scale) || 1));
  if (s === uiScale) return;
  uiScale = s;
  document.documentElement.style.zoom = s;
  fitToast();
}

fitToast();

// 给外壳留一个入口：后端挂了的时候没有 WS 可用，但窗口还在，
// Rust 侧可以直接 eval 这个函数把错误提示显示在这条提示条上 ——
// 这样所有提示都走同一套自绘 UI，不出现系统原生弹窗。
window.__showNotice = (text, ms, tone) => show(text, ms || 0, tone || "");

function connect() {
  const ws = new WebSocket(WS_URL);
  ws.onopen = () => {
    ws.send(JSON.stringify({ type: "toast_ready" }));
    ws.send(JSON.stringify({ type: "settings_get" }));
  };
  ws.onmessage = (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch (err) { return; }
    if (msg.type === "notice") show(msg.text, msg.ms || 0, msg.tone || "");
    if (msg.type === "settings") applyScale(msg.cfg);
  };
  ws.onclose = () => setTimeout(connect, 2000);
}

connect();
